from __future__ import annotations

import re
import unicodedata

from app.models.cad import Axis, HoleType, Material, SideFace
from app.services.planners.base import CadPlanner
from app.services.planners.intent import (
    IntentCutout,
    IntentHole,
    IntentParameters,
    LayoutKind,
    LLMIntent,
    Point3,
    TemplateKind,
    intent_to_document,
)
from app.services.standards import metric_clearance_diameter, metric_tap_drill_diameter

_NUMBER = r"([0-9]+(?:\.[0-9]+)?)"
_SIGNED_NUMBER = r"(-?[0-9]+(?:\.[0-9]+)?)"
_UNIT = r"\s*(mm|毫米|cm|公分|厘米|m|公尺|in|inch|英吋|吋)?"

_CN_COUNT = {"一": 1, "二": 2, "兩": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_EN_COUNT = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return text.replace("，", ",").replace("：", ":").replace("×", "x").strip()


def _multiplier(unit: str | None) -> float:
    if not unit or unit in {"mm", "毫米"}:
        return 1.0
    if unit in {"cm", "公分", "厘米"}:
        return 10.0
    if unit in {"m", "公尺"}:
        return 1000.0
    if unit in {"in", "inch", "英吋", "吋"}:
        return 25.4
    return 1.0


def _labeled(text: str, labels: list[str]) -> float | None:
    joined = "|".join(re.escape(label) for label in labels)
    patterns = [
        rf"(?:{joined})\s*(?:為|是|=|:)?\s*{_NUMBER}{_UNIT}",
        rf"{_NUMBER}{_UNIT}\s*(?:的)?(?:{joined})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            groups = match.groups()
            value = float(groups[0])
            unit = groups[1] if len(groups) > 1 else None
            return value * _multiplier(unit)
    return None


def _xyz_triplet(text: str) -> tuple[float, float, float] | None:
    match = re.search(
        rf"{_NUMBER}{_UNIT}\s*[xX*]\s*{_NUMBER}{_UNIT}\s*[xX*]\s*{_NUMBER}{_UNIT}",
        text,
    )
    if not match:
        return None
    groups = match.groups()
    return (
        float(groups[0]) * _multiplier(groups[1]),
        float(groups[2]) * _multiplier(groups[3]),
        float(groups[4]) * _multiplier(groups[5]),
    )


def _template(text: str) -> TemplateKind:
    low = text.lower()
    if any(token in low for token in ["l bracket", "l-bracket", "l型", "l 型", "角碼", "直角支架"]):
        return TemplateKind.L_BRACKET
    if any(token in low for token in ["墊圈", "垫圈", "washer", "圓環", "圆环", "套筒", "ring"]):
        return TemplateKind.RING
    if any(token in low for token in ["外殼", "外壳", "盒", "箱", "enclosure", "case"]):
        return TemplateKind.ENCLOSURE
    if any(token in low for token in ["圓柱", "圆柱", "圓棒", "圆棒", "cylinder", "shaft"]):
        return TemplateKind.CYLINDER
    return TemplateKind.PLATE


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text, re.IGNORECASE) is not None


def _material(text: str) -> Material | None:
    low = text.lower()
    if any(token in low for token in ["鋁", "铝"]) or any(
        _has_word(low, token) for token in ["aluminum", "aluminium"]
    ):
        return Material.ALUMINUM
    if any(token in low for token in ["不鏽鋼", "不锈钢"]) or _has_word(low, "stainless"):
        return Material.STAINLESS_STEEL
    if any(token in low for token in ["鋼", "钢"]) or _has_word(low, "steel"):
        return Material.STEEL
    if any(token in low for token in ["塑膠", "塑料"]) or any(
        _has_word(low, token) for token in ["plastic", "abs", "pla", "petg"]
    ):
        return Material.PLASTIC
    if "木" in low or _has_word(low, "wood"):
        return Material.WOOD
    return None


def _count(text: str) -> int:
    patterns = [
        r"([0-9]+)\s*(?:個|个)(?=.{0,24}(?:孔|holes?))",
        r"\b([0-9]+)\s*(?:孔|holes?)\b",
        r"\b([0-9]+)\s+(?=M\s*[0-9]+(?:\.[0-9]+)?\s+holes?\b)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return max(1, min(int(match.group(1)), 64))
    for char, count in _CN_COUNT.items():
        if re.search(rf"{char}\s*(?:個|个)?(?=.{{0,24}}(?:孔|holes?))", text, re.IGNORECASE):
            return count
    for word, count in _EN_COUNT.items():
        if re.search(rf"\b{word}\b(?=.{{0,24}}\bholes?\b)", text, re.IGNORECASE):
            return count
    if any(token in text.lower() for token in ["四角", "4角", "four corners", "each corner"]):
        return 4
    if re.search(r"M\s*[0-9]+(?:\.[0-9]+)?", text, re.IGNORECASE) or "孔" in text or "hole" in text.lower():
        return 1
    return 0


def _hole_axis(text: str) -> Axis:
    low = text.lower()
    if any(token in low for token in ["x軸", "x轴", "x-axis", "x axis", "along x"]):
        return Axis.X
    if any(
        token in low
        for token in ["y軸", "y轴", "y-axis", "y axis", "along y", "立板", "vertical plate"]
    ):
        return Axis.Y
    return Axis.Z


def _first_measure(patterns: list[str], text: str) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1)) * _multiplier(match.group(2))
    return None


def _coordinate(text: str, axis: str) -> float | None:
    match = re.search(
        rf"(?:中心|center\s*)?{axis}\s*=\s*{_SIGNED_NUMBER}{_UNIT}",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return float(match.group(1)) * _multiplier(match.group(2))


def _hole_diameter(text: str) -> float | None:
    explicit = _labeled(text, ["孔徑", "孔径", "hole diameter"])
    if explicit is not None:
        return explicit

    hole_words = r"(?:通孔|盲孔|沉頭孔|沉头孔|沉孔|牙孔|螺紋孔|螺纹孔|孔|through\s+holes?|blind\s+holes?|counterbore(?:d)?\s+holes?|countersink(?:ed)?\s+holes?|holes?)"
    patterns = [
        rf"(?:直徑|直径|diameter|[Øφ])\s*(?:為|是|=|:)?\s*{_NUMBER}{_UNIT}(?=.{{0,24}}{hole_words})",
        rf"{_NUMBER}{_UNIT}\s*(?:直徑|直径|diameter)(?=.{{0,24}}{hole_words})",
        rf"{_NUMBER}{_UNIT}\s*(?:的)?(?:通孔|盲孔|沉頭孔|沉头孔|沉孔|牙孔|螺紋孔|螺纹孔|孔)",
        rf"{_NUMBER}{_UNIT}\s*(?:(?:through|blind|counterbore(?:d)?|countersink(?:ed)?)\s+)?holes?\b",
    ]
    return _first_measure(patterns, text)


def _blind_hole_depth(text: str) -> float | None:
    explicit = _labeled(text, ["孔深", "盲孔深", "盲孔深度", "blind hole depth"])
    if explicit is not None:
        return explicit
    patterns = [
        rf"(?:盲孔)\s*(?:深|深度)?\s*{_NUMBER}{_UNIT}",
        rf"(?:深|深度)\s*{_NUMBER}{_UNIT}(?=.{{0,16}}盲孔)",
        rf"(?:blind\s+holes?)\s*(?:to\s+)?{_NUMBER}{_UNIT}\s*(?:deep)?",
        rf"{_NUMBER}{_UNIT}\s+deep(?=.{{0,16}}blind\s+holes?|.{{0,16}}holes?)",
    ]
    return _first_measure(patterns, text)


def _rectangular_cutout(text: str, params: IntentParameters) -> IntentCutout | None:
    size_match = re.search(
        rf"{_NUMBER}{_UNIT}\s*[xX*]\s*{_NUMBER}{_UNIT}\s*"
        r"(?:的)?(?:矩形|長方形|长方形|rectangular)?\s*"
        r"(?:開口|开口|切口|窗口|cutout|opening)",
        text,
        re.IGNORECASE,
    )
    if not size_match:
        return None

    groups = size_match.groups()
    shared_unit = groups[1] or groups[3]
    width = float(groups[0]) * _multiplier(groups[1] or shared_unit)
    height = float(groups[2]) * _multiplier(groups[3] or shared_unit)
    low = text.lower()

    face = SideFace.POSITIVE_Y
    face_tokens = (
        (SideFace.POSITIVE_X, ["+x面", "正x面", "positive x", "right side", "右側", "右侧"]),
        (SideFace.NEGATIVE_X, ["-x面", "負x面", "负x面", "negative x", "left side", "左側", "左侧"]),
        (SideFace.POSITIVE_Y, ["+y面", "正y面", "positive y", "front side", "前側", "前侧"]),
        (SideFace.NEGATIVE_Y, ["-y面", "負y面", "负y面", "negative y", "back side", "後側", "后侧"]),
    )
    for candidate, tokens in face_tokens:
        if any(token in low for token in tokens):
            face = candidate
            break

    center_z = _coordinate(text, "z")
    return IntentCutout(
        face=face,
        x=_coordinate(text, "x") or 0,
        y=_coordinate(text, "y") or 0,
        z=center_z if center_z is not None else (params.height or 30) / 2,
        width=width,
        height=height,
    )


def _hole_group(text: str, params: IntentParameters) -> IntentHole | None:
    count = _count(text)
    if count <= 0:
        return None

    thread_match = re.search(r"M\s*([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    thread = f"M{thread_match.group(1)}" if thread_match else None
    nominal = float(thread_match.group(1)) if thread_match else None

    diameter_text = re.sub(r"(?<![A-Za-z0-9])M\s*[0-9]+(?:\.[0-9]+)?(?![A-Za-z0-9])", "", text, flags=re.IGNORECASE)
    diameter = _hole_diameter(diameter_text)
    tapped = any(token in text.lower() for token in ["攻牙", "牙孔", "螺紋", "螺纹", "tapped", "threaded"])
    if diameter is None and nominal is not None:
        diameter = (
            metric_tap_drill_diameter(nominal)[0]
            if tapped
            else metric_clearance_diameter(nominal)[0]
        )

    hole_type = HoleType.TAPPED if tapped else HoleType.CLEARANCE if thread else HoleType.THROUGH
    if any(token in text.lower() for token in ["盲孔", "blind"]):
        hole_type = HoleType.BLIND
    if any(token in text.lower() for token in ["沉孔", "counterbore", "counter-bore"]):
        hole_type = HoleType.COUNTERBORE
    if any(token in text.lower() for token in ["沉頭", "沉头", "countersink", "counter-sink"]):
        hole_type = HoleType.COUNTERSINK

    layout = LayoutKind.LINE_X
    corner_holes = re.search(
        r"(?:四角|4角|four corners|each corner).{0,24}(?:M\s*[0-9]+(?:\.[0-9]+)?|孔|holes?)",
        text,
        re.IGNORECASE,
    )
    if corner_holes:
        layout = LayoutKind.FOUR_CORNERS
        count = 4
    elif any(token in text.lower() for token in ["中心", "中間", "中央", "center", "centre"]):
        layout = LayoutKind.CENTER if count == 1 else LayoutKind.CENTERED_LINE_X

    positions: list[Point3] = []
    coord_pattern = re.compile(
        rf"x\s*=\s*{_NUMBER}{_UNIT}\s*[,;/ ]+\s*y\s*=\s*{_NUMBER}{_UNIT}",
        re.IGNORECASE,
    )
    for match in coord_pattern.finditer(text):
        groups = match.groups()
        positions.append(
            Point3(
                x=float(groups[0]) * _multiplier(groups[1]),
                y=float(groups[2]) * _multiplier(groups[3]),
                z=0,
            )
        )
    if positions:
        layout = LayoutKind.EXPLICIT
        count = len(positions)

    depth = _blind_hole_depth(text) if hole_type == HoleType.BLIND else _labeled(
        text, ["孔深", "hole depth"]
    )
    cbore_d = _labeled(text, ["沉孔徑", "沉孔径", "counterbore diameter"])
    cbore_depth = _labeled(text, ["沉孔深", "counterbore depth"])
    csk_d = _labeled(text, ["沉頭徑", "沉头径", "countersink diameter"])
    csk_angle = _labeled(text, ["沉頭角", "沉头角", "countersink angle"])

    if hole_type == HoleType.COUNTERBORE:
        diameter = diameter or nominal or 3.0
        cbore_d = cbore_d or diameter * 1.8
        cbore_depth = cbore_depth or diameter * 0.6
    if hole_type == HoleType.COUNTERSINK:
        diameter = diameter or nominal or 3.0
        csk_d = csk_d or diameter * 2.0
        csk_angle = csk_angle or 90.0

    return IntentHole(
        count=count,
        diameter=diameter or 3.0,
        thread=thread,
        hole_type=hole_type,
        layout=layout,
        positions=positions,
        axis=_hole_axis(text),
        depth=depth,
        counterbore_diameter=cbore_d,
        counterbore_depth=cbore_depth,
        countersink_diameter=csk_d,
        countersink_angle=csk_angle,
    )


def _fillet(text: str) -> float | None:
    patterns = [rf"(?:圓角|圆角|倒圓角|倒圆角|fillet)\s*(?:R)?\s*{_NUMBER}{_UNIT}", rf"\bR\s*{_NUMBER}{_UNIT}"]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1)) * _multiplier(match.group(2))
    return None


def _chamfer(text: str) -> float | None:
    patterns = [rf"(?:倒角|chamfer)\s*(?:C)?\s*{_NUMBER}{_UNIT}", rf"\bC\s*{_NUMBER}{_UNIT}"]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1)) * _multiplier(match.group(2))
    return None


class RuleBasedPlanner(CadPlanner):
    name = "rule"

    async def plan(self, prompt: str):
        text = _normalize(prompt)
        template = _template(text)
        triplet = _xyz_triplet(text)

        length = _labeled(text, ["長度", "长度", "長", "长", "length"])
        width = _labeled(text, ["寬度", "宽度", "寬", "宽", "width", "wide"])
        height = _labeled(text, ["高度", "高", "height", "tall"])
        thickness = _labeled(text, ["厚度", "壁厚", "厚", "thickness", "thick"])
        depth = _labeled(text, ["深度", "深", "depth", "deep"])
        diameter = _labeled(text, ["直徑", "直径", "diameter", "Ø", "φ"])
        outer_diameter = _labeled(text, ["外徑", "外径", "outer diameter", "od"])
        inner_diameter = _labeled(text, ["內徑", "内径", "inner diameter", "id"])
        vertical_height = _labeled(text, ["立板高", "垂直高度", "vertical height"])
        wall_thickness = _labeled(text, ["壁厚", "wall thickness"])
        edge_margin = _labeled(text, ["離邊緣", "离边缘", "邊距", "边距", "edge margin", "from edge"])

        if triplet:
            length = length or triplet[0]
            width = width or triplet[1]
            if template in {TemplateKind.PLATE, TemplateKind.RING}:
                thickness = thickness or triplet[2]
            else:
                height = height or triplet[2]

        params = IntentParameters(
            length=length,
            width=width,
            height=height,
            thickness=thickness,
            diameter=diameter,
            outer_diameter=outer_diameter,
            inner_diameter=inner_diameter,
            depth=depth,
            vertical_height=vertical_height,
            wall_thickness=wall_thickness,
            edge_margin=edge_margin,
        )
        holes = []
        group = _hole_group(text, params)
        if group:
            holes.append(group)

        assumptions: list[str] = []
        cutout = _rectangular_cutout(text, params)
        cutouts = [cutout] if cutout else []
        if cutout and _coordinate(text, "z") is None:
            assumptions.append("矩形開口未指定中心高度，暫置於外殼高度中央")
        if group and group.thread and group.hole_type == HoleType.CLEARANCE:
            assumptions.append(f"{group.thread} 未指定攻牙，依一般間隙孔近似")
        if template == TemplateKind.L_BRACKET and group and not any(
            token in text.lower()
            for token in ["底板", "base plate", "立板", "vertical plate", "x軸", "x轴", "y軸", "y轴", "z軸", "z轴"]
        ):
            assumptions.append("L 型支架未指定孔位於底板或立板，暫放在底板")
        confidence = 0.92
        if sum(value is not None for value in params.model_dump().values()) < 2:
            confidence = 0.68

        name_map = {
            TemplateKind.PLATE: "promptcad-plate",
            TemplateKind.CYLINDER: "promptcad-cylinder",
            TemplateKind.RING: "promptcad-ring",
            TemplateKind.L_BRACKET: "promptcad-l-bracket",
            TemplateKind.ENCLOSURE: "promptcad-enclosure",
        }
        intent = LLMIntent(
            name=name_map[template],
            template=template,
            unit="mm",
            material=_material(text),
            parameters=params,
            holes=holes,
            cutouts=cutouts,
            fillet_radius=_fillet(text),
            chamfer_distance=_chamfer(text),
            assumptions=assumptions,
            notes=[],
            confidence=confidence,
            review_required=confidence < 0.8,
        )
        return intent_to_document(intent, prompt, self.name)

from __future__ import annotations

import re

from app.models.cad import (
    Axis,
    CadDocument,
    HoleFeature,
    HoleType,
    LBracketBase,
    Material,
    PlannerMetadata,
    StandardReference,
)
from app.services.planners.base import CadPlanner, PlannerError
from app.services.standards_catalog import NEMA17_FACE


class StandardAwarePlanner(CadPlanner):
    name = "standard-agent"

    @staticmethod
    def can_handle(prompt: str) -> bool:
        return re.search(r"(?<![A-Za-z0-9])nema\s*-?\s*17(?![0-9])", prompt, re.IGNORECASE) is not None

    async def plan(self, prompt: str) -> CadDocument:
        if not self.can_handle(prompt):
            raise PlannerError("No supported standard component was identified")

        standard = NEMA17_FACE
        thickness = self._measure(prompt, ["板厚", "厚度", "thickness"]) or 3.0
        width = self._measure(prompt, ["支架寬", "支架宽", "bracket width"]) or 60.0
        depth = self._measure(prompt, ["底板深", "底座深", "base depth"]) or 50.0
        height = self._measure(prompt, ["立板高", "支架高", "bracket height"]) or 50.0
        center_z = thickness + height / 2
        half_pitch = standard.mounting_pitch / 2

        holes = [
            HoleFeature(
                x=x,
                z=center_z + z,
                axis=Axis.Y,
                diameter=standard.mounting_clearance,
                hole_type=HoleType.CLEARANCE,
                thread=standard.mounting_thread,
            )
            for x, z in (
                (-half_pitch, -half_pitch),
                (half_pitch, -half_pitch),
                (half_pitch, half_pitch),
                (-half_pitch, half_pitch),
            )
        ]
        holes.append(
            HoleFeature(
                x=0,
                z=center_z,
                axis=Axis.Y,
                diameter=standard.pilot_clearance,
                hole_type=HoleType.THROUGH,
            )
        )
        holes.extend(
            HoleFeature(
                x=x,
                y=y,
                axis=Axis.Z,
                diameter=4.5,
                hole_type=HoleType.CLEARANCE,
                thread="M4",
            )
            for x in (-22.0, 22.0)
            for y in (-15.0, 15.0)
        )

        return CadDocument(
            name="nema17-motor-bracket",
            source_prompt=prompt,
            material=Material.ALUMINUM,
            base=LBracketBase(
                width=width,
                depth=depth,
                vertical_height=height,
                thickness=thickness,
            ),
            holes=holes,
            standards=[
                StandardReference(
                    key=standard.key,
                    revision=standard.revision,
                    source_label=standard.source_label,
                    source_url=standard.source_url,
                ),
                StandardReference(
                    key="nema17-bracket-thickness",
                    revision="pololu-2266-2014",
                    source_label="Pololu stamped aluminum L-bracket for NEMA 17",
                    source_url="https://www.pololu.com/product/2266",
                ),
            ],
            assumptions=[
                "依 NEMA17 常見 31 mm 方形孔距與 M3 馬達面安裝孔建立介面。",
                "馬達定位凸台以 22.5 mm 中心通孔預留 0.5 mm 直徑間隙。",
                "3 mm 鋁板是常見商品支架厚度，不是 NEMA 強制標準；可在提示詞以板厚覆寫。",
                "底板加入四個 M4 一般間隙孔，位置為 ±22 x ±15 mm。",
            ],
            notes=[
                f"參考馬達面寬 {standard.face_size:g} mm、軸徑 {standard.shaft_diameter:g} mm。",
                "NEMA frame size 不保證所有供應商的軸長、凸台高度與螺紋深度相同，製作前核對實際料號。",
            ],
            planner=PlannerMetadata(
                planner=self.name,
                confidence=0.94,
                review_required=True,
            ),
        )

    @staticmethod
    def _measure(prompt: str, labels: list[str]) -> float | None:
        for label in labels:
            match = re.search(
                rf"{re.escape(label)}\s*(?:為|是|=|:)?\s*([0-9]+(?:\.[0-9]+)?)\s*(mm|毫米|cm|公分|in|inch|英吋|吋)?",
                prompt,
                re.IGNORECASE,
            )
            if match:
                multiplier = {
                    None: 1.0,
                    "mm": 1.0,
                    "毫米": 1.0,
                    "cm": 10.0,
                    "公分": 10.0,
                    "in": 25.4,
                    "inch": 25.4,
                    "英吋": 25.4,
                    "吋": 25.4,
                }[match.group(2).lower() if match.group(2) and match.group(2).isascii() else match.group(2)]
                return round(float(match.group(1)) * multiplier, 6)
        return None

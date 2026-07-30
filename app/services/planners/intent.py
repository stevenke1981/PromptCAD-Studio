from __future__ import annotations

import math
from enum import StrEnum

from pydantic import Field

from app.models.cad import (
    Axis,
    CadDocument,
    ChamferFeature,
    CylinderBase,
    EdgeSelector,
    EnclosureBase,
    FilletFeature,
    HoleFeature,
    HoleType,
    LBracketBase,
    Material,
    PlannerMetadata,
    PlateBase,
    RingBase,
    StrictModel,
)
from app.services.standards import (
    metric_clearance_diameter,
    metric_tap_drill_diameter,
    metric_thread_nominal,
)


class TemplateKind(StrEnum):
    PLATE = "plate"
    CYLINDER = "cylinder"
    RING = "ring"
    L_BRACKET = "l_bracket"
    ENCLOSURE = "enclosure"


class LayoutKind(StrEnum):
    CENTER = "center"
    LINE_X = "line_x"
    FOUR_CORNERS = "four_corners"
    EXPLICIT = "explicit"


class Point3(StrictModel):
    x: float
    y: float
    z: float


class IntentParameters(StrictModel):
    length: float | None
    width: float | None
    height: float | None
    thickness: float | None
    diameter: float | None
    outer_diameter: float | None
    inner_diameter: float | None
    depth: float | None
    vertical_height: float | None
    wall_thickness: float | None
    edge_margin: float | None


class IntentHole(StrictModel):
    count: int = Field(ge=0, le=64)
    diameter: float | None
    thread: str | None
    hole_type: HoleType
    layout: LayoutKind
    positions: list[Point3] = Field(max_length=64)
    axis: Axis
    depth: float | None
    counterbore_diameter: float | None
    counterbore_depth: float | None
    countersink_diameter: float | None
    countersink_angle: float | None


class LLMIntent(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    template: TemplateKind
    unit: str
    material: Material | None
    parameters: IntentParameters
    holes: list[IntentHole] = Field(max_length=16)
    fillet_radius: float | None
    chamfer_distance: float | None
    assumptions: list[str] = Field(max_length=32)
    notes: list[str] = Field(max_length=32)
    confidence: float = Field(ge=0, le=1)
    review_required: bool


def _value(value: float | None, default: float, label: str, assumptions: list[str]) -> float:
    if value is None:
        assumptions.append(f"未提供{label}，暫用 {default:g} mm")
        return default
    return value


def _base_from_intent(intent: LLMIntent, assumptions: list[str]):
    p = intent.parameters
    if intent.template == TemplateKind.PLATE:
        return PlateBase(
            length=_value(p.length, 100, "長度", assumptions),
            width=_value(p.width, 60, "寬度", assumptions),
            thickness=_value(p.thickness or p.height, 5, "厚度", assumptions),
        )
    if intent.template == TemplateKind.CYLINDER:
        return CylinderBase(
            diameter=_value(p.diameter or p.outer_diameter, 20, "直徑", assumptions),
            height=_value(p.height or p.length, 40, "高度", assumptions),
        )
    if intent.template == TemplateKind.RING:
        outer = _value(p.outer_diameter, 30, "外徑", assumptions)
        inner = _value(p.inner_diameter, outer / 2, "內徑", assumptions)
        if inner >= outer:
            assumptions.append("內徑大於或等於外徑，已修正為外徑的一半")
            inner = outer / 2
        return RingBase(
            outer_diameter=outer,
            inner_diameter=inner,
            height=_value(p.thickness or p.height, 5, "厚度", assumptions),
        )
    if intent.template == TemplateKind.L_BRACKET:
        return LBracketBase(
            width=_value(p.width or p.length, 60, "支架寬度", assumptions),
            depth=_value(p.depth, 40, "底板深度", assumptions),
            vertical_height=_value(p.vertical_height or p.height, 50, "立板高度", assumptions),
            thickness=_value(p.thickness, 4, "板厚", assumptions),
        )
    return EnclosureBase(
        length=_value(p.length, 100, "外殼長度", assumptions),
        width=_value(p.width, 70, "外殼寬度", assumptions),
        height=_value(p.height, 30, "外殼高度", assumptions),
        wall_thickness=_value(p.wall_thickness or p.thickness, 2, "壁厚", assumptions),
    )


def _axis_plane(base, axis: Axis) -> tuple[float, float, float, float]:
    """Return local plane spans and centers for default hole placement."""
    if isinstance(base, PlateBase):
        x_span, y_span, z_span = base.length, base.width, base.thickness
    elif isinstance(base, EnclosureBase):
        x_span, y_span, z_span = base.length, base.width, base.height
    elif isinstance(base, LBracketBase):
        x_span, y_span = base.width, base.depth
        z_span = base.vertical_height + base.thickness
    elif isinstance(base, CylinderBase):
        x_span = y_span = base.diameter
        z_span = base.height
    elif isinstance(base, RingBase):
        x_span = y_span = base.outer_diameter
        z_span = base.height
    else:
        x_span = y_span = z_span = 50.0

    if axis == Axis.Z:
        return x_span, y_span, 0.0, 0.0
    if axis == Axis.X:
        return y_span, z_span, 0.0, z_span / 2
    return x_span, z_span, 0.0, z_span / 2


def _material_depth(base, axis: Axis) -> float:
    if isinstance(base, PlateBase):
        return {Axis.X: base.length, Axis.Y: base.width, Axis.Z: base.thickness}[axis]
    if isinstance(base, EnclosureBase):
        return base.wall_thickness
    if isinstance(base, LBracketBase):
        if axis == Axis.X:
            return base.width
        return base.thickness
    if isinstance(base, CylinderBase):
        return base.height if axis == Axis.Z else base.diameter
    if isinstance(base, RingBase):
        return base.height if axis == Axis.Z else base.outer_diameter
    return 10.0


def _point_on_axis_plane(axis: Axis, first: float, second: float) -> Point3:
    if axis == Axis.Z:
        return Point3(x=first, y=second, z=0)
    if axis == Axis.X:
        return Point3(x=0, y=first, z=second)
    return Point3(x=first, y=0, z=second)


def _default_positions(
    base,
    count: int,
    layout: LayoutKind,
    margin: float,
    axis: Axis,
) -> list[Point3]:
    if count <= 0:
        return []

    span_1, span_2, center_1, center_2 = _axis_plane(base, axis)
    max_margin = max(min(span_1, span_2) / 2 - 0.5, 0.0)
    safe_margin = min(max(margin, 0.0), max_margin)
    half_1 = max(span_1 / 2 - safe_margin, 0.0)
    half_2 = max(span_2 / 2 - safe_margin, 0.0)

    if layout == LayoutKind.CENTER and count == 1:
        return [_point_on_axis_plane(axis, center_1, center_2)]
    if layout == LayoutKind.FOUR_CORNERS:
        corners = [
            (center_1 - half_1, center_2 - half_2),
            (center_1 + half_1, center_2 - half_2),
            (center_1 + half_1, center_2 + half_2),
            (center_1 - half_1, center_2 + half_2),
        ]
        return [_point_on_axis_plane(axis, first, second) for first, second in corners[:count]]

    if count == 1:
        return [_point_on_axis_plane(axis, center_1, center_2)]
    return [
        _point_on_axis_plane(
            axis,
            center_1 - half_1 + (2 * half_1 * index / (count - 1)),
            center_2,
        )
        for index in range(count)
    ]


def intent_to_document(intent: LLMIntent, prompt: str, planner_name: str) -> CadDocument:
    assumptions = list(intent.assumptions)
    base = _base_from_intent(intent, assumptions)
    margin = intent.parameters.edge_margin or 10.0
    holes: list[HoleFeature] = []

    for group in intent.holes:
        positions = group.positions
        if group.layout != LayoutKind.EXPLICIT or not positions:
            positions = _default_positions(base, group.count, group.layout, margin, group.axis)
        if not positions and group.count:
            positions = _default_positions(base, group.count, LayoutKind.LINE_X, margin, group.axis)

        diameter = group.diameter
        nominal = metric_thread_nominal(group.thread)
        if diameter is None and nominal is not None:
            if group.hole_type == HoleType.TAPPED:
                diameter, standard = metric_tap_drill_diameter(nominal)
                label = "攻牙底孔"
            elif group.hole_type == HoleType.CLEARANCE:
                diameter, standard = metric_clearance_diameter(nominal)
                label = "一般間隙孔"
            else:
                diameter, standard = nominal, True
                label = "名目孔徑"
            qualifier = "表列值" if standard else "比例近似值"
            assumptions.append(
                f"{group.thread} 未提供實際孔徑，暫用{label} {diameter:g} mm（{qualifier}）"
            )
        diameter = diameter or 3.0

        material_depth = _material_depth(base, group.axis)
        depth = group.depth
        counterbore_diameter = group.counterbore_diameter
        counterbore_depth = group.counterbore_depth
        countersink_diameter = group.countersink_diameter
        countersink_angle = group.countersink_angle

        if group.hole_type == HoleType.BLIND and depth is None:
            depth = min(max(diameter * 1.5, 1.0), material_depth * 0.75)
            assumptions.append(f"盲孔未提供深度，暫用 {depth:g} mm")
        if group.hole_type == HoleType.COUNTERBORE:
            if counterbore_diameter is None:
                counterbore_diameter = diameter * 1.8
                assumptions.append(f"沉孔未提供外徑，暫用 {counterbore_diameter:g} mm")
            if counterbore_depth is None:
                counterbore_depth = min(diameter * 0.6, material_depth * 0.5)
                assumptions.append(f"沉孔未提供深度，暫用 {counterbore_depth:g} mm")
        if group.hole_type == HoleType.COUNTERSINK:
            if countersink_diameter is None:
                countersink_diameter = diameter * 2.0
                assumptions.append(f"沉頭孔未提供外徑，暫用 {countersink_diameter:g} mm")
            if countersink_angle is None:
                countersink_angle = 90.0
                assumptions.append("沉頭孔未提供角度，暫用 90°")

        for point in positions[: group.count]:
            holes.append(
                HoleFeature(
                    x=point.x,
                    y=point.y,
                    z=point.z,
                    axis=group.axis,
                    diameter=diameter,
                    hole_type=group.hole_type,
                    depth=depth,
                    thread=group.thread,
                    counterbore_diameter=counterbore_diameter,
                    counterbore_depth=counterbore_depth,
                    countersink_diameter=countersink_diameter,
                    countersink_angle=countersink_angle,
                )
            )

    fillets = []
    if intent.fillet_radius and math.isfinite(intent.fillet_radius):
        fillets.append(FilletFeature(radius=intent.fillet_radius, selector=EdgeSelector.VERTICAL))
    chamfers = []
    if intent.chamfer_distance and math.isfinite(intent.chamfer_distance):
        chamfers.append(ChamferFeature(distance=intent.chamfer_distance, selector=EdgeSelector.VERTICAL))

    review = intent.review_required or bool(assumptions) or intent.confidence < 0.8
    return CadDocument(
        name=intent.name,
        source_prompt=prompt,
        material=intent.material,
        base=base,
        holes=holes,
        fillets=fillets,
        chamfers=chamfers,
        assumptions=assumptions,
        notes=intent.notes,
        planner=PlannerMetadata(
            planner=planner_name,
            confidence=intent.confidence,
            review_required=review,
        ),
    )

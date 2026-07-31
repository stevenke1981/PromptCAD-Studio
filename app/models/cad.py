from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_DIMENSION_MM = 100_000.0


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Material(StrEnum):
    ALUMINUM = "aluminum"
    STEEL = "steel"
    STAINLESS_STEEL = "stainless_steel"
    PLASTIC = "plastic"
    WOOD = "wood"
    OTHER = "other"


class PlateBase(StrictModel):
    kind: Literal["plate"] = "plate"
    length: float = Field(gt=0, le=MAX_DIMENSION_MM)
    width: float = Field(gt=0, le=MAX_DIMENSION_MM)
    thickness: float = Field(gt=0, le=MAX_DIMENSION_MM)


class CylinderBase(StrictModel):
    kind: Literal["cylinder"] = "cylinder"
    diameter: float = Field(gt=0, le=MAX_DIMENSION_MM)
    height: float = Field(gt=0, le=MAX_DIMENSION_MM)


class RingBase(StrictModel):
    kind: Literal["ring"] = "ring"
    outer_diameter: float = Field(gt=0, le=MAX_DIMENSION_MM)
    inner_diameter: float = Field(gt=0, le=MAX_DIMENSION_MM)
    height: float = Field(gt=0, le=MAX_DIMENSION_MM)

    @model_validator(mode="after")
    def inner_must_be_smaller(self) -> RingBase:
        if self.inner_diameter >= self.outer_diameter:
            raise ValueError("inner_diameter must be smaller than outer_diameter")
        return self


class LBracketBase(StrictModel):
    kind: Literal["l_bracket"] = "l_bracket"
    width: float = Field(gt=0, le=MAX_DIMENSION_MM)
    depth: float = Field(gt=0, le=MAX_DIMENSION_MM)
    vertical_height: float = Field(gt=0, le=MAX_DIMENSION_MM)
    thickness: float = Field(gt=0, le=MAX_DIMENSION_MM)

    @model_validator(mode="after")
    def thickness_must_fit(self) -> LBracketBase:
        if self.thickness >= min(self.depth, self.vertical_height):
            raise ValueError("thickness must be smaller than depth and vertical_height")
        return self


class EnclosureBase(StrictModel):
    kind: Literal["enclosure"] = "enclosure"
    length: float = Field(gt=0, le=MAX_DIMENSION_MM)
    width: float = Field(gt=0, le=MAX_DIMENSION_MM)
    height: float = Field(gt=0, le=MAX_DIMENSION_MM)
    wall_thickness: float = Field(gt=0, le=MAX_DIMENSION_MM)

    @model_validator(mode="after")
    def wall_must_fit(self) -> EnclosureBase:
        if self.wall_thickness * 2 >= min(self.length, self.width):
            raise ValueError("wall_thickness is too large for enclosure length/width")
        if self.wall_thickness >= self.height:
            raise ValueError("wall_thickness must be smaller than enclosure height")
        return self


class Point2D(StrictModel):
    x: float = Field(ge=-MAX_DIMENSION_MM, le=MAX_DIMENSION_MM)
    y: float = Field(ge=-MAX_DIMENSION_MM, le=MAX_DIMENSION_MM)


class LineSegment2D(StrictModel):
    kind: Literal["line"] = "line"
    start: Point2D
    end: Point2D


class ArcSegment2D(StrictModel):
    """A circular arc defined by its start, a point on the arc, and its end."""

    kind: Literal["arc"] = "arc"
    start: Point2D
    mid: Point2D
    end: Point2D


ProfileSegment2D = Annotated[
    LineSegment2D | ArcSegment2D,
    Field(discriminator="kind"),
]


class ProfileLoop2D(StrictModel):
    segments: list[ProfileSegment2D] = Field(min_length=1, max_length=512)


class ProfileExtrusionBase(StrictModel):
    kind: Literal["profile_extrusion"] = "profile_extrusion"
    outer: ProfileLoop2D
    thickness: float = Field(gt=0, le=MAX_DIMENSION_MM)


class ProfileRevolutionBase(StrictModel):
    """A closed radius/Z profile revolved 360 degrees around global Z."""

    kind: Literal["profile_revolution"] = "profile_revolution"
    outer: ProfileLoop2D


BaseFeature = Annotated[
    PlateBase
    | CylinderBase
    | RingBase
    | LBracketBase
    | EnclosureBase
    | ProfileExtrusionBase
    | ProfileRevolutionBase,
    Field(discriminator="kind"),
]


class HoleType(StrEnum):
    THROUGH = "through"
    BLIND = "blind"
    CLEARANCE = "clearance"
    TAPPED = "tapped"
    COUNTERBORE = "counterbore"
    COUNTERSINK = "countersink"


class Axis(StrEnum):
    X = "x"
    Y = "y"
    Z = "z"


class SideFace(StrEnum):
    POSITIVE_X = "positive_x"
    NEGATIVE_X = "negative_x"
    POSITIVE_Y = "positive_y"
    NEGATIVE_Y = "negative_y"


class HoleFeature(StrictModel):
    kind: Literal["hole"] = "hole"
    x: float = Field(default=0, ge=-MAX_DIMENSION_MM, le=MAX_DIMENSION_MM)
    y: float = Field(default=0, ge=-MAX_DIMENSION_MM, le=MAX_DIMENSION_MM)
    z: float = Field(default=0, ge=-MAX_DIMENSION_MM, le=MAX_DIMENSION_MM)
    axis: Axis = Axis.Z
    diameter: float = Field(gt=0, le=MAX_DIMENSION_MM)
    hole_type: HoleType = HoleType.THROUGH
    depth: float | None = Field(default=None, gt=0, le=MAX_DIMENSION_MM)
    thread: str | None = Field(default=None, max_length=32)
    counterbore_diameter: float | None = Field(default=None, gt=0, le=MAX_DIMENSION_MM)
    counterbore_depth: float | None = Field(default=None, gt=0, le=MAX_DIMENSION_MM)
    countersink_diameter: float | None = Field(default=None, gt=0, le=MAX_DIMENSION_MM)
    countersink_angle: float | None = Field(default=None, gt=0, lt=180)

    @model_validator(mode="after")
    def validate_subtype(self) -> HoleFeature:
        if self.hole_type == HoleType.BLIND and self.depth is None:
            raise ValueError("blind hole requires depth")
        if self.hole_type == HoleType.COUNTERBORE:
            if self.counterbore_diameter is None or self.counterbore_depth is None:
                raise ValueError("counterbore requires counterbore_diameter and counterbore_depth")
            if self.counterbore_diameter <= self.diameter:
                raise ValueError("counterbore_diameter must exceed hole diameter")
        if self.hole_type == HoleType.COUNTERSINK:
            if self.countersink_diameter is None or self.countersink_angle is None:
                raise ValueError("countersink requires countersink_diameter and countersink_angle")
            if self.countersink_diameter <= self.diameter:
                raise ValueError("countersink_diameter must exceed hole diameter")
        return self


class RectangularCutoutFeature(StrictModel):
    kind: Literal["rectangular_cutout"] = "rectangular_cutout"
    face: SideFace
    x: float = Field(default=0, ge=-MAX_DIMENSION_MM, le=MAX_DIMENSION_MM)
    y: float = Field(default=0, ge=-MAX_DIMENSION_MM, le=MAX_DIMENSION_MM)
    z: float = Field(default=0, ge=-MAX_DIMENSION_MM, le=MAX_DIMENSION_MM)
    width: float = Field(gt=0, le=MAX_DIMENSION_MM)
    height: float = Field(gt=0, le=MAX_DIMENSION_MM)


class EdgeSelector(StrEnum):
    ALL = "all"
    VERTICAL = "vertical"
    TOP = "top"
    BOTTOM = "bottom"


class FilletFeature(StrictModel):
    kind: Literal["fillet"] = "fillet"
    radius: float = Field(gt=0, le=MAX_DIMENSION_MM)
    selector: EdgeSelector = EdgeSelector.VERTICAL


class ChamferFeature(StrictModel):
    kind: Literal["chamfer"] = "chamfer"
    distance: float = Field(gt=0, le=MAX_DIMENSION_MM)
    selector: EdgeSelector = EdgeSelector.VERTICAL


class PlannerMetadata(StrictModel):
    planner: str = Field(min_length=1, max_length=64)
    confidence: float = Field(default=1.0, ge=0, le=1)
    review_required: bool = False


class StandardReference(StrictModel):
    key: str = Field(min_length=1, max_length=80)
    revision: str = Field(min_length=1, max_length=40)
    source_label: str = Field(min_length=1, max_length=160)
    source_url: str = Field(pattern=r"^https://", max_length=500)


class CadDocument(StrictModel):
    schema_version: Literal["1.0", "1.1", "1.2"] = "1.0"
    name: str = Field(default="promptcad-part", min_length=1, max_length=80)
    source_prompt: str = Field(min_length=1, max_length=20_000)
    unit: Literal["mm"] = "mm"
    material: Material | None = None
    base: BaseFeature
    holes: list[HoleFeature] = Field(default_factory=list, max_length=64)
    cutouts: list[RectangularCutoutFeature] = Field(default_factory=list, max_length=32)
    fillets: list[FilletFeature] = Field(default_factory=list, max_length=8)
    chamfers: list[ChamferFeature] = Field(default_factory=list, max_length=8)
    standards: list[StandardReference] = Field(default_factory=list, max_length=16)
    assumptions: list[str] = Field(default_factory=list, max_length=32)
    notes: list[str] = Field(default_factory=list, max_length=32)
    planner: PlannerMetadata

    @model_validator(mode="after")
    def validate_schema_feature_contract(self) -> CadDocument:
        if (
            isinstance(self.base, ProfileExtrusionBase)
            and self.schema_version not in {"1.1", "1.2"}
        ):
            raise ValueError("profile_extrusion requires schema_version 1.1 or newer")
        if isinstance(self.base, ProfileRevolutionBase):
            if self.schema_version != "1.2":
                raise ValueError("profile_revolution requires schema_version 1.2")
            if self.holes or self.cutouts:
                raise ValueError(
                    "profile_revolution does not support holes or cutouts in schema 1.2"
                )
            if self.fillets or self.chamfers:
                raise ValueError(
                    "profile_revolution does not support top-level fillets or chamfers; "
                    "author corner geometry in the revolution profile"
                )
        return self


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationIssue(StrictModel):
    severity: ValidationSeverity
    code: str
    message: str
    feature_index: int | None = None


class ValidationReport(StrictModel):
    valid: bool
    review_required: bool
    issues: list[ValidationIssue] = Field(default_factory=list)

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


BaseFeature = Annotated[
    PlateBase | CylinderBase | RingBase | LBracketBase | EnclosureBase,
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


class CadDocument(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    name: str = Field(default="promptcad-part", min_length=1, max_length=80)
    source_prompt: str = Field(min_length=1, max_length=20_000)
    unit: Literal["mm"] = "mm"
    material: Material | None = None
    base: BaseFeature
    holes: list[HoleFeature] = Field(default_factory=list, max_length=64)
    fillets: list[FilletFeature] = Field(default_factory=list, max_length=8)
    chamfers: list[ChamferFeature] = Field(default_factory=list, max_length=8)
    assumptions: list[str] = Field(default_factory=list, max_length=32)
    notes: list[str] = Field(default_factory=list, max_length=32)
    planner: PlannerMetadata


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

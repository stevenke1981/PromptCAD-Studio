from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.models.cad import MAX_DIMENSION_MM, StrictModel

IDENTIFIER_PATTERN = r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$"


class ProjectionMethod(StrEnum):
    FIRST_ANGLE = "first_angle"
    THIRD_ANGLE = "third_angle"


class ReviewStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewAction(StrEnum):
    SUBMIT = "submit"
    APPROVE = "approve"
    REJECT = "reject"


class TitleBlock(StrictModel):
    part_name: str = Field(min_length=1, max_length=120)
    part_number: str = Field(min_length=1, max_length=80)
    drawing_number: str = Field(min_length=1, max_length=80)
    revision: str = Field(min_length=1, max_length=16)
    sheet_number: int = Field(default=1, ge=1, le=99)
    sheet_count: int = Field(default=1, ge=1, le=99)
    scale: str = Field(default="NTS", min_length=1, max_length=20)
    projection: ProjectionMethod = ProjectionMethod.THIRD_ANGLE
    unit: Literal["mm"] = "mm"
    drawn_by: str = Field(min_length=1, max_length=80)
    drawn_on: date
    checked_by: str | None = Field(default=None, min_length=1, max_length=80)
    approved_by: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_sheet(self) -> TitleBlock:
        if self.sheet_number > self.sheet_count:
            raise ValueError("sheet_number cannot exceed sheet_count")
        return self


class GeneralTolerance(StrictModel):
    kind: Literal["unspecified", "bilateral"] = "unspecified"
    linear_mm: float | None = Field(default=None, gt=0, le=MAX_DIMENSION_MM)
    angular_deg: float | None = Field(default=None, gt=0, lt=180)
    note: str = Field(default="Engineering review required before manufacturing", max_length=240)

    @model_validator(mode="after")
    def validate_values(self) -> GeneralTolerance:
        values = (self.linear_mm, self.angular_deg)
        if self.kind == "unspecified" and any(value is not None for value in values):
            raise ValueError("unspecified general tolerance cannot contain numeric values")
        if self.kind == "bilateral" and any(value is None for value in values):
            raise ValueError("bilateral general tolerance requires linear_mm and angular_deg")
        return self


class ReferenceTolerance(StrictModel):
    kind: Literal["reference"] = "reference"


class BasicTolerance(StrictModel):
    kind: Literal["basic"] = "basic"


class SymmetricTolerance(StrictModel):
    kind: Literal["symmetric"] = "symmetric"
    plus_minus_mm: float = Field(gt=0, le=MAX_DIMENSION_MM)


class DeviationTolerance(StrictModel):
    kind: Literal["deviation"] = "deviation"
    lower_mm: float = Field(ge=-MAX_DIMENSION_MM, le=0)
    upper_mm: float = Field(ge=0, le=MAX_DIMENSION_MM)

    @model_validator(mode="after")
    def validate_range(self) -> DeviationTolerance:
        if self.lower_mm == self.upper_mm:
            raise ValueError("deviation tolerance must have a non-zero range")
        return self


DimensionTolerance = Annotated[
    ReferenceTolerance | BasicTolerance | SymmetricTolerance | DeviationTolerance,
    Field(discriminator="kind"),
]


class BaseMeasurement(StrEnum):
    OVERALL_X = "overall_x"
    OVERALL_Y = "overall_y"
    OVERALL_Z = "overall_z"
    INNER_DIAMETER = "inner_diameter"
    WALL_THICKNESS = "wall_thickness"


class HoleMeasurement(StrEnum):
    DIAMETER = "diameter"
    X = "x"
    Y = "y"
    Z = "z"
    DEPTH = "depth"
    COUNTERBORE_DIAMETER = "counterbore_diameter"
    COUNTERBORE_DEPTH = "counterbore_depth"
    COUNTERSINK_DIAMETER = "countersink_diameter"
    COUNTERSINK_ANGLE = "countersink_angle"


class CutoutMeasurement(StrEnum):
    X = "x"
    Y = "y"
    Z = "z"
    WIDTH = "width"
    HEIGHT = "height"


class BaseDimensionTarget(StrictModel):
    kind: Literal["base"] = "base"
    measurement: BaseMeasurement


class HoleDimensionTarget(StrictModel):
    kind: Literal["hole"] = "hole"
    index: int = Field(ge=0, lt=64)
    measurement: HoleMeasurement


class CutoutDimensionTarget(StrictModel):
    kind: Literal["cutout"] = "cutout"
    index: int = Field(ge=0, lt=32)
    measurement: CutoutMeasurement


class FilletDimensionTarget(StrictModel):
    kind: Literal["fillet"] = "fillet"
    index: int = Field(ge=0, lt=8)
    measurement: Literal["radius"] = "radius"


class ChamferDimensionTarget(StrictModel):
    kind: Literal["chamfer"] = "chamfer"
    index: int = Field(ge=0, lt=8)
    measurement: Literal["distance"] = "distance"


DimensionTarget = Annotated[
    BaseDimensionTarget
    | HoleDimensionTarget
    | CutoutDimensionTarget
    | FilletDimensionTarget
    | ChamferDimensionTarget,
    Field(discriminator="kind"),
]


class DrawingDimension(StrictModel):
    id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=64)
    target: DimensionTarget
    tolerance: DimensionTolerance = Field(default_factory=ReferenceTolerance)
    datum_references: list[str] = Field(default_factory=list, max_length=3)
    label: str | None = Field(default=None, min_length=1, max_length=80)
    note: str = Field(default="", max_length=240)
    critical: bool = False

    @model_validator(mode="after")
    def unique_datum_references(self) -> DrawingDimension:
        if len(self.datum_references) != len(set(self.datum_references)):
            raise ValueError("datum_references must be unique")
        return self


class DatumFace(StrEnum):
    TOP = "top"
    BOTTOM = "bottom"
    POSITIVE_X = "positive_x"
    NEGATIVE_X = "negative_x"
    POSITIVE_Y = "positive_y"
    NEGATIVE_Y = "negative_y"


class BaseFaceDatumTarget(StrictModel):
    kind: Literal["base_face"] = "base_face"
    face: DatumFace


class HoleAxisDatumTarget(StrictModel):
    kind: Literal["hole_axis"] = "hole_axis"
    hole_index: int = Field(ge=0, lt=64)


DatumTarget = Annotated[
    BaseFaceDatumTarget | HoleAxisDatumTarget,
    Field(discriminator="kind"),
]


class DatumDefinition(StrictModel):
    id: str = Field(pattern=r"^[A-Z]{1,3}$", max_length=3)
    target: DatumTarget
    description: str = Field(default="", max_length=240)


class BaseFaceSurfaceTarget(StrictModel):
    kind: Literal["base_face"] = "base_face"
    face: DatumFace


class HoleWallSurfaceTarget(StrictModel):
    kind: Literal["hole_wall"] = "hole_wall"
    hole_index: int = Field(ge=0, lt=64)


class CutoutWallSurfaceTarget(StrictModel):
    kind: Literal["cutout_wall"] = "cutout_wall"
    cutout_index: int = Field(ge=0, lt=32)


SurfaceTarget = Annotated[
    BaseFaceSurfaceTarget | HoleWallSurfaceTarget | CutoutWallSurfaceTarget,
    Field(discriminator="kind"),
]


class SurfaceFinishRequirement(StrictModel):
    id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=64)
    target: SurfaceTarget
    ra_micrometers: float = Field(gt=0, le=1000)
    process: str | None = Field(default=None, min_length=1, max_length=80)
    datum_reference: str | None = Field(default=None, pattern=r"^[A-Z]{1,3}$")
    note: str = Field(default="", max_length=240)


class ProcurementType(StrEnum):
    MAKE = "make"
    BUY = "buy"


class BomItem(StrictModel):
    id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=64)
    item_number: int = Field(ge=1, le=9999)
    part_number: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=160)
    quantity: int = Field(ge=1, le=1_000_000)
    procurement: ProcurementType
    material: str | None = Field(default=None, min_length=1, max_length=80)
    note: str = Field(default="", max_length=240)


class RevisionEntry(StrictModel):
    revision: str = Field(min_length=1, max_length=16)
    occurred_on: date
    description: str = Field(min_length=1, max_length=240)
    author: str = Field(min_length=1, max_length=80)
    review_record_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)


class ReviewRecord(StrictModel):
    id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=64)
    version: int = Field(ge=1)
    action: ReviewAction
    from_status: ReviewStatus
    to_status: ReviewStatus
    reviewer: str = Field(min_length=1, max_length=80)
    occurred_at: datetime
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_transition(self) -> ReviewRecord:
        allowed = {
            ReviewAction.SUBMIT: (ReviewStatus.DRAFT, ReviewStatus.IN_REVIEW),
            ReviewAction.APPROVE: (ReviewStatus.IN_REVIEW, ReviewStatus.APPROVED),
            ReviewAction.REJECT: (ReviewStatus.IN_REVIEW, ReviewStatus.REJECTED),
        }
        if (self.from_status, self.to_status) != allowed[self.action]:
            raise ValueError("review action does not match from_status/to_status")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return self


class ReviewTransitionRequest(StrictModel):
    action: ReviewAction
    expected_version: int = Field(ge=0)
    reviewer: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=1000)
    record_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    occurred_at: datetime | None = None

    @model_validator(mode="after")
    def validate_timestamp(self) -> ReviewTransitionRequest:
        if self.occurred_at is not None and (
            self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None
        ):
            raise ValueError("occurred_at must be timezone-aware")
        return self


class ManufacturingDrawingSpec(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    cad_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    title_block: TitleBlock
    general_tolerance: GeneralTolerance = Field(default_factory=GeneralTolerance)
    dimensions: list[DrawingDimension] = Field(default_factory=list, max_length=12)
    datums: list[DatumDefinition] = Field(default_factory=list, max_length=3)
    surface_finishes: list[SurfaceFinishRequirement] = Field(default_factory=list, max_length=4)
    bom: list[BomItem] = Field(default_factory=list, max_length=8)
    revisions: list[RevisionEntry] = Field(min_length=1, max_length=8)
    review_status: ReviewStatus = ReviewStatus.DRAFT
    review_version: int = Field(default=0, ge=0)
    review_records: list[ReviewRecord] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_references_and_history(self) -> ManufacturingDrawingSpec:
        collections = {
            "dimension": [item.id for item in self.dimensions],
            "datum": [item.id for item in self.datums],
            "surface finish": [item.id for item in self.surface_finishes],
            "BOM": [item.id for item in self.bom],
            "review record": [item.id for item in self.review_records],
        }
        for label, identifiers in collections.items():
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{label} ids must be unique")
        item_numbers = [item.item_number for item in self.bom]
        if len(item_numbers) != len(set(item_numbers)):
            raise ValueError("BOM item_numbers must be unique")
        revisions = [entry.revision for entry in self.revisions]
        if len(revisions) != len(set(revisions)):
            raise ValueError("revision identifiers must be unique")
        if self.title_block.revision != self.revisions[-1].revision:
            raise ValueError("title block revision must match latest revision entry")

        datum_ids = {datum.id for datum in self.datums}
        for dimension in self.dimensions:
            if missing := set(dimension.datum_references) - datum_ids:
                raise ValueError(f"dimension references unknown datums: {sorted(missing)}")
        for finish in self.surface_finishes:
            if finish.datum_reference and finish.datum_reference not in datum_ids:
                raise ValueError("surface finish references an unknown datum")

        record_ids = {record.id for record in self.review_records}
        for revision in self.revisions:
            if revision.review_record_id and revision.review_record_id not in record_ids:
                raise ValueError("revision references an unknown review record")

        if self.review_version != len(self.review_records):
            raise ValueError("review_version must equal the number of review records")
        status = ReviewStatus.DRAFT
        previous_time: datetime | None = None
        for expected_version, record in enumerate(self.review_records, start=1):
            if record.version != expected_version:
                raise ValueError("review record versions must be contiguous")
            if record.from_status != status:
                raise ValueError("review record status chain is discontinuous")
            if previous_time is not None and record.occurred_at < previous_time:
                raise ValueError("review records must be chronological")
            status = record.to_status
            previous_time = record.occurred_at
        if self.review_status != status:
            raise ValueError("review_status must match the review record chain")
        if self.review_status == ReviewStatus.APPROVED and not self.title_block.approved_by:
            raise ValueError("approved drawing requires title_block.approved_by")
        return self


class ResolvedDimension(StrictModel):
    id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=64)
    target: DimensionTarget
    nominal: float = Field(ge=-MAX_DIMENSION_MM, le=MAX_DIMENSION_MM)
    unit: Literal["mm", "deg"] = "mm"
    tolerance: DimensionTolerance
    datum_references: list[str] = Field(default_factory=list, max_length=3)
    label: str | None = Field(default=None, min_length=1, max_length=80)
    note: str = Field(default="", max_length=240)
    critical: bool = False

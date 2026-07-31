from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.models.cad import CadDocument, Point2D, ProfileLoop2D, StrictModel, ValidationReport


class DxfEntityCounts(StrictModel):
    lines: int = Field(default=0, ge=0)
    arcs: int = Field(default=0, ge=0)
    circles: int = Field(default=0, ge=0)
    lwpolylines: int = Field(default=0, ge=0)
    polylines: int = Field(default=0, ge=0)
    ignored_annotations: int = Field(default=0, ge=0)


class DxfSymmetry(StrictModel):
    axes: list[Literal["horizontal", "vertical", "rotational_180"]] = Field(
        default_factory=list, max_length=3
    )
    tolerance_mm: float = Field(gt=0, le=10)


class DxfProvenance(StrictModel):
    dxf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_geometry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dxf_format: Literal["ASCII", "BINARY"]
    byte_length: int = Field(gt=0)
    dxf_version: str = Field(min_length=1, max_length=32)
    parser_name: Literal["ezdxf"] = "ezdxf"
    parser_version: str = Field(min_length=1, max_length=32)
    entity_total: int = Field(ge=0)
    insunits: int = Field(ge=0, le=24)
    source_unit: Literal["mm", "inch", "cm"]
    unit_scale_to_mm: float = Field(gt=0)


class DxfCircleHole(StrictModel):
    id: str = Field(pattern=r"^circle-[0-9]{2}$")
    center: Point2D
    radius_mm: float = Field(gt=0)


class DxfFeatureTreeNode(StrictModel):
    """A deliberately small, editable representation of a restricted DXF drawing."""

    id: str = Field(min_length=1, max_length=64)
    operation: Literal["profile_loop", "circle_hole", "extrude_profile"]
    parent_id: str | None = Field(default=None, max_length=64)
    loop: ProfileLoop2D | None = None
    center: Point2D | None = None
    radius_mm: float | None = Field(default=None, gt=0)
    thickness_mm: float | None = Field(default=None, gt=0)
    confidence: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_operation_fields(self) -> DxfFeatureTreeNode:
        expected = {
            "profile_loop": (self.loop is not None, self.center is None, self.radius_mm is None, self.thickness_mm is None),
            "circle_hole": (self.loop is None, self.center is not None, self.radius_mm is not None, self.thickness_mm is None),
            "extrude_profile": (self.loop is None, self.center is None, self.radius_mm is None, self.thickness_mm is not None),
        }[self.operation]
        if not all(expected):
            raise ValueError(f"{self.operation} has invalid editable fields")
        return self


class DxfAnalysisResponse(StrictModel):
    analysis_version: Literal["1.0"] = "1.0"
    provenance: DxfProvenance
    entity_counts: DxfEntityCounts
    outer_profile: ProfileLoop2D
    holes: list[DxfCircleHole] = Field(default_factory=list, max_length=256)
    feature_tree: list[DxfFeatureTreeNode] = Field(default_factory=list, max_length=260)
    symmetry: DxfSymmetry
    analysis_token: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    review_required: bool = True
    convertible: bool
    warnings: list[str] = Field(default_factory=list, max_length=32)
    proposed_spec: CadDocument | None = None
    validation: ValidationReport | None = None
    preview_svg: str | None = Field(default=None, max_length=200_000)

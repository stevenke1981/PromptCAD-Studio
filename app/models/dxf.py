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
    centerlines: int = Field(default=0, ge=0)
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


class DxfRevolutionAxis(StrictModel):
    orientation: Literal["horizontal", "vertical"]
    offset_mm: float
    start: Point2D
    end: Point2D
    source: Literal["layer", "linetype"]


class DxfHolePattern(StrictModel):
    id: str = Field(pattern=r"^pattern-[0-9]{2}$")
    kind: Literal["linear", "circular"]
    member_ids: list[str] = Field(min_length=3, max_length=64)
    hole_radius_mm: float = Field(gt=0)
    count: int = Field(ge=3, le=64)
    seed_center: Point2D
    direction: Literal["x", "y"] | None = None
    spacing_mm: float | None = Field(default=None, gt=0)
    pattern_center: Point2D | None = None
    pattern_radius_mm: float | None = Field(default=None, gt=0)
    start_angle_deg: float | None = Field(default=None, ge=-360, le=360)
    angular_spacing_deg: float | None = Field(default=None, gt=0, le=360)
    confidence: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_pattern_fields(self) -> DxfHolePattern:
        if self.count != len(self.member_ids) or len(set(self.member_ids)) != self.count:
            raise ValueError("pattern count must match unique member_ids")
        if self.kind == "linear":
            if self.direction is None or self.spacing_mm is None:
                raise ValueError("linear pattern requires direction and spacing_mm")
            if any(
                value is not None
                for value in (
                    self.pattern_center,
                    self.pattern_radius_mm,
                    self.start_angle_deg,
                    self.angular_spacing_deg,
                )
            ):
                raise ValueError("linear pattern contains circular-only fields")
        else:
            if any(
                value is None
                for value in (
                    self.pattern_center,
                    self.pattern_radius_mm,
                    self.start_angle_deg,
                    self.angular_spacing_deg,
                )
            ):
                raise ValueError("circular pattern requires center, radius, and angular fields")
            if self.direction is not None or self.spacing_mm is not None:
                raise ValueError("circular pattern contains linear-only fields")
        return self


class DxfEdgeTreatment(StrictModel):
    kind: Literal["fillet", "chamfer"]
    segment_indices: list[int] = Field(min_length=1, max_length=64)
    size_mm: float = Field(gt=0)
    confidence: float = Field(default=1.0, ge=0, le=1)


class DxfFeatureTreeNode(StrictModel):
    """A deliberately small, editable representation of a restricted DXF drawing."""

    id: str = Field(min_length=1, max_length=64)
    operation: Literal[
        "profile_loop",
        "circle_hole",
        "hole_pattern",
        "fillet_edges",
        "chamfer_edges",
        "extrude_profile",
        "revolve_profile",
    ]
    parent_id: str | None = Field(default=None, max_length=64)
    loop: ProfileLoop2D | None = None
    center: Point2D | None = None
    radius_mm: float | None = Field(default=None, gt=0)
    thickness_mm: float | None = Field(default=None, gt=0)
    pattern: DxfHolePattern | None = None
    edge_treatment: DxfEdgeTreatment | None = None
    revolution_axis: DxfRevolutionAxis | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_operation_fields(self) -> DxfFeatureTreeNode:
        expected = {
            "profile_loop": (
                self.loop is not None,
                self.center is None,
                self.radius_mm is None,
                self.thickness_mm is None,
                self.pattern is None,
                self.edge_treatment is None,
                self.revolution_axis is None,
            ),
            "circle_hole": (
                self.loop is None,
                self.center is not None,
                self.radius_mm is not None,
                self.thickness_mm is None,
                self.pattern is None,
                self.edge_treatment is None,
                self.revolution_axis is None,
            ),
            "hole_pattern": (
                self.loop is None,
                self.center is None,
                self.radius_mm is None,
                self.thickness_mm is None,
                self.pattern is not None,
                self.edge_treatment is None,
                self.revolution_axis is None,
            ),
            "fillet_edges": (
                self.loop is None,
                self.center is None,
                self.radius_mm is None,
                self.thickness_mm is None,
                self.pattern is None,
                self.edge_treatment is not None and self.edge_treatment.kind == "fillet",
                self.revolution_axis is None,
            ),
            "chamfer_edges": (
                self.loop is None,
                self.center is None,
                self.radius_mm is None,
                self.thickness_mm is None,
                self.pattern is None,
                self.edge_treatment is not None and self.edge_treatment.kind == "chamfer",
                self.revolution_axis is None,
            ),
            "extrude_profile": (
                self.loop is None,
                self.center is None,
                self.radius_mm is None,
                self.thickness_mm is not None,
                self.pattern is None,
                self.edge_treatment is None,
                self.revolution_axis is None,
            ),
            "revolve_profile": (
                self.loop is None,
                self.center is None,
                self.radius_mm is None,
                self.thickness_mm is None,
                self.pattern is None,
                self.edge_treatment is None,
                self.revolution_axis is not None,
            ),
        }[self.operation]
        if not all(expected):
            raise ValueError(f"{self.operation} has invalid editable fields")
        return self


class DxfAnalysisResponse(StrictModel):
    analysis_version: Literal["1.0", "1.1"] = "1.1"
    provenance: DxfProvenance
    entity_counts: DxfEntityCounts
    outer_profile: ProfileLoop2D
    holes: list[DxfCircleHole] = Field(default_factory=list, max_length=64)
    patterns: list[DxfHolePattern] = Field(default_factory=list, max_length=21)
    edge_treatments: list[DxfEdgeTreatment] = Field(default_factory=list, max_length=64)
    inferred_operation: Literal["extrude", "revolve"] = "extrude"
    revolution_axis: DxfRevolutionAxis | None = None
    feature_tree: list[DxfFeatureTreeNode] = Field(default_factory=list, max_length=68)
    symmetry: DxfSymmetry
    analysis_token: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    review_required: bool = True
    convertible: bool
    warnings: list[str] = Field(default_factory=list, max_length=32)
    proposed_spec: CadDocument | None = None
    validation: ValidationReport | None = None
    preview_svg: str | None = Field(default=None, max_length=200_000)

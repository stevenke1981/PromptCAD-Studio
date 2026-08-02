from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.models.cad import CadDocument, StrictModel, ValidationReport

ContentProfile = Literal["auto", "photo", "sketch", "whiteboard", "patent", "scan"]


class PixelPoint(StrictModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)


class MetricPoint(StrictModel):
    x: float
    y: float


class ImageCalibration(StrictModel):
    mode: Literal["known_long_edge"] = "known_long_edge"
    known_distance_mm: float = Field(gt=0)
    point_a_px: PixelPoint
    point_b_px: PixelPoint
    pixel_distance: float = Field(gt=0)
    mm_per_pixel: float = Field(gt=0)


class DetectedOuterProfile(StrictModel):
    shape: Literal["rectangle", "profile", "unsupported"]
    length_mm: float | None = Field(default=None, gt=0)
    width_mm: float | None = Field(default=None, gt=0)
    points_mm: list[MetricPoint] = Field(default_factory=list, max_length=128)
    rotation_deg: float
    rectangularity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    perspective_corrected: bool = False


class DetectedCircle(StrictModel):
    id: str = Field(pattern=r"^circle-[0-9]{2}$")
    center_px: PixelPoint
    center_mm: MetricPoint
    diameter_mm: float = Field(gt=0)
    diameter_min_mm: float = Field(gt=0)
    diameter_max_mm: float = Field(gt=0)
    circularity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    extraction_method: Literal["contour_void", "line_art_candidate"]
    accepted_for_cad: bool


class DetectedObjectCandidate(StrictModel):
    index: int = Field(ge=0, le=31)
    bounds_px: list[int] = Field(min_length=4, max_length=4)
    area_ratio: float = Field(gt=0, le=1)
    confidence: float = Field(ge=0, le=1)


FeatureOperation = Literal[
    "sketch_rectangle",
    "sketch_profile",
    "sketch_circle",
    "extrude",
    "cut_through",
]


class FeatureTreeNode(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    operation: FeatureOperation
    parent_id: str | None = Field(default=None, max_length=64)
    parameters: dict[str, float] = Field(default_factory=dict, max_length=12)
    points: list[MetricPoint] = Field(default_factory=list, max_length=128)
    confidence: float = Field(ge=0, le=1)


class ImageAnalysisResponse(StrictModel):
    analysis_version: Literal["1.0", "1.1"] = "1.1"
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    analysis_token: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    image_format: Literal["PNG", "JPEG", "PDF"]
    content_profile: ContentProfile = "auto"
    source_kind: Literal["image", "pdf"] = "image"
    source_page_index: int | None = Field(default=None, ge=0, le=99)
    source_page_count: int | None = Field(default=None, ge=1, le=100)
    source_image_width_px: int | None = Field(default=None, gt=0)
    source_image_height_px: int | None = Field(default=None, gt=0)
    image_width_px: int = Field(gt=0)
    image_height_px: int = Field(gt=0)
    calibration: ImageCalibration
    object_candidates: list[DetectedObjectCandidate] = Field(
        default_factory=list,
        max_length=32,
    )
    selected_object_index: int | None = Field(default=None, ge=0, le=31)
    ambiguous_objects: bool = False
    outer_profile: DetectedOuterProfile
    circles: list[DetectedCircle] = Field(default_factory=list, max_length=64)
    feature_tree: list[FeatureTreeNode] = Field(default_factory=list, max_length=130)
    review_required: bool = True
    convertible: bool
    warnings: list[str] = Field(default_factory=list, max_length=32)
    proposed_spec: CadDocument | None = None
    validation: ValidationReport | None = None
    preview_svg: str | None = Field(default=None, max_length=200_000)

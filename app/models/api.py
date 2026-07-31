from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.cad import CadDocument, ValidationReport
from app.models.dxf import DxfAnalysisResponse, DxfFeatureTreeNode
from app.models.image import FeatureTreeNode, ImageAnalysisResponse

OutputFormat = Literal["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"]
PlannerChoice = Literal["auto", "agent", "rule", "llm"]


def default_formats() -> list[OutputFormat]:
    return ["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"]


class StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanRequest(StrictApiModel):
    prompt: str = Field(min_length=3, max_length=20_000)
    planner: PlannerChoice = "auto"


class GenerateRequest(PlanRequest):
    formats: list[OutputFormat] = Field(
        default_factory=default_formats,
        min_length=1,
        max_length=8,
    )
    render: bool = True


class GenerateFromSpecRequest(StrictApiModel):
    spec: CadDocument
    formats: list[OutputFormat] = Field(
        default_factory=default_formats,
        min_length=1,
        max_length=8,
    )
    render: bool = True


class FeatureTreeToSpecRequest(StrictApiModel):
    analysis: ImageAnalysisResponse
    feature_tree: list[FeatureTreeNode] = Field(min_length=2, max_length=130)


class GenerateFromImageFeatureTreeRequest(StrictApiModel):
    analysis: ImageAnalysisResponse
    feature_tree: list[FeatureTreeNode] = Field(min_length=2, max_length=130)
    formats: list[OutputFormat] = Field(
        default_factory=default_formats,
        min_length=1,
        max_length=8,
    )
    render: bool = True


class DxfFeatureTreeToSpecRequest(StrictApiModel):
    analysis: DxfAnalysisResponse
    feature_tree: list[DxfFeatureTreeNode] = Field(min_length=2, max_length=260)


class GenerateFromDxfFeatureTreeRequest(DxfFeatureTreeToSpecRequest):
    formats: list[OutputFormat] = Field(
        default_factory=default_formats,
        min_length=1,
        max_length=8,
    )
    render: bool = True


class PlanResponse(StrictApiModel):
    spec: CadDocument
    validation: ValidationReport
    planner_used: str


class Artifact(StrictApiModel):
    filename: str
    media_type: str
    size: int
    url: str


class JobManifest(StrictApiModel):
    job_id: str
    status: Literal["completed", "source_only", "failed"]
    created_at: str
    prompt: str
    planner_used: str
    renderer_used: str
    requested_formats: list[OutputFormat]
    spec: CadDocument
    validation: ValidationReport
    artifacts: list[Artifact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class JobListItem(StrictApiModel):
    job_id: str
    status: str
    created_at: str
    prompt: str
    name: str
    renderer_used: str


class CapabilityResponse(StrictApiModel):
    planners: list[str]
    base_features: list[str]
    feature_types: list[str]
    hole_types: list[str]
    formats: list[str]
    cadquery_available: bool
    openscad_available: bool
    image_analysis_available: bool = True
    image_formats: list[str] = Field(default_factory=lambda: ["png", "jpeg"])
    dxf_analysis_available: bool = True
    dxf_entities: list[str] = Field(
        default_factory=lambda: ["LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE"]
    )
    dxf_units: list[str] = Field(default_factory=lambda: ["auto", "mm", "inch", "cm"])
    configured_planner_mode: str
    configured_render_backend: str

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.cad import CadDocument, ValidationReport
from app.models.dxf import DxfAnalysisResponse, DxfFeatureTreeNode
from app.models.image import FeatureTreeNode, ImageAnalysisResponse

OutputFormat = Literal["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"]
PlannerChoice = Literal["auto", "agent", "rule", "llm"]
QueueJobKind = Literal["prompt", "spec"]
QueueJobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
BackendChoice = Literal[
    "auto",
    "cadquery",
    "build123d",
    "freecad",
    "openscad",
    "fusion360",
    "solidworks",
    "source_only",
]


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
    backend: BackendChoice = "auto"


class GenerateFromSpecRequest(StrictApiModel):
    spec: CadDocument
    formats: list[OutputFormat] = Field(
        default_factory=default_formats,
        min_length=1,
        max_length=8,
    )
    render: bool = True
    backend: BackendChoice = "auto"


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
    backend: BackendChoice = "auto"


class DxfFeatureTreeToSpecRequest(StrictApiModel):
    analysis: DxfAnalysisResponse
    feature_tree: list[DxfFeatureTreeNode] = Field(min_length=2, max_length=66)


class GenerateFromDxfFeatureTreeRequest(DxfFeatureTreeToSpecRequest):
    formats: list[OutputFormat] = Field(
        default_factory=default_formats,
        min_length=1,
        max_length=8,
    )
    render: bool = True
    backend: BackendChoice = "auto"


class PlanResponse(StrictApiModel):
    spec: CadDocument
    validation: ValidationReport
    planner_used: str


class Artifact(StrictApiModel):
    filename: str
    media_type: str
    size: int
    url: str
    sha256: str = ""


class BackendDiagnostic(StrictApiModel):
    backend_id: str
    severity: Literal["info", "warning", "error"]
    code: str
    message: str


class BackendCapability(StrictApiModel):
    backend_id: str
    display_name: str
    compiler_version: str
    contract_version: str
    execution_kind: Literal["local_process", "host_application", "none"]
    source_export_available: bool
    local_execution_supported: bool
    runtime_available: bool
    schema_versions: list[str]
    base_features: list[str]
    feature_types: list[str]
    export_formats: list[str]
    server_render_formats: list[str]
    source_filenames: list[str]
    semantic_fidelity: Literal["exact", "approximated", "neutral_step_bridge"]
    unavailable_reason: str | None = None


class PlannerCapability(StrictApiModel):
    planner_id: str
    version: str
    available: bool
    input_kind: Literal["prompt", "standard_prompt"]
    description: str


class FormatResult(StrictApiModel):
    format: OutputFormat
    status: Literal["produced", "unavailable", "failed", "source_only", "cancelled"]
    filename: str | None = None
    reason: str | None = None


class JobManifest(StrictApiModel):
    job_id: str
    status: Literal["completed", "source_only", "failed", "cancelled"]
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
    backend_requested: BackendChoice = "auto"
    backend_used: str = "legacy"
    backend_contract_version: str = "1.0"
    source_backends: list[str] = Field(default_factory=list)
    backend_diagnostics: list[BackendDiagnostic] = Field(default_factory=list)
    format_results: list[FormatResult] = Field(default_factory=list)
    fallback_chain: list[str] = Field(default_factory=list)
    spec_sha256: str = ""
    validation_version: str = "1"
    completed_at: datetime | None = None


class JobListItem(StrictApiModel):
    job_id: str
    status: str
    created_at: str
    prompt: str
    name: str
    renderer_used: str


class QueueJobResponse(StrictApiModel):
    queue_job_id: str
    kind: QueueJobKind
    status: QueueJobStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempts: int = 0
    cancellation_requested: bool = False
    result_job_id: str | None = None
    result_url: str | None = None
    error: str | None = None


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
    backends: list[BackendCapability] = Field(default_factory=list)
    planner_capabilities: list[PlannerCapability] = Field(default_factory=list)
    async_queue_available: bool = True
    async_job_kinds: list[QueueJobKind] = Field(default_factory=lambda: ["prompt", "spec"])

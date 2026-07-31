from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.core.security import require_api_token, safe_job_file, validate_job_id
from app.models.api import (
    CapabilityResponse,
    DxfFeatureTreeToSpecRequest,
    FeatureTreeToSpecRequest,
    GenerateFromDxfFeatureTreeRequest,
    GenerateFromImageFeatureTreeRequest,
    GenerateFromSpecRequest,
    GenerateRequest,
    JobListItem,
    JobManifest,
    ManufacturingReviewResponse,
    ManufacturingTemplateRequest,
    PlanRequest,
    PlanResponse,
    QueueJobResponse,
)
from app.models.cad import CadDocument, ValidationReport
from app.models.dxf import DxfAnalysisResponse
from app.models.image import ImageAnalysisResponse
from app.models.manufacturing import (
    ManufacturingDrawingSpec,
    ReviewTransitionRequest,
)
from app.services.async_queue import QueueFullError, QueueJobNotFound

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_token)])


def _service(request: Request):
    return request.app.state.jobs


def _queue(request: Request):
    return request.app.state.async_queue


def _queue_error(exc: Exception) -> HTTPException:
    if isinstance(exc, QueueJobNotFound):
        return HTTPException(status_code=404, detail="Async job not found")
    if isinstance(exc, QueueFullError):
        return HTTPException(status_code=429, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/health")
async def health(request: Request):
    return {"status": "ok", "version": request.app.version}


@router.get("/capabilities", response_model=CapabilityResponse)
async def capabilities(request: Request):
    service = _service(request)
    settings = request.app.state.settings
    return CapabilityResponse(
        planners=["auto", "agent", "rule", "llm"],
        schema_versions=["1.0", "1.1", "1.2"],
        base_features=[
            "plate",
            "cylinder",
            "ring",
            "l_bracket",
            "enclosure",
            "profile_extrusion",
            "profile_revolution",
        ],
        feature_types=["hole", "rectangular_cutout", "fillet", "chamfer"],
        hole_types=["through", "blind", "clearance", "tapped", "counterbore", "countersink"],
        formats=["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
        cadquery_available=service.renderer.cadquery_available(),
        openscad_available=service.renderer.openscad_available(),
        image_analysis_available=True,
        image_formats=["png", "jpeg", "pdf"],
        dxf_analysis_available=True,
        dxf_entities=["LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE"],
        dxf_units=["auto", "mm", "inch", "cm"],
        dxf_operations=["auto", "extrude", "revolve"],
        configured_planner_mode=settings.planner_mode,
        configured_render_backend=settings.render_backend,
        backends=service.backends.capabilities(),
        planner_capabilities=service.planners.capabilities(),
    )


@router.post("/plan", response_model=PlanResponse)
async def plan(request: Request, body: PlanRequest):
    try:
        return await _service(request).plan(body.prompt, body.planner)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/generate", response_model=JobManifest)
async def generate(request: Request, body: GenerateRequest):
    try:
        return await _service(request).generate(
            body.prompt,
            body.planner,
            body.formats,
            body.render,
            body.backend,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/generate-from-spec", response_model=JobManifest)
async def generate_from_spec(request: Request, body: GenerateFromSpecRequest):
    try:
        return await _service(request).generate_from_spec(
            body.spec,
            body.formats,
            body.render,
            body.backend,
            drawing_spec=body.drawing_spec,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/manufacturing-template",
    response_model=ManufacturingDrawingSpec,
)
async def manufacturing_template(request: Request, body: ManufacturingTemplateRequest):
    try:
        return _service(request).manufacturing_template(
            body.spec,
            part_number=body.part_number,
            drawing_number=body.drawing_number,
            author=body.author,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/async/generate",
    response_model=QueueJobResponse,
    status_code=202,
)
async def enqueue_generate(request: Request, body: GenerateRequest):
    settings = request.app.state.settings
    if len(body.prompt) > settings.max_prompt_chars:
        raise HTTPException(
            status_code=422,
            detail=f"Prompt exceeds {settings.max_prompt_chars} characters",
        )
    try:
        return await asyncio.to_thread(
            _queue(request).enqueue,
            "prompt",
            body.model_dump(mode="json"),
        )
    except (ValueError, QueueFullError) as exc:
        raise _queue_error(exc) from exc


@router.post(
    "/async/generate-from-spec",
    response_model=QueueJobResponse,
    status_code=202,
)
async def enqueue_generate_from_spec(
    request: Request,
    body: GenerateFromSpecRequest,
):
    try:
        return await asyncio.to_thread(
            _queue(request).enqueue,
            "spec",
            body.model_dump(mode="json"),
        )
    except (ValueError, QueueFullError) as exc:
        raise _queue_error(exc) from exc


@router.get("/async/jobs", response_model=list[QueueJobResponse])
async def list_async_jobs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
):
    return await asyncio.to_thread(_queue(request).list, limit)


@router.get("/async/jobs/{queue_job_id}", response_model=QueueJobResponse)
async def get_async_job(request: Request, queue_job_id: str):
    try:
        return await asyncio.to_thread(_queue(request).get, queue_job_id)
    except (ValueError, QueueJobNotFound) as exc:
        raise _queue_error(exc) from exc


@router.post(
    "/async/jobs/{queue_job_id}/cancel",
    response_model=QueueJobResponse,
)
async def cancel_async_job(request: Request, queue_job_id: str):
    try:
        return await asyncio.to_thread(_queue(request).cancel, queue_job_id)
    except (ValueError, QueueJobNotFound) as exc:
        raise _queue_error(exc) from exc


@router.post("/image-analysis", response_model=ImageAnalysisResponse)
async def image_analysis(
    request: Request,
    image: Annotated[UploadFile, File()],
    known_length_mm: Annotated[float, Form(gt=0, le=100_000)],
    thickness_mm: Annotated[float, Form(gt=0, le=100_000)],
    perspective_correction: Annotated[bool, Form()] = False,
    page_index: Annotated[int, Form(ge=0, le=99)] = 0,
):
    service = _service(request)
    try:
        return await service.analyze_upload(
            image,
            known_length_mm=known_length_mm,
            thickness_mm=thickness_mm,
            perspective_correction=perspective_correction,
            page_index=page_index,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await image.close()


@router.post("/image-feature-tree-to-spec", response_model=PlanResponse)
async def image_feature_tree_to_spec(
    request: Request,
    body: FeatureTreeToSpecRequest,
):
    try:
        return _service(request).image_feature_tree_to_spec(
            body.analysis,
            body.feature_tree,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/generate-from-image-feature-tree", response_model=JobManifest)
async def generate_from_image_feature_tree(
    request: Request,
    body: GenerateFromImageFeatureTreeRequest,
):
    try:
        return await _service(request).generate_from_image_feature_tree(
            body.analysis,
            body.feature_tree,
            formats=body.formats,
            render=body.render,
            backend=body.backend,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/dxf-analysis", response_model=DxfAnalysisResponse)
async def dxf_analysis(
    request: Request,
    dxf: Annotated[UploadFile, File()],
    thickness_mm: Annotated[float, Form(gt=0, le=100_000)],
    unit_override: Annotated[Literal["auto", "mm", "inch", "cm"], Form()] = "auto",
    operation_mode: Annotated[Literal["auto", "extrude", "revolve"], Form()] = "auto",
):
    try:
        return await _service(request).analyze_dxf_upload(
            dxf,
            thickness_mm=thickness_mm,
            unit_override=unit_override,
            operation_mode=operation_mode,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await dxf.close()


@router.post("/dxf-feature-tree-to-spec", response_model=PlanResponse)
async def dxf_feature_tree_to_spec(
    request: Request,
    body: DxfFeatureTreeToSpecRequest,
):
    try:
        return _service(request).dxf_feature_tree_to_spec(
            body.analysis,
            body.feature_tree,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/generate-from-dxf-feature-tree", response_model=JobManifest)
async def generate_from_dxf_feature_tree(
    request: Request,
    body: GenerateFromDxfFeatureTreeRequest,
):
    try:
        return await _service(request).generate_from_dxf_feature_tree(
            body.analysis,
            body.feature_tree,
            formats=body.formats,
            render=body.render,
            backend=body.backend,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/validate", response_model=ValidationReport)
async def validate(request: Request, body: CadDocument):
    return _service(request).validate(body)


@router.get("/jobs", response_model=list[JobListItem])
async def list_jobs(request: Request):
    return _service(request).list()


@router.get("/jobs/{job_id}", response_model=JobManifest)
async def get_job(request: Request, job_id: str):
    validate_job_id(job_id)
    manifest = _service(request).get(job_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Job not found")
    return manifest


@router.get(
    "/jobs/{job_id}/manufacturing-review",
    response_model=ManufacturingReviewResponse,
)
async def get_manufacturing_review(request: Request, job_id: str):
    validate_job_id(job_id)
    try:
        return await asyncio.to_thread(
            _service(request).get_manufacturing_review,
            job_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/jobs/{job_id}/manufacturing-review/transitions",
    response_model=ManufacturingReviewResponse,
)
async def transition_manufacturing_review(
    request: Request,
    job_id: str,
    body: ReviewTransitionRequest,
):
    validate_job_id(job_id)
    try:
        return await asyncio.to_thread(
            _service(request).transition_manufacturing_review,
            job_id,
            body,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/files/{filename}")
async def get_file(request: Request, job_id: str, filename: str):
    validate_job_id(job_id)
    job_dir = _service(request).storage.path(job_id)
    path = safe_job_file(job_dir, filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=path.name)


@router.get("/jobs/{job_id}/bundle.zip")
async def bundle(request: Request, job_id: str):
    validate_job_id(job_id)
    service = _service(request)
    job_dir: Path = service.storage.path(job_id)
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Job not found")
    manifest = service.get(job_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Job not found")

    filenames = ["manifest.json", *(artifact.filename for artifact in manifest.artifacts)]
    if (job_dir / "drawing-spec.json").is_file():
        try:
            filenames.extend(service.manufacturing_review_filenames(job_id))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in sorted(set(filenames)):
            path = safe_job_file(job_dir, filename)
            if path.is_file() and not path.is_symlink():
                archive.write(path, arcname=path.name)
    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="promptcad-{job_id}.zip"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)

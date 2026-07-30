from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.core.security import require_api_token, safe_job_file, validate_job_id
from app.models.api import (
    CapabilityResponse,
    FeatureTreeToSpecRequest,
    GenerateFromImageFeatureTreeRequest,
    GenerateFromSpecRequest,
    GenerateRequest,
    JobListItem,
    JobManifest,
    PlanRequest,
    PlanResponse,
)
from app.models.cad import CadDocument, ValidationReport
from app.models.image import ImageAnalysisResponse

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_token)])


def _service(request: Request):
    return request.app.state.jobs


@router.get("/health")
async def health(request: Request):
    return {"status": "ok", "version": request.app.version}


@router.get("/capabilities", response_model=CapabilityResponse)
async def capabilities(request: Request):
    service = _service(request)
    settings = request.app.state.settings
    return CapabilityResponse(
        planners=["auto", "agent", "rule", "llm"],
        base_features=["plate", "cylinder", "ring", "l_bracket", "enclosure"],
        feature_types=["hole", "rectangular_cutout", "fillet", "chamfer"],
        hole_types=["through", "blind", "clearance", "tapped", "counterbore", "countersink"],
        formats=["step", "stl", "dxf", "svg", "pdf", "py", "scad", "json"],
        cadquery_available=service.renderer.cadquery_available(),
        openscad_available=service.renderer.openscad_available(),
        image_analysis_available=True,
        image_formats=["png", "jpeg"],
        configured_planner_mode=settings.planner_mode,
        configured_render_backend=settings.render_backend,
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
        return await _service(request).generate(body.prompt, body.planner, body.formats, body.render)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/generate-from-spec", response_model=JobManifest)
async def generate_from_spec(request: Request, body: GenerateFromSpecRequest):
    try:
        return await _service(request).generate_from_spec(
            body.spec,
            body.formats,
            body.render,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/image-analysis", response_model=ImageAnalysisResponse)
async def image_analysis(
    request: Request,
    image: Annotated[UploadFile, File()],
    known_length_mm: Annotated[float, Form(gt=0, le=100_000)],
    thickness_mm: Annotated[float, Form(gt=0, le=100_000)],
):
    service = _service(request)
    try:
        return await service.analyze_upload(
            image,
            known_length_mm=known_length_mm,
            thickness_mm=thickness_mm,
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
    job_dir: Path = _service(request).storage.path(job_id)
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Job not found")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(job_dir.iterdir()):
            if path.is_file() and not path.name.startswith(".tmp-"):
                archive.write(path, arcname=path.name)
    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="promptcad-{job_id}.zip"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from app.core.config import Settings
from app.models.api import (
    Artifact,
    BackendDiagnostic,
    FormatResult,
    JobListItem,
    JobManifest,
    ManufacturingHashBinding,
    ManufacturingReviewResponse,
    PlanResponse,
)
from app.models.cad import CadDocument
from app.models.dxf import DxfAnalysisResponse, DxfFeatureTreeNode
from app.models.image import FeatureTreeNode, ImageAnalysisResponse
from app.models.manufacturing import ManufacturingDrawingSpec, ReviewTransitionRequest
from app.services.backends import BACKEND_CONTRACT_VERSION, default_backend_registry
from app.services.cancellation import CancelCheck, JobCancelled
from app.services.drawing_pdf import EngineeringDrawingPdf
from app.services.dxf_analysis import DxfAnalysisError, DxfFeatureExtractor
from app.services.image_analysis import ImageFeatureExtractor
from app.services.manufacturing import ManufacturingDrawingService
from app.services.planners.factory import PlannerFactory
from app.services.preview import SvgPreview
from app.services.renderer import Renderer
from app.services.storage import JobStorage
from app.services.validator import DesignValidator


class JobService:
    _MANUFACTURING_CLAIM_LEASE_SECONDS = 300

    def __init__(self, settings: Settings):
        self.settings = settings
        self.planners = PlannerFactory(settings)
        self.validator = DesignValidator()
        self.backends = default_backend_registry()
        self.drawing = EngineeringDrawingPdf()
        self.preview = SvgPreview()
        self.manufacturing = ManufacturingDrawingService()
        self.storage = JobStorage(settings)
        self.renderer = Renderer(settings)
        self.image_extractor = ImageFeatureExtractor(
            max_bytes=settings.max_image_bytes,
            max_pixels=settings.max_image_pixels,
            max_dimension=settings.max_image_dimension,
            max_pdf_pages=settings.max_pdf_pages,
        )
        self.dxf_extractor = DxfFeatureExtractor(
            max_bytes=settings.max_dxf_bytes,
            max_entities=settings.max_dxf_entities,
            max_segments=settings.max_dxf_segments,
            max_holes=settings.max_dxf_holes,
        )
        self._image_slots = asyncio.Semaphore(settings.image_analysis_concurrency)
        self._dxf_slots = asyncio.Semaphore(settings.dxf_analysis_concurrency)
        self._render_slots = asyncio.Semaphore(settings.render_concurrency)
        self._image_executor = ThreadPoolExecutor(
            max_workers=settings.image_analysis_concurrency,
            thread_name_prefix="promptcad-image",
        )
        self._analysis_signing_key = secrets.token_bytes(32)
        self._manufacturing_locks: dict[str, threading.Lock] = {}
        self._manufacturing_locks_guard = threading.Lock()

    def close(self) -> None:
        self._image_executor.shutdown(wait=False, cancel_futures=True)

    async def plan(self, prompt: str, planner_choice: str) -> PlanResponse:
        if len(prompt) > self.settings.max_prompt_chars:
            raise ValueError(f"Prompt exceeds {self.settings.max_prompt_chars} characters")
        spec, planner_used = await self.planners.plan(prompt, planner_choice)
        validation = self.validator.validate(spec)
        return PlanResponse(spec=spec, validation=validation, planner_used=planner_used)

    async def generate(
        self,
        prompt: str,
        planner_choice: str,
        formats: list[str],
        render: bool,
        backend: str = "auto",
        cancel_check: CancelCheck | None = None,
    ) -> JobManifest:
        if cancel_check is not None and cancel_check():
            raise JobCancelled("Job cancelled before planning")
        plan = await self.plan(prompt, planner_choice)
        if cancel_check is not None and cancel_check():
            raise JobCancelled("Job cancelled after planning")
        return await self._materialize(
            spec=plan.spec,
            validation=plan.validation,
            prompt=prompt,
            planner_used=plan.planner_used,
            formats=formats,
            render=render,
            backend=backend,
            cancel_check=cancel_check,
        )

    async def generate_from_spec(
        self,
        spec: CadDocument,
        formats: list[str],
        render: bool,
        backend: str = "auto",
        cancel_check: CancelCheck | None = None,
        drawing_spec: ManufacturingDrawingSpec | None = None,
    ) -> JobManifest:
        if cancel_check is not None and cancel_check():
            raise JobCancelled("Job cancelled before validation")
        validation = self.validator.validate(spec)
        if drawing_spec is not None:
            if not validation.valid:
                raise ValueError(
                    "A manufacturing package requires a valid CAD document"
                )
            if "pdf" not in formats:
                raise ValueError(
                    "A manufacturing package requires pdf in the requested formats"
                )
            self.manufacturing.validate_against_cad(spec, drawing_spec)
            if (
                self._enum_value(drawing_spec.review_status) != "draft"
                or drawing_spec.review_version != 0
                or drawing_spec.review_records
            ):
                raise ValueError(
                    "A new manufacturing package must start as an empty draft at version 0"
                )
        return await self._materialize(
            spec=spec,
            validation=validation,
            prompt=spec.source_prompt,
            planner_used="manual-dsl",
            formats=formats,
            render=render,
            backend=backend,
            cancel_check=cancel_check,
            drawing_spec=drawing_spec,
        )

    def manufacturing_template(
        self,
        spec: CadDocument,
        *,
        part_number: str | None = None,
        drawing_number: str | None = None,
        author: str = "PromptCAD",
    ) -> ManufacturingDrawingSpec:
        validation = self.validator.validate(spec)
        if not validation.valid:
            raise ValueError("A manufacturing template requires a valid CAD document")
        return self.manufacturing.create_default(
            spec,
            part_number=part_number,
            drawing_number=drawing_number,
            author=author,
        )

    async def analyze_image(
        self,
        data: bytes,
        *,
        known_length_mm: float,
        thickness_mm: float,
        perspective_correction: bool = False,
        page_index: int = 0,
        content_profile: str = "auto",
        object_index: int | None = None,
        accept_line_art_holes: bool = False,
    ):
        if len(data) > self.settings.max_image_bytes:
            raise ValueError(f"Image exceeds the {self.settings.max_image_bytes} byte limit")
        await self._image_slots.acquire()
        return await self._analyze_admitted(
            data,
            known_length_mm=known_length_mm,
            thickness_mm=thickness_mm,
            perspective_correction=perspective_correction,
            page_index=page_index,
            content_profile=content_profile,
            object_index=object_index,
            accept_line_art_holes=accept_line_art_holes,
        )

    async def analyze_upload(
        self,
        upload,
        *,
        known_length_mm: float,
        thickness_mm: float,
        perspective_correction: bool = False,
        page_index: int = 0,
        content_profile: str = "auto",
        object_index: int | None = None,
        accept_line_art_holes: bool = False,
    ) -> ImageAnalysisResponse:
        await self._image_slots.acquire()
        try:
            data = await upload.read(self.settings.max_image_bytes + 1)
            if len(data) > self.settings.max_image_bytes:
                raise ValueError(
                    f"Image exceeds the {self.settings.max_image_bytes} byte limit"
                )
        except BaseException:
            self._image_slots.release()
            raise
        return await self._analyze_admitted(
            data,
            known_length_mm=known_length_mm,
            thickness_mm=thickness_mm,
            perspective_correction=perspective_correction,
            page_index=page_index,
            content_profile=content_profile,
            object_index=object_index,
            accept_line_art_holes=accept_line_art_holes,
        )

    async def _analyze_admitted(
        self,
        data: bytes,
        *,
        known_length_mm: float,
        thickness_mm: float,
        perspective_correction: bool,
        page_index: int,
        content_profile: str,
        object_index: int | None,
        accept_line_art_holes: bool,
    ) -> ImageAnalysisResponse:
        loop = asyncio.get_running_loop()
        try:
            future = loop.run_in_executor(
                self._image_executor,
                partial(
                    self.image_extractor.analyze,
                    data,
                    known_length_mm=known_length_mm,
                    thickness_mm=thickness_mm,
                    perspective_correction=perspective_correction,
                    page_index=page_index,
                    content_profile=content_profile,
                    object_index=object_index,
                    accept_line_art_holes=accept_line_art_holes,
                ),
            )
        except BaseException:
            self._image_slots.release()
            raise
        future.add_done_callback(lambda _future: self._image_slots.release())
        try:
            result = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=self.settings.image_analysis_timeout_seconds,
            )
        except TimeoutError as exc:
            raise RuntimeError(
                "Image analysis timed out; worker capacity remains reserved until it stops"
            ) from exc
        return self._sign_analysis(result)

    async def analyze_dxf_bytes(
        self,
        data: bytes,
        *,
        thickness_mm: float,
        unit_override: str = "auto",
        operation_mode: str = "auto",
    ) -> DxfAnalysisResponse:
        if len(data) > self.settings.max_dxf_bytes:
            raise ValueError(f"DXF exceeds the {self.settings.max_dxf_bytes} byte limit")
        await self._dxf_slots.acquire()
        try:
            return await self._analyze_dxf_admitted(
                data,
                thickness_mm=thickness_mm,
                unit_override=unit_override,
                operation_mode=operation_mode,
            )
        finally:
            self._dxf_slots.release()

    async def analyze_dxf_upload(
        self,
        upload,
        *,
        thickness_mm: float,
        unit_override: str = "auto",
        operation_mode: str = "auto",
    ) -> DxfAnalysisResponse:
        await self._dxf_slots.acquire()
        try:
            data = await upload.read(self.settings.max_dxf_bytes + 1)
            if len(data) > self.settings.max_dxf_bytes:
                raise ValueError(
                    f"DXF exceeds the {self.settings.max_dxf_bytes} byte limit"
                )
            return await self._analyze_dxf_admitted(
                data,
                thickness_mm=thickness_mm,
                unit_override=unit_override,
                operation_mode=operation_mode,
            )
        finally:
            self._dxf_slots.release()

    async def _analyze_dxf_admitted(
        self,
        data: bytes,
        *,
        thickness_mm: float,
        unit_override: str,
        operation_mode: str,
    ) -> DxfAnalysisResponse:
        if unit_override not in {"auto", "mm", "inch", "cm"}:
            raise ValueError("DXF units must be auto, mm, inch, or cm")
        if operation_mode not in {"auto", "extrude", "revolve"}:
            raise ValueError("DXF operation must be auto, extrude, or revolve")
        result = await asyncio.to_thread(
            self._run_dxf_worker,
            data,
            thickness_mm,
            unit_override,
            operation_mode,
        )
        return self._sign_analysis(result)

    def _run_dxf_worker(
        self,
        data: bytes,
        thickness_mm: float,
        unit_override: str,
        operation_mode: str = "auto",
    ) -> DxfAnalysisResponse:
        self.settings.ensure_directories()
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".promptcad-dxf-",
                suffix=".dxf",
                delete=False,
            ) as stream:
                stream.write(data)
                path = Path(stream.name)
            request = {
                "path": str(path.resolve()),
                "thickness_mm": thickness_mm,
                "unit_override": unit_override,
                "operation_mode": operation_mode,
                "max_bytes": self.settings.max_dxf_bytes,
                "max_entities": self.settings.max_dxf_entities,
                "max_segments": self.settings.max_dxf_segments,
                "max_holes": self.settings.max_dxf_holes,
            }
            try:
                child_env = {
                    key: os.environ[key]
                    for key in (
                        "LANG",
                        "LC_ALL",
                        "APPDATA",
                        "HOME",
                        "LOCALAPPDATA",
                        "PATH",
                        "SYSTEMROOT",
                        "TEMP",
                        "TMP",
                        "USERPROFILE",
                        "WINDIR",
                        "XDG_CONFIG_HOME",
                    )
                    if key in os.environ
                }
                child_env.update(
                    {
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONIOENCODING": "utf-8",
                        "PYTHONUTF8": "1",
                    }
                )
                completed = subprocess.run(
                    [sys.executable, "-m", "app.workers.dxf_analyze"],
                    input=json.dumps(request, separators=(",", ":")),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.settings.dxf_analysis_timeout_seconds,
                    check=False,
                    shell=False,
                    cwd=Path(__file__).resolve().parents[2],
                    env=child_env,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("DXF analysis timed out and was terminated") from exc
            if completed.returncode != 0:
                detail = completed.stderr.strip()[:1_000]
                raise RuntimeError(f"DXF analysis worker failed: {detail or 'unknown error'}")
            if len(completed.stdout) > 1_000_000:
                raise RuntimeError("DXF analysis worker produced an oversized response")
            try:
                payload = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeError("DXF analysis worker returned invalid JSON") from exc
            if not payload.get("ok"):
                raise DxfAnalysisError(str(payload.get("error", "DXF analysis failed")))
            return DxfAnalysisResponse.model_validate(payload["result"])
        finally:
            if path is not None:
                with suppress(FileNotFoundError):
                    os.unlink(path)

    def image_feature_tree_to_spec(
        self,
        analysis: ImageAnalysisResponse,
        feature_tree: list[FeatureTreeNode],
    ) -> PlanResponse:
        self._verify_analysis(analysis)
        spec = self.image_extractor.spec_from_feature_tree(
            feature_tree,
            image_sha256=analysis.image_sha256,
        )
        validation = self.validator.validate(spec)
        return PlanResponse(
            spec=spec,
            validation=validation,
            planner_used="image-feature-tree",
        )

    async def generate_from_image_feature_tree(
        self,
        analysis: ImageAnalysisResponse,
        feature_tree: list[FeatureTreeNode],
        *,
        formats: list[str],
        render: bool,
        backend: str = "auto",
    ) -> JobManifest:
        plan = self.image_feature_tree_to_spec(
            analysis,
            feature_tree,
        )
        analysis_payload = analysis.model_dump(
            mode="json",
            exclude={
                "analysis_token",
                "preview_svg",
                "proposed_spec",
                "validation",
                "feature_tree",
            },
        )
        analysis_payload["feature_tree_file"] = "feature-tree.json"
        analysis_payload["provenance_verification"] = "verified-before-generation"
        return await self._materialize(
            spec=plan.spec,
            validation=plan.validation,
            prompt=plan.spec.source_prompt,
            planner_used="image-feature-tree",
            formats=formats,
            render=render,
            backend=backend,
            extra_json_artifacts={
                "image-analysis.json": analysis_payload,
                "feature-tree.json": [
                    node.model_dump(mode="json") for node in feature_tree
                ],
            },
        )

    def dxf_feature_tree_to_spec(
        self,
        analysis: DxfAnalysisResponse,
        feature_tree: list[DxfFeatureTreeNode],
    ) -> PlanResponse:
        self._verify_analysis(analysis, kind="DXF")
        spec = self.dxf_extractor.spec_from_feature_tree(
            feature_tree,
            analysis.provenance,
        )
        validation = self.validator.validate(spec)
        return PlanResponse(
            spec=spec,
            validation=validation,
            planner_used="dxf-feature-tree",
        )

    async def generate_from_dxf_feature_tree(
        self,
        analysis: DxfAnalysisResponse,
        feature_tree: list[DxfFeatureTreeNode],
        *,
        formats: list[str],
        render: bool,
        backend: str = "auto",
    ) -> JobManifest:
        plan = self.dxf_feature_tree_to_spec(analysis, feature_tree)
        analysis_payload = analysis.model_dump(
            mode="json",
            exclude={
                "analysis_token",
                "preview_svg",
                "proposed_spec",
                "validation",
                "feature_tree",
            },
        )
        analysis_payload["feature_tree_file"] = "dxf-feature-tree.json"
        analysis_payload["provenance_verification"] = "verified-before-generation"
        return await self._materialize(
            spec=plan.spec,
            validation=plan.validation,
            prompt=plan.spec.source_prompt,
            planner_used="dxf-feature-tree",
            formats=formats,
            render=render,
            backend=backend,
            extra_json_artifacts={
                "dxf-analysis.json": analysis_payload,
                "dxf-feature-tree.json": [
                    node.model_dump(mode="json") for node in feature_tree
                ],
            },
        )

    def _sign_analysis(self, analysis):
        token = hmac.new(
            self._analysis_signing_key,
            self._analysis_payload(analysis),
            hashlib.sha256,
        ).hexdigest()
        return analysis.model_copy(update={"analysis_token": token})

    def _verify_analysis(self, analysis, *, kind: str = "Image") -> None:
        supplied = analysis.analysis_token
        expected = hmac.new(
            self._analysis_signing_key,
            self._analysis_payload(analysis),
            hashlib.sha256,
        ).hexdigest()
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise ValueError(f"{kind} analysis provenance is invalid or expired")

    @staticmethod
    def _analysis_payload(analysis) -> bytes:
        value = analysis.model_dump(
            mode="json",
            exclude={
                "analysis_token",
                "preview_svg",
                "proposed_spec",
                "validation",
                "feature_tree",
            },
        )

        def normalize_json(value):
            if isinstance(value, float) and value == 0:
                return 0.0
            if isinstance(value, dict):
                return {
                    key: normalize_json(item)
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [normalize_json(item) for item in value]
            return value

        return json.dumps(
            normalize_json(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    async def _materialize(
        self,
        *,
        spec: CadDocument,
        validation,
        prompt: str,
        planner_used: str,
        formats: list[str],
        render: bool,
        backend: str = "auto",
        drawing_spec: ManufacturingDrawingSpec | None = None,
        extra_json_artifacts: dict[str, object] | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> JobManifest:
        if cancel_check is not None and cancel_check():
            raise JobCancelled("Job cancelled before materialization")
        selection_request = (
            self.settings.render_backend
            if backend == "auto" and self.settings.render_backend != "auto"
            else backend
        )
        selection = self.backends.select(
            selection_request,
            doc=spec,
            formats=formats,
            render=render,
            allow_source_fallback=self.settings.allow_source_fallback,
        )
        job_id, job_dir = self.storage.create()
        created_at = datetime.now(UTC).isoformat()
        warnings: list[str] = []
        backend_diagnostics: list[BackendDiagnostic] = list(selection.diagnostics)
        fallback_chain = list(selection.fallback_chain)
        error: str | None = None
        renderer_used = "not-run"
        status = "source_only"
        canonical_spec = json.dumps(
            spec.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        spec_sha256 = hashlib.sha256(canonical_spec).hexdigest()

        self.storage.write_json(job_dir / "spec.json", spec.model_dump(mode="json"))
        if drawing_spec is not None:
            self.storage.write_json(
                job_dir / "drawing-spec.json",
                drawing_spec.model_dump(mode="json"),
            )
        self.storage.write_json(
            job_dir / "validation.json",
            validation.model_dump(mode="json"),
        )
        source_backends: list[str] = []
        sources, source_diagnostics = self.backends.compile_sources(spec)
        backend_diagnostics.extend(source_diagnostics)
        for source in sources:
            self.storage.write_text(job_dir / source.filename, source.content)
            source_backends.append(source.backend_id)
        source_records = [
            {
                "backend_id": source.backend_id,
                "filename": source.filename,
                "sha256": hashlib.sha256(
                    source.content.encode("utf-8")
                ).hexdigest(),
            }
            for source in sources
        ]
        warnings.extend(item.message for item in backend_diagnostics)
        for filename, payload in (extra_json_artifacts or {}).items():
            if filename not in {
                "image-analysis.json",
                "feature-tree.json",
                "dxf-analysis.json",
                "dxf-feature-tree.json",
            }:
                raise ValueError("Unsupported internal JSON artifact")
            self.storage.write_json(job_dir / filename, payload)
        try:
            self.preview.write(spec, job_dir / "preview.svg")
        except ValueError as exc:
            warnings.append(f"快速預覽已略過：{exc}")
        if "pdf" in formats and validation.valid:
            if drawing_spec is None:
                self.drawing.write(spec, job_dir / "drawing.pdf")
            else:
                draft_pdf = self.drawing.render(spec, manufacturing=drawing_spec)
                self.storage.write_bytes(job_dir / "drawing.pdf", draft_pdf)
                self._create_manufacturing_review(
                    job_id,
                    job_dir,
                    drawing_spec,
                )

        if not validation.valid:
            status = "failed"
            renderer_used = "validation-blocked"
            error = "CAD validation failed; rendering was skipped."
            warnings.extend(
                issue.message
                for issue in validation.issues
                if issue.severity.value == "error"
            )
        elif render:
            try:
                render_formats = list(dict.fromkeys(formats))
                selected_registration = (
                    self.backends.get(selection.requested)
                    if selection.requested not in {"auto", "source_only"}
                    else None
                )
                if (
                    selected_registration is not None
                    and selected_registration.execution_kind == "host_application"
                    and selection.effective in {"cadquery", "build123d"}
                    and "step" not in render_formats
                ):
                    render_formats.append("step")
                async with self._render_slots:
                    result = await asyncio.to_thread(
                        self.renderer.render,
                        job_dir,
                        render_formats,
                        selection.effective,
                        cancel_check,
                    )
                renderer_used = result.renderer
                status = result.status
                warnings.extend(result.warnings)
                fallback_chain = self._merge_fallback_chains(
                    fallback_chain,
                    result.fallback_chain,
                )
                if result.fallback_chain and len(result.fallback_chain) > 1:
                    backend_diagnostics.append(
                        BackendDiagnostic(
                            backend_id=result.renderer,
                            severity="warning",
                            code="runtime_fallback",
                            message=(
                                "CAD runtime 執行期間已降級："
                                + " → ".join(result.fallback_chain)
                            ),
                        )
                    )
                for message in result.diagnostics:
                    backend_diagnostics.append(
                        BackendDiagnostic(
                            backend_id=result.renderer,
                            severity="warning",
                            code="runtime_diagnostic",
                            message=message,
                        )
                    )
            except JobCancelled as exc:
                status = "cancelled"
                renderer_used = "cancelled"
                error = str(exc)
                warnings.append("工作已依使用者要求取消。")
            except RuntimeError as exc:
                status = "failed"
                renderer_used = "failed"
                error = str(exc)
        else:
            renderer_used = "not-run"
            warnings.append("render=false：只產生 DSL 與 CAD 原始碼。")

        if renderer_used in {*self.backends.ids, "source_only"}:
            backend_used = renderer_used
        else:
            backend_used = "source_only"
        self.storage.write_json(
            job_dir / "backend-report.json",
            {
                "contract_version": BACKEND_CONTRACT_VERSION,
                "spec_sha256": spec_sha256,
                "backend_requested": backend,
                "backend_selected": selection.requested,
                "backend_effective": backend_used,
                "adapter_target": (
                    selection.requested
                    if selection.requested not in {"auto", "source_only"}
                    and self.backends.get(selection.requested).execution_kind
                    == "host_application"
                    else None
                ),
                "renderer_used": renderer_used,
                "status": status,
                "fallback_chain": fallback_chain,
                "sources": source_records,
                "capabilities": [
                    item.model_dump(mode="json")
                    for item in self.backends.capabilities()
                ],
                "diagnostics": [
                    item.model_dump(mode="json")
                    for item in backend_diagnostics
                ],
            },
        )
        artifacts = self._artifacts(job_id, job_dir)
        source_backend = (
            selection.requested
            if selection.requested not in {"auto", "source_only"}
            else backend_used
        )
        format_results = self._format_results(
            formats,
            artifacts,
            status,
            source_backend=source_backend,
        )
        manifest = JobManifest(
            job_id=job_id,
            status=status,
            created_at=created_at,
            prompt=prompt,
            planner_used=planner_used,
            renderer_used=renderer_used,
            requested_formats=list(dict.fromkeys(formats)),
            spec=spec,
            validation=validation,
            artifacts=artifacts,
            warnings=warnings,
            error=error,
            backend_requested=backend,
            backend_used=backend_used,
            backend_contract_version=BACKEND_CONTRACT_VERSION,
            source_backends=source_backends,
            backend_diagnostics=backend_diagnostics,
            format_results=format_results,
            fallback_chain=fallback_chain,
            spec_sha256=spec_sha256,
            completed_at=datetime.now(UTC),
        )
        self.storage.write_json(job_dir / "manifest.json", manifest.model_dump(mode="json"))
        return manifest

    def _create_manufacturing_review(
        self,
        job_id: str,
        job_dir: Path,
        drawing_spec: ManufacturingDrawingSpec,
    ) -> ManufacturingReviewResponse:
        review_path = job_dir / "manufacturing-review-v000.json"
        self._require_new_artifacts(review_path)
        status = self._enum_value(drawing_spec.review_status)
        version = drawing_spec.review_version
        if status != "draft" or version != 0 or drawing_spec.review_records:
            raise ValueError(
                "A new manufacturing package must start as an empty draft at version 0"
            )
        response = ManufacturingReviewResponse(
            job_id=job_id,
            version=version,
            status=status,
            hashes=ManufacturingHashBinding(
                spec_sha256=self._sha256_file(job_dir / "spec.json"),
                drawing_spec_sha256=self._sha256_file(job_dir / "drawing-spec.json"),
                draft_pdf_sha256=self._sha256_file(job_dir / "drawing.pdf"),
            ),
            current_drawing_spec_sha256=self._sha256_file(
                job_dir / "drawing-spec.json"
            ),
            current_pdf_sha256=self._sha256_file(job_dir / "drawing.pdf"),
            drawing_spec_filename="drawing-spec.json",
            draft_pdf_filename="drawing.pdf",
            latest_pdf_filename="drawing.pdf",
            events=[],
        )
        self.storage.write_json_once(review_path, response.model_dump(mode="json"))
        return response

    def get_manufacturing_review(
        self,
        job_id: str,
        *,
        verify_integrity: bool = True,
    ) -> ManufacturingReviewResponse:
        job_dir = self.storage.path(job_id)
        if not job_dir.is_dir() or self.get(job_id) is None:
            raise FileNotFoundError("Job not found")
        snapshots: dict[int, Path] = {}
        for path in job_dir.glob("manufacturing-review-v*.json"):
            suffix = path.name.removeprefix("manufacturing-review-v").removesuffix(
                ".json"
            )
            version_text = suffix.split("-", 1)[0]
            if not version_text.isdigit():
                continue
            version = int(version_text)
            if version in snapshots:
                raise RuntimeError(
                    "Manufacturing review contains duplicate state snapshots"
                )
            snapshots[version] = path
        if not snapshots:
            raise FileNotFoundError("Manufacturing review not found")
        version = max(snapshots)
        try:
            review = ManufacturingReviewResponse.model_validate_json(
                snapshots[version].read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError("Manufacturing review state is invalid") from exc
        if review.job_id != job_id or review.version != version:
            raise RuntimeError("Manufacturing review snapshot provenance mismatch")
        if any(
            not (job_dir / f"manufacturing-review-v{item:03d}.json").is_file()
            for item in range(version + 1)
        ):
            raise RuntimeError("Manufacturing review snapshot history is incomplete")
        if len(review.events) != review.version:
            raise RuntimeError("Manufacturing review event history is incomplete")
        if review.events and self._enum_value(review.events[-1].to_status) != review.status:
            raise RuntimeError("Manufacturing review status does not match its event history")
        if verify_integrity:
            self._verify_manufacturing_review(job_dir, review)
        return review

    def transition_manufacturing_review(
        self,
        job_id: str,
        request: ReviewTransitionRequest,
    ) -> ManufacturingReviewResponse:
        job_dir = self.storage.path(job_id)
        lock = self._manufacturing_lock(job_id)
        with lock, self._manufacturing_process_lock(job_dir):
            current = self.get_manufacturing_review(job_id)
            if request.expected_version != current.version:
                raise RuntimeError(
                    "Manufacturing review version conflict: "
                    f"expected {request.expected_version}, current {current.version}"
                )
            if current.status in {"approved", "rejected"}:
                raise RuntimeError(
                    f"Manufacturing review is terminal in state {current.status}"
                )
            action = self._enum_value(request.action)
            if action == "reject" and not request.note.strip():
                raise ValueError("A rejection requires a non-empty review note")

            next_version = current.version + 1
            claim_path, claim_id = self._acquire_manufacturing_claim(
                job_dir,
                next_version,
                request,
            )
            try:
                manifest = self.get(job_id)
                if manifest is None:
                    raise FileNotFoundError("Job not found")
                current_spec_path = job_dir / current.drawing_spec_filename
                try:
                    drawing_spec = ManufacturingDrawingSpec.model_validate_json(
                        current_spec_path.read_text(encoding="utf-8")
                    )
                except (OSError, ValueError) as exc:
                    raise RuntimeError(
                        "Current manufacturing drawing spec is invalid"
                    ) from exc
                updated = self.manufacturing.transition(drawing_spec, request)
                self.manufacturing.validate_against_cad(manifest.spec, updated)
                updated_version = updated.review_version
                updated_status = self._enum_value(updated.review_status)
                if updated_version != next_version:
                    raise RuntimeError(
                        "Manufacturing transition did not advance one version"
                    )
                if len(updated.review_records) != len(current.events) + 1:
                    raise RuntimeError(
                        "Manufacturing transition did not append exactly one event"
                    )

                token = secrets.token_hex(6)
                spec_filename = (
                    f"drawing-spec-review-v{updated_version:03d}-{token}.json"
                )
                pdf_filename = (
                    f"drawing-review-v{updated_version:03d}-{updated_status}-{token}.pdf"
                )
                snapshot_filename = (
                    f"manufacturing-review-v{updated_version:03d}.json"
                )
                spec_path = job_dir / spec_filename
                pdf_path = job_dir / pdf_filename
                snapshot_path = job_dir / snapshot_filename
                self._require_new_artifacts(spec_path, pdf_path, snapshot_path)

                review_summary = {
                    "status": updated_status,
                    "version": updated_version,
                    "event": updated.review_records[-1].model_dump(mode="json"),
                    "signature_notice": (
                        "Reviewer labels are self-asserted workflow metadata, not a "
                        "cryptographic or legal signature."
                    ),
                }
                pdf_bytes = self.drawing.render(
                    manifest.spec,
                    manufacturing=updated,
                    review_summary=review_summary,
                )
                self.storage.write_bytes(pdf_path, pdf_bytes, overwrite=False)
                self.storage.write_json_once(
                    spec_path,
                    updated.model_dump(mode="json"),
                )

                response = ManufacturingReviewResponse(
                    job_id=job_id,
                    version=updated_version,
                    status=updated_status,
                    hashes=current.hashes,
                    current_drawing_spec_sha256=self._sha256_file(spec_path),
                    current_pdf_sha256=self._sha256_file(pdf_path),
                    drawing_spec_filename=spec_filename,
                    draft_pdf_filename=current.draft_pdf_filename,
                    latest_pdf_filename=pdf_filename,
                    events=updated.review_records,
                )
                self.storage.write_json_once(
                    snapshot_path,
                    response.model_dump(mode="json"),
                )
                return response
            finally:
                self._release_manufacturing_claim(claim_path, claim_id)

    def _acquire_manufacturing_claim(
        self,
        job_dir: Path,
        next_version: int,
        request: ReviewTransitionRequest,
    ) -> tuple[Path, str]:
        claim_path = job_dir / f"manufacturing-review-v{next_version:03d}.claim"
        snapshot_path = job_dir / f"manufacturing-review-v{next_version:03d}.json"
        claim_id = secrets.token_hex(16)
        now = datetime.now(UTC)
        payload = {
            "claim_id": claim_id,
            "created_at": now.isoformat(),
            "lease_expires_at": (
                now.timestamp() + self._MANUFACTURING_CLAIM_LEASE_SECONDS
            ),
            "request": request.model_dump(mode="json"),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        for attempt in range(2):
            try:
                self.storage.write_bytes(claim_path, encoded, overwrite=False)
                return claim_path, claim_id
            except FileExistsError as exc:
                if snapshot_path.is_file():
                    raise RuntimeError(
                        "Manufacturing review version was already transitioned"
                    ) from exc
                try:
                    existing_bytes = claim_path.read_bytes()
                    existing = json.loads(existing_bytes)
                    lease_expires_at = float(existing["lease_expires_at"])
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    try:
                        lease_expires_at = (
                            claim_path.stat().st_mtime
                            + self._MANUFACTURING_CLAIM_LEASE_SECONDS
                        )
                    except OSError:
                        lease_expires_at = now.timestamp() + 1
                    existing_bytes = b""

                if datetime.now(UTC).timestamp() < lease_expires_at:
                    raise RuntimeError(
                        "Manufacturing review version is already being transitioned"
                    ) from exc
                if attempt:
                    raise RuntimeError(
                        "Manufacturing review version recovery was contested"
                    ) from exc

                # The state snapshot is the commit record. With no snapshot and
                # an expired claim, version-scoped derivatives are interrupted
                # transaction debris and may be removed before a single retry.
                if existing_bytes:
                    try:
                        if claim_path.read_bytes() != existing_bytes:
                            raise RuntimeError(
                                "Manufacturing review claim changed during recovery"
                            )
                    except OSError as read_exc:
                        raise RuntimeError(
                            "Manufacturing review claim could not be recovered"
                        ) from read_exc
                for pattern in (
                    f"drawing-review-v{next_version:03d}-*.pdf",
                    f"drawing-spec-review-v{next_version:03d}-*.json",
                ):
                    for orphan in job_dir.glob(pattern):
                        orphan.unlink(missing_ok=True)
                claim_path.unlink(missing_ok=True)

        raise RuntimeError("Manufacturing review claim could not be acquired")

    @staticmethod
    def _release_manufacturing_claim(claim_path: Path, claim_id: str) -> None:
        try:
            payload = json.loads(claim_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return
        if payload.get("claim_id") == claim_id:
            claim_path.unlink(missing_ok=True)

    def _verify_manufacturing_review(
        self,
        job_dir: Path,
        review: ManufacturingReviewResponse,
    ) -> None:
        expected = {
            job_dir / "spec.json": review.hashes.spec_sha256,
            job_dir / "drawing-spec.json": review.hashes.drawing_spec_sha256,
            job_dir / review.draft_pdf_filename: review.hashes.draft_pdf_sha256,
            job_dir / review.drawing_spec_filename: review.current_drawing_spec_sha256,
            job_dir / review.latest_pdf_filename: review.current_pdf_sha256,
        }
        for path, expected_hash in expected.items():
            if not path.is_file() or self._sha256_file(path) != expected_hash:
                raise RuntimeError(
                    f"Manufacturing review integrity check failed for {path.name}"
                )
        try:
            drawing_spec = ManufacturingDrawingSpec.model_validate_json(
                (job_dir / review.drawing_spec_filename).read_text(encoding="utf-8")
            )
            cad_document = CadDocument.model_validate_json(
                (job_dir / "spec.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError("Manufacturing review bound inputs are invalid") from exc
        self.manufacturing.validate_against_cad(cad_document, drawing_spec)
        if drawing_spec.review_version != review.version:
            raise RuntimeError("Manufacturing drawing version does not match review state")
        if self._enum_value(drawing_spec.review_status) != review.status:
            raise RuntimeError("Manufacturing drawing status does not match review state")
        if drawing_spec.review_records != review.events:
            raise RuntimeError("Manufacturing drawing events do not match review state")

    def manufacturing_review_filenames(self, job_id: str) -> list[str]:
        """Return only integrity-checked files referenced by the review history."""
        current = self.get_manufacturing_review(job_id)
        job_dir = self.storage.path(job_id)
        filenames = {"drawing-spec.json", current.draft_pdf_filename}
        for version in range(current.version + 1):
            snapshot_path = job_dir / f"manufacturing-review-v{version:03d}.json"
            try:
                snapshot = ManufacturingReviewResponse.model_validate_json(
                    snapshot_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    "Manufacturing review history contains an invalid snapshot"
                ) from exc
            if (
                snapshot.job_id != job_id
                or snapshot.version != version
                or snapshot.hashes != current.hashes
                or snapshot.events != current.events[:version]
            ):
                raise RuntimeError(
                    "Manufacturing review history is discontinuous or has mismatched provenance"
                )
            if version == 0:
                expected_status = "draft"
            else:
                expected_status = self._enum_value(snapshot.events[-1].to_status)
            if snapshot.status != expected_status:
                raise RuntimeError(
                    "Manufacturing review history contains a status mismatch"
                )
            expected_files = {
                job_dir / snapshot.drawing_spec_filename: (
                    snapshot.current_drawing_spec_sha256
                ),
                job_dir / snapshot.latest_pdf_filename: snapshot.current_pdf_sha256,
            }
            for path, expected_hash in expected_files.items():
                if not path.is_file() or self._sha256_file(path) != expected_hash:
                    raise RuntimeError(
                        f"Manufacturing review history integrity failed for {path.name}"
                    )
            filenames.update(
                {
                    snapshot_path.name,
                    snapshot.drawing_spec_filename,
                    snapshot.latest_pdf_filename,
                }
            )
        return sorted(filenames)

    def _manufacturing_lock(self, job_id: str) -> threading.Lock:
        with self._manufacturing_locks_guard:
            return self._manufacturing_locks.setdefault(job_id, threading.Lock())

    @staticmethod
    @contextmanager
    def _manufacturing_process_lock(job_dir: Path):
        """Serialize review transitions across processes for one persisted job."""

        lock_path = job_dir / ".manufacturing-review.lock"
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _require_new_artifacts(*paths: Path) -> None:
        existing = [path.name for path in paths if path.exists()]
        if existing:
            raise RuntimeError(
                "Manufacturing review artifacts are append-only; already present: "
                + ", ".join(existing)
            )

    @staticmethod
    def _enum_value(value) -> str:
        return str(getattr(value, "value", value))

    def validate(self, spec: CadDocument):
        return self.validator.validate(spec)

    def get(self, job_id: str) -> JobManifest | None:
        value = self.storage.read_manifest(job_id)
        return JobManifest.model_validate(value) if value else None

    def mark_cancelled(self, manifest: JobManifest) -> JobManifest:
        if manifest.status == "cancelled":
            return manifest
        warning = "Cancelled by user request."
        updated = manifest.model_copy(
            update={
                "status": "cancelled",
                "error": warning,
                "warnings": [*manifest.warnings, warning],
                "completed_at": datetime.now(UTC),
            }
        )
        self.storage.write_json(
            self.storage.path(updated.job_id) / "manifest.json",
            updated.model_dump(mode="json"),
        )
        return updated

    def list(self) -> list[JobListItem]:
        items = []
        for value in self.storage.list_manifests(self.settings.max_jobs_returned):
            try:
                manifest = JobManifest.model_validate(value)
            except ValueError:
                continue
            items.append(
                JobListItem(
                    job_id=manifest.job_id,
                    status=manifest.status,
                    created_at=manifest.created_at,
                    prompt=manifest.prompt,
                    name=manifest.spec.name,
                    renderer_used=manifest.renderer_used,
                )
            )
        return items

    @staticmethod
    def _artifacts(job_id: str, job_dir: Path) -> list[Artifact]:
        artifacts = []
        for path in sorted(job_dir.iterdir()):
            if not path.is_file() or path.name == "manifest.json" or path.name.startswith(".tmp-"):
                continue
            media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            artifacts.append(
                Artifact(
                    filename=path.name,
                    media_type=media_type,
                    size=path.stat().st_size,
                    url=f"/api/v1/jobs/{job_id}/files/{path.name}",
                    sha256=JobService._sha256_file(path),
                )
            )
        return artifacts

    @staticmethod
    def _format_results(
        requested_formats: list[str],
        artifacts: list[Artifact],
        status: str,
        *,
        source_backend: str,
    ) -> list[FormatResult]:
        names = {artifact.filename for artifact in artifacts}
        expected = {
            "step": "model.step",
            "stl": "model.stl",
            "dxf": "model.dxf",
            "svg": "model.svg",
            "pdf": "drawing.pdf",
            "py": {
                "build123d": "model.build123d.py",
                "freecad": "model.freecad.py",
                "fusion360": "model.fusion360.py",
                "solidworks": "model.solidworks.py",
            }.get(source_backend, "model.py"),
            "scad": "model.scad",
            "json": "spec.json",
        }
        results: list[FormatResult] = []
        for fmt in dict.fromkeys(requested_formats):
            filename = expected[fmt]
            if filename in names:
                results.append(
                    FormatResult(
                        format=fmt,
                        status="produced",
                        filename=filename,
                    )
                )
            elif status == "failed":
                results.append(
                    FormatResult(
                        format=fmt,
                        status="failed",
                        reason="Job failed before this artifact was produced.",
                    )
                )
            elif status == "source_only":
                results.append(
                    FormatResult(
                        format=fmt,
                        status="source_only",
                        reason="No compatible server-side CAD runtime produced this format.",
                    )
                )
            elif status == "cancelled":
                results.append(
                    FormatResult(
                        format=fmt,
                        status="cancelled",
                        reason="Job was cancelled before this artifact was produced.",
                    )
                )
            else:
                results.append(
                    FormatResult(
                        format=fmt,
                        status="unavailable",
                        reason="The selected backend does not produce this format.",
                    )
                )
        return results

    @staticmethod
    def _merge_fallback_chains(
        selected: list[str],
        runtime: list[str],
    ) -> list[str]:
        if not runtime:
            return selected
        merged = list(selected)
        start = 1 if merged and merged[-1] == runtime[0] else 0
        for backend_id in runtime[start:]:
            if not merged or merged[-1] != backend_id:
                merged.append(backend_id)
        return merged

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

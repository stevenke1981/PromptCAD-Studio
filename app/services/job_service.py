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
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
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
    PlanResponse,
)
from app.models.cad import CadDocument
from app.models.dxf import DxfAnalysisResponse, DxfFeatureTreeNode
from app.models.image import FeatureTreeNode, ImageAnalysisResponse
from app.services.backends import BACKEND_CONTRACT_VERSION, default_backend_registry
from app.services.cancellation import CancelCheck, JobCancelled
from app.services.drawing_pdf import EngineeringDrawingPdf
from app.services.dxf_analysis import DxfAnalysisError, DxfFeatureExtractor
from app.services.image_analysis import ImageFeatureExtractor
from app.services.planners.factory import PlannerFactory
from app.services.preview import SvgPreview
from app.services.renderer import Renderer
from app.services.storage import JobStorage
from app.services.validator import DesignValidator


class JobService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.planners = PlannerFactory(settings)
        self.validator = DesignValidator()
        self.backends = default_backend_registry()
        self.drawing = EngineeringDrawingPdf()
        self.preview = SvgPreview()
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
    ) -> JobManifest:
        if cancel_check is not None and cancel_check():
            raise JobCancelled("Job cancelled before validation")
        validation = self.validator.validate(spec)
        return await self._materialize(
            spec=spec,
            validation=validation,
            prompt=spec.source_prompt,
            planner_used="manual-dsl",
            formats=formats,
            render=render,
            backend=backend,
            cancel_check=cancel_check,
        )

    async def analyze_image(
        self,
        data: bytes,
        *,
        known_length_mm: float,
        thickness_mm: float,
        perspective_correction: bool = False,
        page_index: int = 0,
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
        )

    async def analyze_upload(
        self,
        upload,
        *,
        known_length_mm: float,
        thickness_mm: float,
        perspective_correction: bool = False,
        page_index: int = 0,
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
        )

    async def _analyze_admitted(
        self,
        data: bytes,
        *,
        known_length_mm: float,
        thickness_mm: float,
        perspective_correction: bool,
        page_index: int,
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
    ) -> DxfAnalysisResponse:
        if len(data) > self.settings.max_dxf_bytes:
            raise ValueError(f"DXF exceeds the {self.settings.max_dxf_bytes} byte limit")
        await self._dxf_slots.acquire()
        try:
            return await self._analyze_dxf_admitted(
                data,
                thickness_mm=thickness_mm,
                unit_override=unit_override,
            )
        finally:
            self._dxf_slots.release()

    async def analyze_dxf_upload(
        self,
        upload,
        *,
        thickness_mm: float,
        unit_override: str = "auto",
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
            )
        finally:
            self._dxf_slots.release()

    async def _analyze_dxf_admitted(
        self,
        data: bytes,
        *,
        thickness_mm: float,
        unit_override: str,
    ) -> DxfAnalysisResponse:
        if unit_override not in {"auto", "mm", "inch", "cm"}:
            raise ValueError("DXF units must be auto, mm, inch, or cm")
        result = await asyncio.to_thread(
            self._run_dxf_worker,
            data,
            thickness_mm,
            unit_override,
        )
        return self._sign_analysis(result)

    def _run_dxf_worker(
        self,
        data: bytes,
        thickness_mm: float,
        unit_override: str,
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
            self.drawing.write(spec, job_dir / "drawing.pdf")

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

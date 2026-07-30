from __future__ import annotations

import asyncio
import mimetypes
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings
from app.models.api import Artifact, JobListItem, JobManifest, PlanResponse
from app.models.cad import CadDocument
from app.services.compiler import CadQueryCompiler
from app.services.openscad import OpenScadCompiler
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
        self.compiler = CadQueryCompiler()
        self.openscad = OpenScadCompiler()
        self.preview = SvgPreview()
        self.storage = JobStorage(settings)
        self.renderer = Renderer(settings)

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
    ) -> JobManifest:
        plan = await self.plan(prompt, planner_choice)
        return await self._materialize(
            spec=plan.spec,
            validation=plan.validation,
            prompt=prompt,
            planner_used=plan.planner_used,
            formats=formats,
            render=render,
        )

    async def generate_from_spec(
        self,
        spec: CadDocument,
        formats: list[str],
        render: bool,
    ) -> JobManifest:
        validation = self.validator.validate(spec)
        return await self._materialize(
            spec=spec,
            validation=validation,
            prompt=spec.source_prompt,
            planner_used="manual-dsl",
            formats=formats,
            render=render,
        )

    async def _materialize(
        self,
        *,
        spec: CadDocument,
        validation,
        prompt: str,
        planner_used: str,
        formats: list[str],
        render: bool,
    ) -> JobManifest:
        job_id, job_dir = self.storage.create()
        created_at = datetime.now(UTC).isoformat()
        warnings: list[str] = []
        error: str | None = None
        renderer_used = "not-run"
        status = "source_only"

        self.storage.write_json(job_dir / "spec.json", spec.model_dump(mode="json"))
        self.storage.write_json(
            job_dir / "validation.json",
            validation.model_dump(mode="json"),
        )
        self.storage.write_text(job_dir / "model.py", self.compiler.compile(spec))
        self.storage.write_text(job_dir / "model.scad", self.openscad.compile(spec))
        self.preview.write(spec, job_dir / "preview.svg")

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
                result = await asyncio.to_thread(self.renderer.render, job_dir, formats)
                renderer_used = result.renderer
                status = result.status
                warnings.extend(result.warnings)
            except RuntimeError as exc:
                status = "failed"
                renderer_used = "failed"
                error = str(exc)
        else:
            renderer_used = "not-run"
            warnings.append("render=false：只產生 DSL 與 CAD 原始碼。")

        artifacts = self._artifacts(job_id, job_dir)
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
        )
        self.storage.write_json(job_dir / "manifest.json", manifest.model_dump(mode="json"))
        return manifest

    def validate(self, spec: CadDocument):
        return self.validator.validate(spec)

    def get(self, job_id: str) -> JobManifest | None:
        value = self.storage.read_manifest(job_id)
        return JobManifest.model_validate(value) if value else None

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
                )
            )
        return artifacts

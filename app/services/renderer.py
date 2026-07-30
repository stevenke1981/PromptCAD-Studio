from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import Settings


@dataclass(slots=True)
class RenderResult:
    renderer: str
    status: str
    files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Renderer:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def cadquery_available() -> bool:
        return importlib.util.find_spec("cadquery") is not None

    @staticmethod
    def openscad_available() -> bool:
        return shutil.which("openscad") is not None

    def render(self, job_dir: Path, formats: list[str]) -> RenderResult:
        requested = set(formats)
        backend = self._choose_backend()
        warnings: list[str] = []

        if backend == "cadquery":
            cmd = [
                sys.executable,
                str(job_dir / "model.py"),
                "--output-dir",
                str(job_dir),
                "--formats",
                *sorted(requested & {"step", "stl", "dxf", "svg"}),
            ]
            try:
                self._run(cmd, job_dir)
                warnings.extend(self._read_render_warnings(job_dir))
                return RenderResult(
                    renderer="cadquery",
                    status="completed",
                    files=self._existing_outputs(job_dir, requested),
                    warnings=warnings,
                )
            except RuntimeError as exc:
                if not self.settings.allow_source_fallback:
                    raise
                warnings.append(str(exc))
                backend = "openscad" if self.openscad_available() else "source_only"

        if backend == "openscad":
            if "stl" in requested:
                try:
                    self._run(
                        ["openscad", "-o", str(job_dir / "model.stl"), str(job_dir / "model.scad")],
                        job_dir,
                    )
                except RuntimeError as exc:
                    if not self.settings.allow_source_fallback:
                        raise
                    warnings.append(str(exc))
                    backend = "source_only"
            unsupported = requested & {"step", "dxf"}
            if unsupported:
                warnings.append("OpenSCAD fallback 無法輸出: " + ", ".join(sorted(unsupported)))
            return RenderResult(
                renderer="openscad" if backend == "openscad" else "source_only",
                status="completed" if backend == "openscad" else "source_only",
                files=self._existing_outputs(job_dir, requested),
                warnings=warnings,
            )

        missing = requested & {"step", "stl", "dxf"}
        if missing:
            warnings.append(
                "目前環境沒有 CadQuery/OpenSCAD；已保留可執行 model.py、model.scad 與預覽，未生成: "
                + ", ".join(sorted(missing))
            )
        return RenderResult(
            renderer="source_only",
            status="source_only",
            files=self._existing_outputs(job_dir, requested),
            warnings=warnings,
        )

    def _choose_backend(self) -> str:
        configured = self.settings.render_backend
        if configured == "source_only":
            return configured
        if configured == "cadquery":
            if self.cadquery_available():
                return configured
            if not self.settings.allow_source_fallback:
                raise RuntimeError("CadQuery backend requested but cadquery is not installed")
            return "openscad" if self.openscad_available() else "source_only"
        if configured == "openscad":
            if self.openscad_available():
                return configured
            if not self.settings.allow_source_fallback:
                raise RuntimeError("OpenSCAD backend requested but openscad is not installed")
            return "source_only"
        if self.cadquery_available():
            return "cadquery"
        if self.openscad_available():
            return "openscad"
        return "source_only"

    def _run(self, command: list[str], cwd: Path) -> None:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self.settings.render_timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"CAD renderer timed out after {self.settings.render_timeout_seconds}s") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown error")[-4000:]
            raise RuntimeError(f"CAD renderer failed ({completed.returncode}): {detail}")

    @staticmethod
    def _read_render_warnings(job_dir: Path) -> list[str]:
        path = job_dir / "render-warnings.json"
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [str(item) for item in data]
        except (json.JSONDecodeError, OSError):
            return []

    @staticmethod
    def _existing_outputs(job_dir: Path, requested: set[str]) -> list[Path]:
        fixed = [job_dir / "spec.json", job_dir / "validation.json", job_dir / "model.py", job_dir / "model.scad", job_dir / "preview.svg"]
        extension_map = {"step": "model.step", "stl": "model.stl", "dxf": "model.dxf", "svg": "model.svg"}
        for fmt, filename in extension_map.items():
            if fmt in requested:
                fixed.append(job_dir / filename)
        return [path for path in fixed if path.is_file()]

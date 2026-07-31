from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import Settings
from app.services.backends import ocp_runtime_conflicted
from app.services.cancellation import CancelCheck, JobCancelled


@dataclass(slots=True)
class RenderResult:
    renderer: str
    status: str
    files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fallback_chain: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


class Renderer:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def cadquery_available() -> bool:
        return (
            not ocp_runtime_conflicted()
            and importlib.util.find_spec("cadquery") is not None
        )

    @staticmethod
    def openscad_available() -> bool:
        return shutil.which("openscad") is not None

    @staticmethod
    def build123d_available() -> bool:
        return (
            not ocp_runtime_conflicted()
            and importlib.util.find_spec("build123d") is not None
        )

    def render(
        self,
        job_dir: Path,
        formats: list[str],
        backend_override: str | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> RenderResult:
        if cancel_check is not None and cancel_check():
            raise JobCancelled("CAD rendering cancelled before start")
        job_dir = job_dir.resolve()
        requested = set(formats)
        backend = self._choose_backend(backend_override)
        warnings: list[str] = []
        fallback_chain = [backend]

        if backend in {"cadquery", "build123d"}:
            script_name = (
                "model.py" if backend == "cadquery" else "model.build123d.py"
            )
            supported = (
                {"step", "stl", "dxf", "svg"}
                if backend == "cadquery"
                else {"step", "stl"}
            )
            render_formats = requested & supported
            requested_kernel = requested & {"step", "stl", "dxf", "svg"}
            if not render_formats:
                if requested_kernel:
                    message = (
                        f"{backend} 無法產生要求的核心格式："
                        + ", ".join(sorted(requested_kernel))
                    )
                    if not self.settings.allow_source_fallback:
                        raise RuntimeError(message)
                    return RenderResult(
                        renderer="source_only",
                        status="source_only",
                        files=self._existing_outputs(job_dir, requested),
                        warnings=[message],
                        fallback_chain=[*fallback_chain, "source_only"],
                        diagnostics=[message],
                    )
                return RenderResult(
                    renderer=backend,
                    status="completed",
                    files=self._existing_outputs(job_dir, requested),
                    fallback_chain=fallback_chain,
                )
            try:
                with tempfile.TemporaryDirectory(
                    prefix=".render-",
                    dir=job_dir,
                ) as staging_value:
                    staging = Path(staging_value).resolve()
                    cmd = [
                        str(Path(sys.executable).resolve()),
                        str(job_dir / script_name),
                        "--output-dir",
                        str(staging),
                        "--formats",
                        *sorted(render_formats),
                    ]
                    if cancel_check is None:
                        self._run(cmd, staging)
                    else:
                        self._run(cmd, staging, cancel_check)
                    warnings.extend(self._read_render_warnings(staging))
                    self._promote_outputs(
                        staging,
                        job_dir,
                        render_formats,
                    )
                return RenderResult(
                    renderer=backend,
                    status="completed",
                    files=self._existing_outputs(job_dir, requested),
                    warnings=warnings,
                    fallback_chain=fallback_chain,
                )
            except JobCancelled:
                raise
            except RuntimeError as exc:
                if not self.settings.allow_source_fallback:
                    raise
                warnings.append(str(exc))
                can_use_openscad = (
                    backend == "cadquery"
                    and self.openscad_available()
                    and bool(requested_kernel)
                    and requested_kernel <= {"stl"}
                )
                backend = "openscad" if can_use_openscad else "source_only"
                fallback_chain.append(backend)

        if backend == "openscad":
            requested_kernel = requested & {"step", "stl", "dxf", "svg"}
            if requested_kernel and "stl" not in requested_kernel:
                message = (
                    "OpenSCAD 無法產生要求的核心格式："
                    + ", ".join(sorted(requested_kernel))
                )
                if not self.settings.allow_source_fallback:
                    raise RuntimeError(message)
                return RenderResult(
                    renderer="source_only",
                    status="source_only",
                    files=self._existing_outputs(job_dir, requested),
                    warnings=[message],
                    fallback_chain=(
                        fallback_chain
                        if fallback_chain[-1] == "source_only"
                        else [*fallback_chain, "source_only"]
                    ),
                    diagnostics=[message],
                )
            if "stl" in requested:
                try:
                    executable = shutil.which("openscad")
                    if executable is None:
                        raise RuntimeError("OpenSCAD executable disappeared")
                    with tempfile.TemporaryDirectory(
                        prefix=".render-",
                        dir=job_dir,
                    ) as staging_value:
                        staging = Path(staging_value).resolve()
                        command = [
                            str(Path(executable).resolve()),
                            "-o",
                            str(staging / "model.stl"),
                            str(job_dir / "model.scad"),
                        ]
                        if cancel_check is None:
                            self._run(command, staging)
                        else:
                            self._run(command, staging, cancel_check)
                        self._promote_outputs(staging, job_dir, {"stl"})
                except JobCancelled:
                    raise
                except RuntimeError as exc:
                    if not self.settings.allow_source_fallback:
                        raise
                    warnings.append(str(exc))
                    backend = "source_only"
                    if fallback_chain[-1] != "source_only":
                        fallback_chain.append("source_only")
            unsupported = requested & {"step", "dxf", "svg"}
            if unsupported:
                warnings.append("OpenSCAD fallback 無法輸出: " + ", ".join(sorted(unsupported)))
            return RenderResult(
                renderer="openscad" if backend == "openscad" else "source_only",
                status="completed" if backend == "openscad" else "source_only",
                files=self._existing_outputs(job_dir, requested),
                warnings=warnings,
                fallback_chain=fallback_chain,
                diagnostics=warnings.copy(),
            )

        missing = requested & {"step", "stl", "dxf"}
        if missing:
            warnings.append(
                "目前沒有相容的伺服器 CAD runtime；已保留多後端來源與預覽，未生成: "
                + ", ".join(sorted(missing))
            )
        return RenderResult(
            renderer="source_only",
            status="source_only",
            files=self._existing_outputs(job_dir, requested),
            warnings=warnings,
            fallback_chain=(
                fallback_chain
                if fallback_chain[-1] == "source_only"
                else [*fallback_chain, "source_only"]
            ),
            diagnostics=warnings.copy(),
        )

    def _choose_backend(self, backend_override: str | None = None) -> str:
        configured = backend_override or self.settings.render_backend
        if configured == "source_only":
            return configured
        if configured == "cadquery":
            if self.cadquery_available():
                return configured
            if not self.settings.allow_source_fallback:
                raise RuntimeError("CadQuery backend requested but cadquery is not installed")
            return "openscad" if self.openscad_available() else "source_only"
        if configured == "build123d":
            if self.build123d_available():
                return configured
            if not self.settings.allow_source_fallback:
                raise RuntimeError(
                    "Build123d backend requested but build123d is not installed"
                )
            return "source_only"
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

    def _run(
        self,
        command: list[str],
        cwd: Path,
        cancel_check: CancelCheck | None = None,
    ) -> None:
        child_env = {
            key: os.environ[key]
            for key in (
                "LANG",
                "LC_ALL",
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
            )
            if key in os.environ
        }
        private_home = cwd / ".runtime-home"
        private_home.mkdir(parents=True, exist_ok=True)
        private_temp = cwd / ".runtime-temp"
        private_temp.mkdir(parents=True, exist_ok=True)
        child_env.update(
            {
                "APPDATA": str(private_home),
                "HOME": str(private_home),
                "LOCALAPPDATA": str(private_home),
                "TEMP": str(private_temp),
                "TMP": str(private_temp),
                "USERPROFILE": str(private_home),
                "XDG_CONFIG_HOME": str(private_home),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
            }
        )
        stdout_path = cwd / ".renderer-stdout"
        stderr_path = cwd / ".renderer-stderr"
        popen_options: dict[str, object] = {}
        if os.name == "nt":
            popen_options["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW
                | 0x00000004  # CREATE_SUSPENDED
            )
        else:
            popen_options["start_new_session"] = True
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                env=child_env,
                **popen_options,
            )
            windows_job: int | None = None
            try:
                if os.name == "nt":
                    windows_job = self._start_windows_job(process)
                started = time.monotonic()
                failure: str | None = None
                while process.poll() is None:
                    if cancel_check is not None and cancel_check():
                        self._terminate_process_tree(process, windows_job)
                        windows_job = None
                        raise JobCancelled("CAD rendering cancelled")
                    if (
                        time.monotonic() - started
                        > self.settings.render_timeout_seconds
                    ):
                        failure = (
                            "CAD renderer timed out after "
                            f"{self.settings.render_timeout_seconds}s"
                        )
                        break
                    if (
                        stdout_path.stat().st_size + stderr_path.stat().st_size
                        > self.settings.max_renderer_output_chars
                    ):
                        failure = "CAD renderer produced oversized console output"
                        break
                    time.sleep(0.05)
                if failure is not None:
                    self._terminate_process_tree(process, windows_job)
                    windows_job = None
                    raise RuntimeError(failure)
                returncode = process.wait()
            finally:
                if windows_job is not None:
                    self._close_windows_job(windows_job)
        try:
            total_output = stdout_path.stat().st_size + stderr_path.stat().st_size
            if total_output > self.settings.max_renderer_output_chars:
                raise RuntimeError("CAD renderer produced oversized console output")
            stdout_text = self._read_bounded_text(
                stdout_path, self.settings.max_renderer_output_chars
            )
            stderr_text = self._read_bounded_text(
                stderr_path, self.settings.max_renderer_output_chars
            )
            if returncode != 0:
                detail = (stderr_text or stdout_text or "unknown error")[-4000:]
                raise RuntimeError(f"CAD renderer failed ({returncode}): {detail}")
        finally:
            stdout_path.unlink(missing_ok=True)
            stderr_path.unlink(missing_ok=True)

    @staticmethod
    def _start_windows_job(process: subprocess.Popen) -> int:
        import ctypes
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            process.kill()
            process.wait()
            raise RuntimeError(
                f"Could not create renderer Job Object: {ctypes.WinError()}"
            )
        job_value = int(job)
        try:
            information = ExtendedLimitInformation()
            information.BasicLimitInformation.LimitFlags = 0x00002000
            if not kernel32.SetInformationJobObject(
                job,
                9,
                ctypes.byref(information),
                ctypes.sizeof(information),
            ):
                raise RuntimeError(
                    f"Could not configure renderer Job Object: {ctypes.WinError()}"
                )
            process_handle = wintypes.HANDLE(int(process._handle))
            if not kernel32.AssignProcessToJobObject(job, process_handle):
                raise RuntimeError(
                    f"Could not isolate renderer process: {ctypes.WinError()}"
                )
            status = ntdll.NtResumeProcess(process_handle)
            if status != 0:
                raise RuntimeError(
                    "Could not resume isolated renderer process "
                    f"(NTSTATUS=0x{status & 0xFFFFFFFF:08x})"
                )
            return job_value
        except BaseException:
            Renderer._close_windows_job(job_value)
            if process.poll() is None:
                process.kill()
            process.wait()
            raise

    @staticmethod
    def _close_windows_job(job: int) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle(wintypes.HANDLE(job))

    @staticmethod
    def _terminate_process_tree(
        process: subprocess.Popen,
        windows_job: int | None = None,
    ) -> None:
        if os.name == "nt":
            if windows_job is not None:
                Renderer._close_windows_job(windows_job)
            elif process.poll() is None:
                process.kill()
        elif process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    @staticmethod
    def _read_bounded_text(path: Path, limit: int) -> str:
        with path.open("rb") as stream:
            return stream.read(limit + 1).decode("utf-8", errors="replace")

    def _promote_outputs(
        self,
        staging: Path,
        job_dir: Path,
        requested: set[str],
    ) -> None:
        filenames = {
            "step": "model.step",
            "stl": "model.stl",
            "dxf": "model.dxf",
            "svg": "model.svg",
        }
        total = 0
        candidates: list[Path] = []
        for fmt in requested:
            path = staging / filenames[fmt]
            if not path.exists():
                raise RuntimeError(f"CAD renderer did not produce requested {fmt} artifact")
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(f"CAD renderer returned an unsafe {fmt} artifact")
            resolved = path.resolve()
            if staging not in resolved.parents:
                raise RuntimeError("CAD renderer artifact escaped staging")
            size = path.stat().st_size
            if size <= 0 or size > self.settings.max_render_artifact_bytes:
                raise RuntimeError(f"CAD renderer {fmt} artifact has an invalid size")
            self._verify_signature(path, fmt)
            total += size
            if total > self.settings.max_render_total_bytes:
                raise RuntimeError("CAD renderer artifacts exceed the total size limit")
            candidates.append(path)
        warning_path = staging / "render-warnings.json"
        if warning_path.is_file() and not warning_path.is_symlink():
            warning_size = warning_path.stat().st_size
            if warning_size > min(65_536, self.settings.max_render_artifact_bytes):
                raise RuntimeError("CAD renderer warnings artifact is oversized")
            total += warning_size
            if total > self.settings.max_render_total_bytes:
                raise RuntimeError("CAD renderer artifacts exceed the total size limit")
            candidates.append(warning_path)
        for source in candidates:
            destination = job_dir / source.name
            with tempfile.NamedTemporaryFile(
                prefix=".tmp-render-",
                dir=job_dir,
                delete=False,
            ) as stream:
                temp_path = Path(stream.name)
            try:
                shutil.copyfile(source, temp_path)
                os.replace(temp_path, destination)
            finally:
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _verify_signature(path: Path, fmt: str) -> None:
        with path.open("rb") as stream:
            head = stream.read(1024)
        if fmt == "step" and b"ISO-10303-21" not in head:
            raise RuntimeError("CAD renderer returned an invalid STEP artifact")
        if fmt == "svg" and b"<svg" not in head.lower():
            raise RuntimeError("CAD renderer returned an invalid SVG artifact")
        if fmt == "dxf" and b"SECTION" not in head.upper():
            raise RuntimeError("CAD renderer returned an invalid DXF artifact")
        if fmt == "stl":
            size = path.stat().st_size
            binary_valid = False
            if len(head) >= 84:
                triangle_count = struct.unpack("<I", head[80:84])[0]
                binary_valid = triangle_count > 0 and size == 84 + triangle_count * 50
            if not binary_valid:
                with path.open("rb") as stream:
                    stream.seek(max(0, size - 256))
                    tail = stream.read(256).lower()
                ascii_valid = (
                    head.lstrip().lower().startswith(b"solid")
                    and b"facet normal" in head.lower()
                    and b"endsolid" in tail
                )
                if not ascii_valid:
                    raise RuntimeError("CAD renderer returned an invalid STL artifact")

    @staticmethod
    def _read_render_warnings(job_dir: Path) -> list[str]:
        path = job_dir / "render-warnings.json"
        if not path.is_file():
            return []
        if path.is_symlink() or path.stat().st_size > 65_536:
            raise RuntimeError("CAD renderer warnings artifact is unsafe or oversized")
        try:
            with path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError("CAD renderer warnings artifact is invalid") from exc
        if not isinstance(data, list) or len(data) > 256:
            raise RuntimeError("CAD renderer warnings artifact has an invalid structure")
        return [str(item)[:1_000] for item in data]

    @staticmethod
    def _existing_outputs(job_dir: Path, requested: set[str]) -> list[Path]:
        fixed = [job_dir / "spec.json", job_dir / "validation.json", job_dir / "model.py", job_dir / "model.scad", job_dir / "preview.svg"]
        extension_map = {
            "step": "model.step",
            "stl": "model.stl",
            "dxf": "model.dxf",
            "svg": "model.svg",
            "pdf": "drawing.pdf",
        }
        for fmt, filename in extension_map.items():
            if fmt in requested:
                fixed.append(job_dir / filename)
        return [path for path in fixed if path.is_file()]

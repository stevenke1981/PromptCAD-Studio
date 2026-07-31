from __future__ import annotations

import importlib.util
import re
import shutil
from dataclasses import dataclass
from importlib import metadata
from types import MappingProxyType
from typing import Protocol

from app.models.api import BackendCapability, BackendDiagnostic
from app.models.cad import CadDocument
from app.services.compiler import CadQueryCompiler
from app.services.openscad import OpenScadCompiler

BACKEND_CONTRACT_VERSION = "1.0"
BACKEND_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
SCHEMA_VERSIONS = ("1.0", "1.1", "1.2")
BASE_FEATURES = (
    "plate",
    "cylinder",
    "ring",
    "l_bracket",
    "enclosure",
    "profile_extrusion",
    "profile_revolution",
)
FEATURE_TYPES = (
    "hole",
    "rectangular_cutout",
    "fillet",
    "chamfer",
)


def ocp_runtime_conflicted() -> bool:
    installed = set()
    for distribution in ("cadquery-ocp", "cadquery-ocp-novtk"):
        try:
            metadata.version(distribution)
        except metadata.PackageNotFoundError:
            continue
        installed.add(distribution)
    return len(installed) > 1


class SourceCompiler(Protocol):
    def compile(self, doc: CadDocument) -> str: ...


@dataclass(frozen=True, slots=True)
class SourceArtifact:
    backend_id: str
    filename: str
    media_type: str
    content: str


@dataclass(frozen=True, slots=True)
class BackendRegistration:
    backend_id: str
    display_name: str
    compiler_version: str
    execution_kind: str
    source_compiler: SourceCompiler | None
    source_filename: str | None
    export_formats: tuple[str, ...]
    server_render_formats: tuple[str, ...]
    semantic_fidelity: str
    local_execution_supported: bool
    runtime_probe: str


@dataclass(frozen=True, slots=True)
class BackendSelection:
    requested: str
    effective: str
    fallback_chain: tuple[str, ...]
    diagnostics: tuple[BackendDiagnostic, ...]


class BackendRegistry:
    """Closed, server-owned backend allowlist.

    Requests can select only a registered short ID. They cannot provide import
    paths, executables, arguments, environment variables, or plugin metadata.
    """

    def __init__(self, registrations: list[BackendRegistration]):
        values: dict[str, BackendRegistration] = {}
        for registration in registrations:
            backend_id = registration.backend_id
            if not BACKEND_ID_PATTERN.fullmatch(backend_id):
                raise ValueError(f"Invalid backend id: {backend_id!r}")
            if backend_id in values:
                raise ValueError(f"Duplicate backend id: {backend_id}")
            values[backend_id] = registration
        if "cadquery" not in values or "openscad" not in values:
            raise ValueError("CadQuery and OpenSCAD registrations are required")
        self._registrations = MappingProxyType(values)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._registrations)

    def get(self, backend_id: str) -> BackendRegistration:
        if not BACKEND_ID_PATTERN.fullmatch(backend_id):
            raise ValueError("Unknown CAD backend")
        try:
            return self._registrations[backend_id]
        except KeyError as exc:
            raise ValueError(f"Unknown CAD backend: {backend_id}") from exc

    def compile_sources(
        self,
        doc: CadDocument,
    ) -> tuple[list[SourceArtifact], list[BackendDiagnostic]]:
        artifacts: list[SourceArtifact] = []
        diagnostics: list[BackendDiagnostic] = []
        for registration in self._registrations.values():
            if registration.source_compiler is None or registration.source_filename is None:
                continue
            try:
                content = registration.source_compiler.compile(doc)
            except (TypeError, ValueError) as exc:
                diagnostics.append(
                    BackendDiagnostic(
                        backend_id=registration.backend_id,
                        severity="warning",
                        code="source_compile_skipped",
                        message=(
                            f"{registration.display_name} 原始碼已略過：{exc}"
                        ),
                    )
                )
                continue
            artifacts.append(
                SourceArtifact(
                    backend_id=registration.backend_id,
                    filename=registration.source_filename,
                    media_type="text/x-python"
                    if registration.source_filename.endswith(".py")
                    else "text/plain",
                    content=content,
                )
            )
        return artifacts, diagnostics

    def capabilities(self) -> list[BackendCapability]:
        return [self._capability(value) for value in self._registrations.values()]

    def select(
        self,
        requested: str,
        *,
        doc: CadDocument,
        formats: list[str],
        render: bool,
        allow_source_fallback: bool,
    ) -> BackendSelection:
        if requested not in {"auto", "source_only"}:
            registration = self.get(requested)
            diagnostics = self.support_diagnostics(registration, doc, formats)
            if any(item.severity == "error" for item in diagnostics):
                if not allow_source_fallback:
                    raise ValueError(diagnostics[0].message)
                return BackendSelection(
                    requested=requested,
                    effective="source_only",
                    fallback_chain=(requested, "source_only"),
                    diagnostics=tuple(diagnostics),
                )
            if not render and registration.execution_kind != "host_application":
                return BackendSelection(
                    requested=requested,
                    effective="source_only",
                    fallback_chain=(requested, "source_only"),
                    diagnostics=tuple(diagnostics),
                )
            if registration.execution_kind == "host_application":
                if not render:
                    diagnostics.append(
                        BackendDiagnostic(
                            backend_id=requested,
                            severity="warning",
                            code="neutral_step_not_generated",
                            message=(
                                f"{registration.display_name} adapter 需要同工作包的 "
                                "model.step；render=false 因此只輸出來源。"
                            ),
                        )
                    )
                    return BackendSelection(
                        requested=requested,
                        effective="source_only",
                        fallback_chain=(requested, "source_only"),
                        diagnostics=tuple(diagnostics),
                    )
                for bridge_id in ("cadquery", "build123d"):
                    bridge = self.get(bridge_id)
                    bridge_support = self.support_diagnostics(bridge, doc, ["step"])
                    if any(item.severity == "error" for item in bridge_support):
                        continue
                    if self._runtime_available(bridge):
                        diagnostics.extend(bridge_support)
                        diagnostics.append(
                            BackendDiagnostic(
                                backend_id=requested,
                                severity="warning",
                                code="neutral_step_bridge",
                                message=(
                                    f"{registration.display_name} 不在伺服器執行；"
                                    f"工作包將由 {bridge.display_name} 產生已驗證的 "
                                    "model.step，供桌面 adapter 匯入。"
                                ),
                            )
                        )
                        return BackendSelection(
                            requested=requested,
                            effective=bridge_id,
                            fallback_chain=(requested, bridge_id),
                            diagnostics=tuple(diagnostics),
                        )
                diagnostic = BackendDiagnostic(
                    backend_id=requested,
                    severity="warning",
                    code="neutral_step_runtime_missing",
                    message=(
                        f"{registration.display_name} adapter 需要 model.step，"
                        "但目前沒有相容的本機 STEP runtime；已降級為來源輸出。"
                    ),
                )
                if not allow_source_fallback:
                    raise ValueError(diagnostic.message)
                return BackendSelection(
                    requested=requested,
                    effective="source_only",
                    fallback_chain=(requested, "source_only"),
                    diagnostics=tuple([*diagnostics, diagnostic]),
                )
            if registration.local_execution_supported and self._runtime_available(
                registration
            ):
                return BackendSelection(
                    requested=requested,
                    effective=requested,
                    fallback_chain=(requested,),
                    diagnostics=tuple(diagnostics),
                )
            diagnostic = self._unavailable_diagnostic(registration)
            if not allow_source_fallback:
                raise ValueError(diagnostic.message)
            return BackendSelection(
                requested=requested,
                effective="source_only",
                fallback_chain=(requested, "source_only"),
                diagnostics=tuple([*diagnostics, diagnostic]),
            )

        if requested == "source_only" or not render:
            return BackendSelection(
                requested=requested,
                effective="source_only",
                fallback_chain=("source_only",),
                diagnostics=(),
            )

        chain: list[str] = []
        diagnostics: list[BackendDiagnostic] = []
        for backend_id in ("cadquery", "openscad"):
            registration = self.get(backend_id)
            chain.append(backend_id)
            support = self.support_diagnostics(registration, doc, formats)
            diagnostics.extend(support)
            if any(item.severity == "error" for item in support):
                continue
            if self._runtime_available(registration):
                return BackendSelection(
                    requested="auto",
                    effective=backend_id,
                    fallback_chain=tuple(chain),
                    diagnostics=tuple(diagnostics),
                )
            diagnostics.append(self._unavailable_diagnostic(registration))
        chain.append("source_only")
        if not allow_source_fallback:
            raise ValueError("No compatible CAD runtime is available")
        return BackendSelection(
            requested="auto",
            effective="source_only",
            fallback_chain=tuple(chain),
            diagnostics=tuple(diagnostics),
        )

    def support_diagnostics(
        self,
        registration: BackendRegistration,
        doc: CadDocument,
        formats: list[str],
    ) -> list[BackendDiagnostic]:
        diagnostics: list[BackendDiagnostic] = []
        requested_kernel_formats = set(formats) & {"step", "stl", "dxf", "svg"}
        unsupported = requested_kernel_formats - set(registration.server_render_formats)
        if unsupported and registration.local_execution_supported:
            diagnostics.append(
                BackendDiagnostic(
                    backend_id=registration.backend_id,
                    severity="warning",
                    code="format_unavailable",
                    message=(
                        f"{registration.display_name} 伺服器執行器無法產生："
                        + ", ".join(sorted(unsupported))
                    ),
                )
            )
        if registration.backend_id == "openscad" and (doc.fillets or doc.chamfers):
            diagnostics.append(
                BackendDiagnostic(
                    backend_id="openscad",
                    severity="error",
                    code="lossy_feature_unsupported",
                    message="OpenSCAD 不會套用圓角或倒角；已阻止不完整幾何輸出。",
                )
            )
        if registration.execution_kind == "host_application":
            diagnostics.append(
                BackendDiagnostic(
                    backend_id=registration.backend_id,
                    severity="warning",
                    code="host_application_required",
                    message=(
                        f"{registration.display_name} adapter 必須由已安裝的桌面 CAD "
                        "主程式執行；伺服器只輸出受控腳本。"
                    ),
                )
            )
        return diagnostics

    def _capability(self, value: BackendRegistration) -> BackendCapability:
        available = self._runtime_available(value)
        reason = None
        if value.local_execution_supported and not available:
            reason = self._unavailable_diagnostic(value).message
        elif value.execution_kind == "host_application":
            reason = "需要在授權桌面 CAD 主程式內執行 adapter"
        return BackendCapability(
            backend_id=value.backend_id,
            display_name=value.display_name,
            compiler_version=value.compiler_version,
            contract_version=BACKEND_CONTRACT_VERSION,
            execution_kind=value.execution_kind,
            source_export_available=value.source_compiler is not None,
            local_execution_supported=value.local_execution_supported,
            runtime_available=available,
            schema_versions=list(SCHEMA_VERSIONS),
            base_features=list(BASE_FEATURES),
            feature_types=list(FEATURE_TYPES),
            export_formats=list(value.export_formats),
            server_render_formats=list(value.server_render_formats),
            source_filenames=[value.source_filename] if value.source_filename else [],
            semantic_fidelity=value.semantic_fidelity,
            unavailable_reason=reason,
        )

    @staticmethod
    def _runtime_available(registration: BackendRegistration) -> bool:
        if not registration.local_execution_supported:
            return False
        if registration.runtime_probe == "cadquery":
            return (
                not ocp_runtime_conflicted()
                and importlib.util.find_spec("cadquery") is not None
            )
        if registration.runtime_probe == "build123d":
            return (
                not ocp_runtime_conflicted()
                and importlib.util.find_spec("build123d") is not None
            )
        if registration.runtime_probe == "openscad":
            return shutil.which("openscad") is not None
        return False

    @staticmethod
    def _unavailable_diagnostic(
        registration: BackendRegistration,
    ) -> BackendDiagnostic:
        if registration.execution_kind == "host_application":
            code = "host_application_required"
            message = (
                f"{registration.display_name} 必須由桌面 CAD 主程式執行；"
                "伺服器已安全降級為來源輸出。"
            )
        elif registration.local_execution_supported:
            code = "runtime_missing"
            message = (
                f"{registration.display_name} runtime 未安裝；"
                "伺服器已安全降級為來源輸出。"
            )
        else:
            code = "source_export_only"
            message = f"{registration.display_name} 僅提供來源 adapter。"
        return BackendDiagnostic(
            backend_id=registration.backend_id,
            severity="warning",
            code=code,
            message=message,
        )


def default_backend_registry() -> BackendRegistry:
    # These imports are static and server-owned. No request can select a module
    # path or cause runtime plugin discovery.
    from app.services.build123d_compiler import Build123dCompiler
    from app.services.external_adapters import (
        Fusion360AdapterCompiler,
        SolidWorksAdapterCompiler,
    )
    from app.services.freecad_compiler import FreeCadCompiler

    return BackendRegistry(
        [
            BackendRegistration(
                backend_id="cadquery",
                display_name="CadQuery",
                compiler_version="2",
                execution_kind="local_process",
                source_compiler=CadQueryCompiler(),
                source_filename="model.py",
                export_formats=("step", "stl", "dxf", "svg"),
                server_render_formats=("step", "stl", "dxf", "svg"),
                semantic_fidelity="exact",
                local_execution_supported=True,
                runtime_probe="cadquery",
            ),
            BackendRegistration(
                backend_id="build123d",
                display_name="Build123d",
                compiler_version="1",
                execution_kind="local_process",
                source_compiler=Build123dCompiler(),
                source_filename="model.build123d.py",
                export_formats=("step", "stl"),
                server_render_formats=("step", "stl"),
                semantic_fidelity="exact",
                local_execution_supported=True,
                runtime_probe="build123d",
            ),
            BackendRegistration(
                backend_id="freecad",
                display_name="FreeCAD Python",
                compiler_version="1",
                execution_kind="none",
                source_compiler=FreeCadCompiler(),
                source_filename="model.freecad.py",
                export_formats=("step", "stl"),
                server_render_formats=(),
                semantic_fidelity="exact",
                local_execution_supported=False,
                runtime_probe="none",
            ),
            BackendRegistration(
                backend_id="openscad",
                display_name="OpenSCAD",
                compiler_version="2",
                execution_kind="local_process",
                source_compiler=OpenScadCompiler(),
                source_filename="model.scad",
                export_formats=("stl",),
                server_render_formats=("stl",),
                semantic_fidelity="approximated",
                local_execution_supported=True,
                runtime_probe="openscad",
            ),
            BackendRegistration(
                backend_id="fusion360",
                display_name="Autodesk Fusion 360 API",
                compiler_version="1",
                execution_kind="host_application",
                source_compiler=Fusion360AdapterCompiler(),
                source_filename="model.fusion360.py",
                export_formats=("f3d", "step"),
                server_render_formats=(),
                semantic_fidelity="neutral_step_bridge",
                local_execution_supported=False,
                runtime_probe="none",
            ),
            BackendRegistration(
                backend_id="solidworks",
                display_name="SOLIDWORKS API",
                compiler_version="1",
                execution_kind="host_application",
                source_compiler=SolidWorksAdapterCompiler(),
                source_filename="model.solidworks.py",
                export_formats=("sldprt", "step"),
                server_render_formats=(),
                semantic_fidelity="neutral_step_bridge",
                local_execution_supported=False,
                runtime_probe="none",
            ),
        ]
    )

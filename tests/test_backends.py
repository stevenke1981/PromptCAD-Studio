from __future__ import annotations

import pytest

from app.models.cad import (
    CadDocument,
    FilletFeature,
    PlannerMetadata,
    PlateBase,
)
from app.services.backends import (
    BackendRegistration,
    BackendRegistry,
    default_backend_registry,
)


def document(*, fillet: bool = False) -> CadDocument:
    return CadDocument(
        name="backend-contract",
        source_prompt="backend contract",
        base=PlateBase(length=80, width=40, thickness=5),
        fillets=[FilletFeature(radius=2)] if fillet else [],
        planner=PlannerMetadata(planner="test"),
    )


def registration(backend_id: str) -> BackendRegistration:
    return BackendRegistration(
        backend_id=backend_id,
        display_name=backend_id,
        compiler_version="1",
        execution_kind="none",
        source_compiler=None,
        source_filename=None,
        export_formats=(),
        server_render_formats=(),
        semantic_fidelity="exact",
        local_execution_supported=False,
        runtime_probe="none",
    )


@pytest.mark.parametrize(
    "backend_id",
    ["../../evil", "module:Class", r"C:\tool.exe", "/usr/bin/freecad", "A"],
)
def test_registry_rejects_unsafe_backend_ids(backend_id: str) -> None:
    with pytest.raises(ValueError, match="Invalid backend id"):
        BackendRegistry(
            [
                registration("cadquery"),
                registration("openscad"),
                registration(backend_id),
            ]
        )


def test_registry_rejects_duplicates_and_unknown_requests() -> None:
    with pytest.raises(ValueError, match="Duplicate backend id"):
        BackendRegistry(
            [
                registration("cadquery"),
                registration("openscad"),
                registration("cadquery"),
            ]
        )

    registry = default_backend_registry()
    for value in ("unknown", "../../evil", "module:Class"):
        with pytest.raises(ValueError, match="Unknown CAD backend"):
            registry.get(value)


def test_registry_is_closed_and_compiles_all_source_adapters() -> None:
    registry = default_backend_registry()
    sources, diagnostics = registry.compile_sources(document())

    assert diagnostics == []
    assert registry.ids == (
        "cadquery",
        "build123d",
        "freecad",
        "openscad",
        "fusion360",
        "solidworks",
    )
    assert {source.filename for source in sources} == {
        "model.py",
        "model.build123d.py",
        "model.freecad.py",
        "model.scad",
        "model.fusion360.py",
        "model.solidworks.py",
    }


def test_lossy_openscad_feature_fails_closed_before_runtime() -> None:
    registry = default_backend_registry()

    selection = registry.select(
        "openscad",
        doc=document(fillet=True),
        formats=["stl"],
        render=True,
        allow_source_fallback=True,
    )

    assert selection.effective == "source_only"
    assert selection.fallback_chain == ("openscad", "source_only")
    assert any(
        item.code == "lossy_feature_unsupported"
        for item in selection.diagnostics
    )


def test_host_application_adapter_uses_validated_neutral_step_bridge(
    monkeypatch,
) -> None:
    registry = default_backend_registry()
    monkeypatch.setattr(
        registry,
        "_runtime_available",
        lambda registration: registration.backend_id == "cadquery",
    )

    selection = registry.select(
        "solidworks",
        doc=document(),
        formats=["step"],
        render=True,
        allow_source_fallback=True,
    )

    assert selection.effective == "cadquery"
    assert selection.fallback_chain == ("solidworks", "cadquery")
    assert any(
        item.code == "neutral_step_bridge"
        for item in selection.diagnostics
    )


def test_host_application_render_false_reports_missing_neutral_step() -> None:
    registry = default_backend_registry()

    selection = registry.select(
        "fusion360",
        doc=document(),
        formats=["py", "json"],
        render=False,
        allow_source_fallback=True,
    )

    assert selection.effective == "source_only"
    assert selection.fallback_chain == ("fusion360", "source_only")
    assert any(
        item.code == "neutral_step_not_generated"
        for item in selection.diagnostics
    )


def test_exact_source_backends_do_not_silently_skip_finishing() -> None:
    sources, diagnostics = default_backend_registry().compile_sources(
        document(fillet=True)
    )

    assert diagnostics == []
    exact = {
        source.backend_id: source.content
        for source in sources
        if source.backend_id in {"cadquery", "build123d", "freecad"}
    }
    for content in exact.values():
        assert "fillet 1 skipped" not in content
        assert "fillet {index + 1} skipped" not in content
        assert "chamfer 1 skipped" not in content
        assert "chamfer {index + 1} skipped" not in content

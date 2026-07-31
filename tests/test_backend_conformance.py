from __future__ import annotations

import ast

import pytest

from app.models.cad import (
    ArcSegment2D,
    CadDocument,
    EnclosureBase,
    HoleFeature,
    LineSegment2D,
    PlannerMetadata,
    Point2D,
    ProfileExtrusionBase,
    ProfileLoop2D,
    RectangularCutoutFeature,
    RingBase,
    SideFace,
)
from app.services.backends import default_backend_registry


def conformance_documents() -> list[CadDocument]:
    planner = PlannerMetadata(planner="conformance")
    return [
        CadDocument(
            name="ring",
            source_prompt="ring",
            base=RingBase(outer_diameter=40, inner_diameter=20, height=6),
            planner=planner,
        ),
        CadDocument(
            name="enclosure-cutout",
            source_prompt="enclosure cutout",
            base=EnclosureBase(
                length=94,
                width=58,
                height=22,
                wall_thickness=2,
            ),
            holes=[HoleFeature(x=0, y=0, diameter=5)],
            cutouts=[
                RectangularCutoutFeature(
                    face=SideFace.POSITIVE_Y,
                    x=20,
                    z=9,
                    width=12,
                    height=8,
                )
            ],
            planner=planner,
        ),
        CadDocument(
            schema_version="1.1",
            name="line-arc-profile",
            source_prompt="line arc profile",
            base=ProfileExtrusionBase(
                thickness=6,
                outer=ProfileLoop2D(
                    segments=[
                        LineSegment2D(
                            start=Point2D(x=-20, y=-10),
                            end=Point2D(x=20, y=-10),
                        ),
                        ArcSegment2D(
                            start=Point2D(x=20, y=-10),
                            mid=Point2D(x=25, y=0),
                            end=Point2D(x=20, y=10),
                        ),
                        LineSegment2D(
                            start=Point2D(x=20, y=10),
                            end=Point2D(x=-20, y=10),
                        ),
                        LineSegment2D(
                            start=Point2D(x=-20, y=10),
                            end=Point2D(x=-20, y=-10),
                        ),
                    ]
                ),
            ),
            planner=planner,
        ),
    ]


@pytest.mark.parametrize("document", conformance_documents(), ids=lambda doc: doc.name)
def test_all_registered_source_backends_compile_same_validated_dsl(
    document: CadDocument,
) -> None:
    registry = default_backend_registry()

    first, first_diagnostics = registry.compile_sources(document)
    second, second_diagnostics = registry.compile_sources(document)

    assert first_diagnostics == second_diagnostics == []
    assert [(item.backend_id, item.content) for item in first] == [
        (item.backend_id, item.content) for item in second
    ]
    assert {item.backend_id for item in first} == {
        "cadquery",
        "build123d",
        "freecad",
        "openscad",
        "fusion360",
        "solidworks",
    }
    for source in first:
        if source.filename.endswith(".py"):
            ast.parse(source.content, filename=source.filename)


def test_capability_fidelity_is_explicit_and_never_claims_desktop_execution() -> None:
    capabilities = {
        item.backend_id: item
        for item in default_backend_registry().capabilities()
    }

    assert capabilities["cadquery"].semantic_fidelity == "exact"
    assert capabilities["build123d"].semantic_fidelity == "exact"
    assert capabilities["freecad"].semantic_fidelity == "exact"
    assert capabilities["openscad"].semantic_fidelity == "approximated"
    for backend_id in ("fusion360", "solidworks"):
        capability = capabilities[backend_id]
        assert capability.semantic_fidelity == "neutral_step_bridge"
        assert capability.execution_kind == "host_application"
        assert capability.local_execution_supported is False
        assert capability.runtime_available is False

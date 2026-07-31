from __future__ import annotations

import ast
import importlib.util

import pytest
from pydantic import ValidationError

from app.models.cad import (
    ArcSegment2D,
    CadDocument,
    ChamferFeature,
    FilletFeature,
    HoleFeature,
    LineSegment2D,
    PlannerMetadata,
    Point2D,
    ProfileExtrusionBase,
    ProfileLoop2D,
    ProfileRevolutionBase,
    RectangularCutoutFeature,
    SideFace,
)
from app.services.backends import default_backend_registry, ocp_runtime_conflicted
from app.services.build123d_compiler import Build123dCompiler
from app.services.compiler import CadQueryCompiler
from app.services.drawing_pdf import EngineeringDrawingPdf
from app.services.freecad_compiler import FreeCADCompiler
from app.services.openscad import OpenScadCompiler
from app.services.preview import SvgPreview
from app.services.validator import DesignValidator


def _rectangle_profile(
    *,
    inner_radius: float = 0,
    outer_radius: float = 20,
    min_z: float = 0,
    max_z: float = 30,
) -> ProfileLoop2D:
    points = [
        Point2D(x=inner_radius, y=min_z),
        Point2D(x=outer_radius, y=min_z),
        Point2D(x=outer_radius, y=max_z),
        Point2D(x=inner_radius, y=max_z),
    ]
    return ProfileLoop2D(
        segments=[
            LineSegment2D(start=point, end=points[(index + 1) % len(points)])
            for index, point in enumerate(points)
        ]
    )


def revolution_document(*, outer: ProfileLoop2D | None = None) -> CadDocument:
    return CadDocument(
        schema_version="1.2",
        name="revolved-profile",
        source_prompt="revolve a radius and height profile",
        base=ProfileRevolutionBase(outer=outer or _rectangle_profile()),
        planner=PlannerMetadata(planner="test"),
    )


def _arc_profile() -> ProfileLoop2D:
    return ProfileLoop2D(
        segments=[
            LineSegment2D(start=Point2D(x=0, y=0), end=Point2D(x=12, y=0)),
            ArcSegment2D(
                start=Point2D(x=12, y=0),
                mid=Point2D(x=16, y=15),
                end=Point2D(x=12, y=30),
            ),
            LineSegment2D(start=Point2D(x=12, y=30), end=Point2D(x=0, y=30)),
            LineSegment2D(start=Point2D(x=0, y=30), end=Point2D(x=0, y=0)),
        ]
    )


def test_schema_1_2_gates_revolution_and_keeps_profile_extrusion_compatible() -> None:
    with pytest.raises(ValidationError, match="schema_version 1.2"):
        CadDocument(
            schema_version="1.1",
            name="old-revolution",
            source_prompt="old revolution",
            base=ProfileRevolutionBase(outer=_rectangle_profile()),
            planner=PlannerMetadata(planner="test"),
        )

    extrusion = CadDocument(
        schema_version="1.2",
        name="compatible-extrusion",
        source_prompt="profile extrusion under the newer schema",
        base=ProfileExtrusionBase(outer=_rectangle_profile(), thickness=5),
        planner=PlannerMetadata(planner="test"),
    )
    assert extrusion.base.kind == "profile_extrusion"


@pytest.mark.parametrize(
    "feature, message",
    [
        ({"holes": [HoleFeature(diameter=3)]}, "holes or cutouts"),
        (
            {
                "cutouts": [
                    RectangularCutoutFeature(
                        face=SideFace.POSITIVE_X,
                        width=2,
                        height=2,
                    )
                ]
            },
            "holes or cutouts",
        ),
        ({"fillets": [FilletFeature(radius=1)]}, "top-level fillets or chamfers"),
        ({"chamfers": [ChamferFeature(distance=1)]}, "top-level fillets or chamfers"),
    ],
)
def test_revolution_rejects_unsupported_secondary_features(feature, message) -> None:
    with pytest.raises(ValidationError, match=message):
        CadDocument(
            schema_version="1.2",
            name="unsafe-revolution",
            source_prompt="unsafe revolution",
            base=ProfileRevolutionBase(outer=_rectangle_profile()),
            planner=PlannerMetadata(planner="test"),
            **feature,
        )


@pytest.mark.parametrize(
    "outer, expected_code",
    [
        (
            _rectangle_profile(inner_radius=-1),
            "revolution_negative_radius",
        ),
        (
            _rectangle_profile(min_z=-1),
            "revolution_negative_z",
        ),
        (
            _rectangle_profile(min_z=4, max_z=4),
            "revolution_zero_height",
        ),
    ],
)
def test_revolution_validator_enforces_axis_side_and_positive_extent(
    outer: ProfileLoop2D,
    expected_code: str,
) -> None:
    report = DesignValidator().validate(revolution_document(outer=outer))
    assert not report.valid
    assert expected_code in {issue.code for issue in report.issues}


def test_revolution_validator_accepts_closed_continuous_arc_profile() -> None:
    assert DesignValidator().validate(revolution_document(outer=_arc_profile())).valid


def test_revolution_validator_rejects_discontinuous_self_crossing_and_zero_area() -> None:
    discontinuous = ProfileLoop2D(
        segments=[
            LineSegment2D(start=Point2D(x=0, y=0), end=Point2D(x=20, y=0)),
            LineSegment2D(start=Point2D(x=20, y=1), end=Point2D(x=20, y=20)),
            LineSegment2D(start=Point2D(x=20, y=20), end=Point2D(x=0, y=20)),
            LineSegment2D(start=Point2D(x=0, y=20), end=Point2D(x=0, y=0)),
        ]
    )
    bow_tie = ProfileLoop2D(
        segments=[
            LineSegment2D(start=Point2D(x=0, y=0), end=Point2D(x=20, y=20)),
            LineSegment2D(start=Point2D(x=20, y=20), end=Point2D(x=0, y=20)),
            LineSegment2D(start=Point2D(x=0, y=20), end=Point2D(x=20, y=0)),
            LineSegment2D(start=Point2D(x=20, y=0), end=Point2D(x=0, y=0)),
        ]
    )
    zero_area = ProfileLoop2D(
        segments=[
            LineSegment2D(start=Point2D(x=1, y=1), end=Point2D(x=10, y=10)),
            LineSegment2D(start=Point2D(x=10, y=10), end=Point2D(x=20, y=20)),
            LineSegment2D(start=Point2D(x=20, y=20), end=Point2D(x=1, y=1)),
        ]
    )

    cases = [
        (discontinuous, "profile_not_continuous"),
        (bow_tie, "profile_self_intersection"),
        (zero_area, "profile_zero_area"),
    ]
    for outer, expected_code in cases:
        report = DesignValidator().validate(revolution_document(outer=outer))
        assert not report.valid
        assert expected_code in {issue.code for issue in report.issues}


def test_all_revolution_compilers_are_deterministic_and_syntax_valid() -> None:
    doc = revolution_document(outer=_arc_profile())
    compilers = [
        CadQueryCompiler(),
        Build123dCompiler(),
        FreeCADCompiler(),
        OpenScadCompiler(),
    ]
    outputs = [compiler.compile(doc) for compiler in compilers]
    assert outputs == [compiler.compile(doc) for compiler in compilers]

    cadquery, build123d, freecad, openscad = outputs
    assert "cq.Workplane('XZ').moveTo(0, 0)" in cadquery
    assert "profile.threePointArc((16, 15), (12, 30))" in cadquery
    assert "axisStart=(0, 0), axisEnd=(0, 1)" in cadquery
    assert "Vector(start[\"x\"], 0, start[\"y\"])" in build123d
    assert "revolve(Face(Wire(edges)), axis=Axis.Z, revolution_arc=360)" in build123d
    assert "face.revolve(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 360)" in freecad
    assert "rotate_extrude(angle=360, convexity=10, $fn=96)" in openscad
    assert "at most 256 segments per arc" in openscad
    ast.parse(cadquery)
    ast.parse(build123d)
    ast.parse(freecad)


def test_revolution_backend_capabilities_advertise_schema_and_base() -> None:
    for capability in default_backend_registry().capabilities():
        assert "1.2" in capability.schema_versions
        assert "profile_revolution" in capability.base_features


def test_revolution_preview_and_drawing_use_diameter_and_height_extents() -> None:
    doc = revolution_document(outer=_rectangle_profile(inner_radius=8))
    preview = SvgPreview().render(doc)
    drawing = EngineeringDrawingPdf().render(doc)

    assert "Front revolution silhouette" in preview
    assert "40 × 30 mm" in preview
    assert preview.count('<path d="M ') == 2
    assert b"40 x 40 x 30 mm" in drawing
    assert drawing.count(b" h S") >= 2


@pytest.mark.skipif(
    importlib.util.find_spec("cadquery") is None or ocp_runtime_conflicted(),
    reason="compatible CadQuery runtime is not installed",
)
def test_cadquery_revolution_executes_exports_and_reads_back_step(tmp_path) -> None:
    import cadquery as cq

    namespace: dict[str, object] = {"__name__": "generated_revolution"}
    exec(CadQueryCompiler().compile(revolution_document()), namespace)
    result, warnings = namespace["build"]()

    assert warnings == []
    assert result.val().isValid()
    bounds = result.val().BoundingBox()
    assert bounds.xlen == pytest.approx(40, abs=1e-5)
    assert bounds.ylen == pytest.approx(40, abs=1e-5)
    assert bounds.zlen == pytest.approx(30, abs=1e-5)

    step_path = tmp_path / "revolution.step"
    result.export(str(step_path))
    imported = cq.importers.importStep(str(step_path))
    assert step_path.stat().st_size > 0
    assert imported.val().isValid()
    assert imported.val().BoundingBox().zlen == pytest.approx(30, abs=1e-5)

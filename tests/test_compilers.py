from __future__ import annotations

from app.models.cad import (
    ArcSegment2D,
    CadDocument,
    EnclosureBase,
    FilletFeature,
    HoleFeature,
    LineSegment2D,
    PlannerMetadata,
    PlateBase,
    Point2D,
    ProfileExtrusionBase,
    ProfileLoop2D,
    RectangularCutoutFeature,
    SideFace,
)
from app.services.compiler import CadQueryCompiler
from app.services.openscad import OpenScadCompiler


def profile_document():
    return CadDocument(
        schema_version="1.1",
        name="arc-profile",
        source_prompt="arc profile",
        base=ProfileExtrusionBase(
            thickness=6,
            outer=ProfileLoop2D(
                segments=[
                    LineSegment2D(start=Point2D(x=-20, y=-10), end=Point2D(x=20, y=-10)),
                    ArcSegment2D(
                        start=Point2D(x=20, y=-10),
                        mid=Point2D(x=25, y=0),
                        end=Point2D(x=20, y=10),
                    ),
                    LineSegment2D(start=Point2D(x=20, y=10), end=Point2D(x=-20, y=10)),
                    LineSegment2D(start=Point2D(x=-20, y=10), end=Point2D(x=-20, y=-10)),
                ]
            ),
        ),
        planner=PlannerMetadata(planner="test"),
    )


def document():
    return CadDocument(
        name="plate",
        source_prompt="plate",
        base=PlateBase(length=100, width=50, thickness=8),
        holes=[HoleFeature(x=20, y=0, diameter=5)],
        fillets=[FilletFeature(radius=3)],
        planner=PlannerMetadata(planner="test"),
    )


def test_cadquery_compiler_is_deterministic_and_safe():
    code = CadQueryCompiler().compile(document())
    assert "cq.Workplane('XY').box(100, 50, 8" in code
    assert "cq.Solid.makeCylinder(2.5" in code
    assert "fillet(3)" in code
    assert "subprocess" not in code
    compile(code, "model.py", "exec")


def test_openscad_compiler_contains_difference():
    code = OpenScadCompiler().compile(document())
    assert "difference()" in code
    assert "cylinder(d=5" in code


def test_profile_extrusion_compilers_preserve_arc_or_use_bounded_tessellation():
    doc = profile_document()

    cadquery = CadQueryCompiler().compile(doc)
    assert "profile = cq.Workplane('XY').moveTo(-20, -10)" in cadquery
    assert "profile.threePointArc((25, 0), (20, 10))" in cadquery
    assert "profile.close().extrude(6)" in cadquery
    compile(cadquery, "profile-model.py", "exec")

    scad = OpenScadCompiler().compile(doc)
    assert "linear_extrude(height=6) polygon(points=[" in scad
    assert "tessellated with at most 256 segments per arc" in scad


def test_axis_aware_blind_and_countersink_compilation():
    from app.models.cad import Axis, HoleType

    doc = CadDocument(
        name="axis-test",
        source_prompt="axis test",
        base=PlateBase(length=100, width=50, thickness=20),
        holes=[
            HoleFeature(
                x=0,
                y=0,
                z=10,
                axis=Axis.X,
                diameter=6,
                hole_type=HoleType.BLIND,
                depth=12,
            ),
            HoleFeature(
                x=0,
                y=0,
                axis=Axis.Z,
                diameter=5,
                hole_type=HoleType.COUNTERSINK,
                countersink_diameter=10,
                countersink_angle=90,
            ),
        ],
        planner=PlannerMetadata(planner="test"),
    )
    code = CadQueryCompiler().compile(doc)
    assert "cq.Vector(-1, 0, 0)" in code
    assert "cq.Solid.makeCone(5, 2.5, 2.5" in code
    compile(code, "axis-model.py", "exec")

    scad = OpenScadCompiler().compile(doc)
    assert "rotate([0, -90, 0])" in scad
    assert "cylinder(d1=10, d2=5, h=2.5)" in scad


def test_prompt_injection_text_remains_data_not_python():
    import ast

    doc = document()
    doc.source_prompt = "'); __import__('os').system('touch /tmp/promptcad-pwned'); #"
    code = CadQueryCompiler().compile(doc)
    tree = ast.parse(code)

    assert not any(
        isinstance(node, ast.Import) and any(alias.name == "os" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "system"
        for node in ast.walk(tree)
    )
    compile(code, "safe-model.py", "exec")


def test_enclosure_side_cutout_compiles_to_cadquery_and_openscad():
    doc = CadDocument(
        name="enclosure-cutout",
        source_prompt="enclosure with a side cutout",
        base=EnclosureBase(length=94, width=58, height=22, wall_thickness=2),
        cutouts=[
            RectangularCutoutFeature(
                face=SideFace.POSITIVE_Y,
                x=25,
                z=9,
                width=14,
                height=8,
            )
        ],
        planner=PlannerMetadata(planner="test"),
    )

    code = CadQueryCompiler().compile(doc)
    assert "cutout_0 = cq.Workplane('XY').box(14, 2.4, 8" in code
    assert ".translate((25, 28, 9))" in code
    compile(code, "cutout-model.py", "exec")

    scad = OpenScadCompiler().compile(doc)
    assert "translate([18, 26.8, 5]) cube([14, 2.4, 8]);" in scad

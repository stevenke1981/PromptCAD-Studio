from __future__ import annotations

from app.models.cad import CadDocument, FilletFeature, HoleFeature, PlannerMetadata, PlateBase
from app.services.compiler import CadQueryCompiler
from app.services.openscad import OpenScadCompiler


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

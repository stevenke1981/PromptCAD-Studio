from __future__ import annotations

import ast
import json

import pytest

from app.models.cad import (
    ArcSegment2D,
    Axis,
    CadDocument,
    ChamferFeature,
    CylinderBase,
    EdgeSelector,
    EnclosureBase,
    FilletFeature,
    HoleFeature,
    HoleType,
    LBracketBase,
    LineSegment2D,
    PlannerMetadata,
    PlateBase,
    Point2D,
    ProfileExtrusionBase,
    ProfileLoop2D,
    RectangularCutoutFeature,
    RingBase,
    SideFace,
)
from app.services.build123d_compiler import Build123dCompiler
from app.services.external_adapters import (
    Fusion360AdapterCompiler,
    SolidWorksAdapterCompiler,
)
from app.services.freecad_compiler import FreeCADCompiler


def _document(base=None, *, source_prompt: str = "phase five compiler test") -> CadDocument:
    return CadDocument(
        schema_version="1.1" if isinstance(base, ProfileExtrusionBase) else "1.0",
        name="phase-five",
        source_prompt=source_prompt,
        base=base or PlateBase(length=120, width=60, thickness=12),
        planner=PlannerMetadata(planner="test"),
    )


def _profile() -> ProfileExtrusionBase:
    return ProfileExtrusionBase(
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
    )


def _embedded_document(source: str) -> dict:
    tree = ast.parse(source)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "CAD_SPEC" for target in node.targets)
    )
    assert isinstance(assignment.value, ast.Call)
    assert isinstance(assignment.value.args[0], ast.Constant)
    return json.loads(assignment.value.args[0].value)


@pytest.mark.parametrize(
    "base",
    [
        PlateBase(length=100, width=50, thickness=8),
        CylinderBase(diameter=40, height=12),
        RingBase(outer_diameter=50, inner_diameter=30, height=10),
        LBracketBase(width=80, depth=40, vertical_height=50, thickness=5),
        EnclosureBase(length=94, width=58, height=22, wall_thickness=2),
        _profile(),
    ],
    ids=["plate", "cylinder", "ring", "l-bracket", "enclosure", "profile"],
)
@pytest.mark.parametrize("compiler", [Build123dCompiler(), FreeCADCompiler()])
def test_source_compilers_are_deterministic_and_syntax_valid_for_every_base(
    base, compiler
):
    document = _document(base)

    first = compiler.compile(document)
    second = compiler.compile(document)

    assert first == second
    assert _embedded_document(first) == document.model_dump(mode="json")
    compile(first, f"{base.kind}.py", "exec")


def test_build123d_script_covers_exact_profiles_holes_cutouts_and_finishing():
    document = _document()
    document.holes = [
        HoleFeature(x=-30, axis=Axis.Z, diameter=4, hole_type=HoleType.THROUGH),
        HoleFeature(
            x=-20,
            axis=Axis.X,
            z=6,
            diameter=5,
            hole_type=HoleType.BLIND,
            depth=4,
        ),
        HoleFeature(y=-10, axis=Axis.Y, z=6, diameter=6, hole_type=HoleType.CLEARANCE),
        HoleFeature(x=0, axis=Axis.Z, diameter=3, hole_type=HoleType.TAPPED, thread="M3"),
        HoleFeature(
            x=20,
            axis=Axis.Z,
            diameter=5,
            hole_type=HoleType.COUNTERBORE,
            counterbore_diameter=9,
            counterbore_depth=3,
        ),
        HoleFeature(
            x=30,
            axis=Axis.Z,
            diameter=5,
            hole_type=HoleType.COUNTERSINK,
            countersink_diameter=10,
            countersink_angle=90,
        ),
    ]
    document.cutouts = [
        RectangularCutoutFeature(face=face, width=12, height=6, z=6)
        for face in SideFace
    ]
    document.fillets = [
        FilletFeature(radius=index + 1, selector=selector)
        for index, selector in enumerate(EdgeSelector)
    ]
    document.chamfers = [
        ChamferFeature(distance=index + 1, selector=selector)
        for index, selector in enumerate(EdgeSelector)
    ]

    source = Build123dCompiler().compile(document)

    assert "ThreePointArc(" in source
    assert "return extrude(Face(Wire(edges)), amount=base[\"thickness\"])" in source
    assert "Rot(0, 90, 0)" in source
    assert "Rot(-90, 0, 0)" in source
    assert "counterbore = Cylinder(" in source
    assert "countersink = Cone(" in source
    assert "thread metadata is preserved in CAD_SPEC" in source
    assert "result -= Pos(*center) * cutter" in source
    assert "result = fillet(" in source
    assert "result = chamfer(" in source
    assert "skipped: {exc}" not in source
    assert "did not produce a file" in source
    assert '"--output-dir"' in source
    assert '"--formats"' in source
    assert "export_step(" in source
    assert "export_stl(" in source
    compile(source, "build123d-model.py", "exec")


def test_freecad_script_covers_exact_profiles_holes_cutouts_and_finishing():
    source = FreeCADCompiler().compile(_document(_profile()))

    assert "Part.Arc(" in source
    assert "Part.Face(Part.Wire(edges)).extrude(" in source
    assert "Part.makeCylinder(" in source
    assert "Part.makeCone(" in source
    assert "Part.makeBox(*dimensions, App.Vector(*origin))" in source
    assert "result.makeFillet(" in source
    assert "result.makeChamfer(" in source
    assert "warnings.append(f\"fillet" not in source
    assert "warnings.append(f\"chamfer" not in source
    assert "Part.export([feature]" in source
    assert "Mesh.export([feature]" in source
    assert '"--output-dir"' in source
    assert '"--formats"' in source
    compile(source, "freecad-model.py", "exec")


@pytest.mark.parametrize(
    "compiler",
    [
        Build123dCompiler(),
        FreeCADCompiler(),
        Fusion360AdapterCompiler(),
        SolidWorksAdapterCompiler(),
    ],
)
def test_prompt_injection_is_only_json_data(compiler):
    attack = "'); __import__('os').system('calc.exe'); open('owned','w'); #"
    document = _document(source_prompt=attack)

    source = compiler.compile(document)
    tree = ast.parse(source)

    assert _embedded_document(source)["source_prompt"] == attack
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "system"
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "open"
        for node in ast.walk(tree)
    )


@pytest.mark.parametrize("compiler", [Build123dCompiler(), FreeCADCompiler()])
def test_internal_template_markers_in_prompt_remain_data(compiler):
    prompt = "__PROMPTCAD_PROFILE_EXTENTS__ __PROMPTCAD_DOCUMENT_JSON__"
    source = compiler.compile(_document(source_prompt=prompt))

    compile(source, "marker-collision.py", "exec")
    assert _embedded_document(source)["source_prompt"] == prompt


def test_external_adapter_scripts_are_isolated_and_only_use_sibling_step():
    document = _document()
    fusion = Fusion360AdapterCompiler().compile(document)
    solidworks = SolidWorksAdapterCompiler().compile(document)

    assert fusion == Fusion360AdapterCompiler().compile(document)
    assert solidworks == SolidWorksAdapterCompiler().compile(document)
    for source in (fusion, solidworks):
        compile(source, "external-adapter.py", "exec")
        assert 'with_name("model.step")' in source
        assert "os.environ" not in source
        assert "subprocess" not in source
        assert "app.services" not in source
        assert "data_dir" not in source

    assert "import adsk.core" in fusion
    assert "app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)" in fusion
    assert "createSTEPImportOptions" in fusion
    assert "if not import_manager.importToTarget(" in fusion
    assert "createFusionArchiveExportOptions" in fusion
    assert 'with_name("model.f3d")' in fusion

    assert "win32com.client.Dispatch(\"SldWorks.Application\")" in solidworks
    assert "application.OpenDoc6(" in solidworks
    assert "model.Extension.SaveAs(" in solidworks
    assert 'with_name("model.SLDPRT")' in solidworks

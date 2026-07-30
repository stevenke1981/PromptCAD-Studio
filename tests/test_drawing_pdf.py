from __future__ import annotations

import asyncio

from app.models.cad import (
    CadDocument,
    EnclosureBase,
    HoleFeature,
    PlannerMetadata,
    RectangularCutoutFeature,
    SideFace,
)
from app.services.drawing_pdf import EngineeringDrawingPdf


def test_engineering_drawing_pdf_contains_views_and_valid_pdf_markers(tmp_path):
    doc = CadDocument(
        name="esp32-enclosure",
        source_prompt="enclosure",
        base=EnclosureBase(length=94, width=58, height=22, wall_thickness=2),
        holes=[HoleFeature(x=-35, y=-17, diameter=3.2)],
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
    path = tmp_path / "drawing.pdf"

    EngineeringDrawingPdf().write(doc, path)

    content = path.read_bytes()
    assert content.startswith(b"%PDF-1.4")
    assert content.endswith(b"%%EOF\n")
    assert b"TOP VIEW" in content
    assert b"FRONT VIEW" in content
    assert b"94 x 58 x 22 mm" in content
    assert len(content) > 1000


def test_engineering_drawing_front_view_contains_y_axis_motor_holes(tmp_path):
    from app.services.planners.standard_agent import StandardAwarePlanner

    doc = asyncio.run(StandardAwarePlanner().plan("NEMA17 馬達支架"))
    path = tmp_path / "nema17.pdf"

    EngineeringDrawingPdf().write(doc, path)

    content = path.read_bytes()
    assert content.count(b" c S") >= 5

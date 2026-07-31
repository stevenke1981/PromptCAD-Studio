from __future__ import annotations

import pypdfium2 as pdfium

from app.models.cad import (
    CadDocument,
    HoleFeature,
    Material,
    PlannerMetadata,
    PlateBase,
)
from app.models.manufacturing import ManufacturingDrawingSpec
from app.services.drawing_pdf import EngineeringDrawingPdf
from app.services.manufacturing import ManufacturingDrawingService


def _document() -> CadDocument:
    return CadDocument(
        name="fixture-plate",
        source_prompt="manufacturing fixture plate",
        base=PlateBase(length=120, width=60, thickness=8),
        holes=[
            HoleFeature(x=-20, y=0, diameter=6, thread="M6"),
            HoleFeature(x=20, y=0, diameter=6, thread="M6"),
        ],
        material=Material.ALUMINUM,
        planner=PlannerMetadata(planner="test"),
    )


def _extract_pages(pdf: bytes) -> list[str]:
    document = pdfium.PdfDocument(pdf)
    pages = []
    try:
        for page in document:
            text_page = page.get_textpage()
            try:
                pages.append(text_page.get_text_range())
            finally:
                text_page.close()
                page.close()
    finally:
        document.close()
    return pages


def test_manufacturing_drawing_is_bounded_searchable_two_page_package():
    doc = _document()
    spec = ManufacturingDrawingSpec.model_validate(
        {
            "cad_document_sha256": ManufacturingDrawingService.cad_document_sha256(doc),
            "title_block": {
                "part_name": "Fixture plate",
                "part_number": "FIXTURE-PLATE",
                "drawing_number": "PC-FIX-001",
                "revision": "B",
                "drawn_by": "PromptCAD",
                "drawn_on": "2026-08-01",
            },
            "general_tolerance": {
                "kind": "bilateral",
                "linear_mm": 0.1,
                "angular_deg": 0.5,
            },
            "dimensions": [
                {
                    "id": "overall-x",
                    "target": {"kind": "base", "measurement": "overall_x"},
                    "tolerance": {"kind": "symmetric", "plus_minus_mm": 0.1},
                    "datum_references": ["A"],
                }
            ],
            "datums": [
                {"id": "A", "target": {"kind": "base_face", "face": "bottom"}},
                {"id": "B", "target": {"kind": "hole_axis", "hole_index": 0}},
            ],
            "surface_finishes": [
                {
                    "id": "finish-top",
                    "target": {"kind": "base_face", "face": "top"},
                    "ra_micrometers": 1.6,
                    "datum_reference": "A",
                }
            ],
            "bom": [
                {
                    "id": "fixture-plate",
                    "item_number": 1,
                    "part_number": "FIXTURE-PLATE",
                    "description": "Machined fixture plate",
                    "quantity": 1,
                    "procurement": "make",
                    "material": "Aluminum",
                    "note": "Deburr",
                }
            ],
            "revisions": [
                {
                    "revision": "B",
                    "occurred_on": "2026-08-01",
                    "description": "Add manufacturing controls",
                    "author": "Manufacturing lead",
                }
            ],
        }
    )
    review = {
        "status": "approved",
        "reviewed_by": "QA reviewer",
        "reviewed_at": "2026-08-01T10:00:00Z",
    }

    pdf = EngineeringDrawingPdf().render(doc, spec, review)
    pages = _extract_pages(pdf)
    text = "\n".join(pages)

    assert len(pages) == 2
    assert len(pdf) < 100_000
    assert "PC-FIX-001" in text
    assert "OVERALL: 120 x 60 x 8 mm" in text
    assert "H1: DIA 6 mm M6" in text
    assert "+/- 0.1 mm / +/- 0.5 deg" in text
    assert "overall-x: 120 mm +/- 0.1 mm DATUM A" in text
    assert "DATUM: A, B" in text
    assert "Ra 1.6 um" in text
    assert "BILL OF MATERIALS" in text
    assert "FIXTURE-PLATE" in text
    assert "REVISION HISTORY" in text
    assert "Add manufacturing controls" in text
    assert "WORKFLOW STATUS: approved" in text
    assert "QA reviewer" in text
    assert "NOT A CRYPTOGRAPHIC SIGNATURE" in text


def test_manufacturing_drawing_truncates_unbounded_bom_and_review_input():
    spec = {
        "bom": [
            {"item_number": index, "description": f"COMPONENT-{index}", "quantity": 1}
            for index in range(1000)
        ],
        "review_records": [
            {"reviewed_by": "reviewer", "notes": "x" * 10_000},
        ],
    }

    pdf = EngineeringDrawingPdf().render(_document(), spec)

    assert len(pdf) < 100_000
    assert b"991 MORE BOM ITEMS" in pdf
    assert b"COMPONENT-9" not in pdf


def test_legacy_render_remains_single_page():
    pdf = EngineeringDrawingPdf().render(_document())

    assert b"/Count 1" in pdf
    assert b"PROMPTCAD ENGINEERING DRAWING" in pdf
    assert b"MANUFACTURING NOTES / BOM" not in pdf

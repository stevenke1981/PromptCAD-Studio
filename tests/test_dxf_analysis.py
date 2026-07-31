from __future__ import annotations

import io

import ezdxf
import pytest

from app.services.dxf_analysis import DxfAnalysisError, DxfFeatureExtractor


def _extractor(**limits) -> DxfFeatureExtractor:
    return DxfFeatureExtractor(
        max_bytes=limits.get("max_bytes", 100_000),
        max_entities=limits.get("max_entities", 32),
        max_segments=limits.get("max_segments", 32),
        max_holes=limits.get("max_holes", 8),
    )


def _bytes(document) -> bytes:
    output = io.StringIO()
    document.write(output)
    return output.getvalue().encode("ascii")


def _rectangle_document(*, insunits: int = 4, hole: bool = True):
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = insunits
    modelspace = document.modelspace()
    for start, end in [((0, 0), (20, 0)), ((20, 0), (20, 10)), ((20, 10), (0, 10)), ((0, 10), (0, 0))]:
        modelspace.add_line(start, end)
    if hole:
        modelspace.add_circle((10, 5), radius=2)
    return document


def test_mm_profile_hole_provenance_and_tree() -> None:
    result = _extractor().analyze(_bytes(_rectangle_document()), thickness_mm=3)

    assert result.convertible is True
    assert result.provenance.source_unit == "mm"
    assert result.provenance.entity_total == 5
    assert len(result.outer_profile.segments) == 4
    assert result.holes[0].radius_mm == 2
    assert [node.operation for node in result.feature_tree] == [
        "profile_loop",
        "extrude_profile",
        "circle_hole",
    ]
    assert result.proposed_spec is not None
    assert result.proposed_spec.schema_version == "1.1"
    assert result.validation is not None and result.validation.valid
    assert result.preview_svg and "<svg" in result.preview_svg


def test_inches_convert_to_mm_and_unitless_needs_override() -> None:
    inches = _extractor().analyze(_bytes(_rectangle_document(insunits=1)), thickness_mm=3)
    assert inches.provenance.source_unit == "inch"
    assert inches.outer_profile.segments[0].end.x == pytest.approx(508)

    unitless = _rectangle_document(insunits=0)
    with pytest.raises(DxfAnalysisError, match="INSUNITS"):
        _extractor().analyze(_bytes(unitless), thickness_mm=3)
    converted = _extractor().analyze(_bytes(unitless), thickness_mm=3, unit_override="cm")
    assert converted.provenance.source_unit == "cm"
    assert converted.outer_profile.segments[0].end.x == pytest.approx(200)


def test_line_and_arc_profile_preserves_exact_arc() -> None:
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4
    modelspace = document.modelspace()
    modelspace.add_line((10, 0), (0, 0))
    modelspace.add_arc((5, 0), radius=5, start_angle=180, end_angle=0)

    result = _extractor().analyze(_bytes(document), thickness_mm=2)
    arc = result.outer_profile.segments[1]
    assert arc.kind == "arc"
    assert arc.mid.y == pytest.approx(-5)
    assert result.proposed_spec is not None


def test_closed_lwpolyline_and_unsupported_entity_are_handled() -> None:
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4
    document.modelspace().add_lwpolyline([(0, 0), (20, 0), (20, 10), (0, 10)], close=True)
    result = _extractor().analyze(_bytes(document), thickness_mm=2)
    assert result.entity_counts.lwpolylines == 1

    document.modelspace().add_spline(fit_points=[(0, 0), (1, 2), (2, 0)])
    with pytest.raises(DxfAnalysisError, match="Unsupported DXF entity: SPLINE"):
        _extractor().analyze(_bytes(document), thickness_mm=2)

    hidden = _rectangle_document()
    block = hidden.blocks.new(name="UNREFERENCED")
    block.add_line((0, 0), (1, 1))
    with pytest.raises(DxfAnalysisError, match="BLOCKS"):
        _extractor().analyze(_bytes(hidden), thickness_mm=2)


@pytest.mark.parametrize(
    ("data", "extractor", "message"),
    [
        (b"PK\x03\x04not-a-dxf", _extractor(), "ZIP"),
        (_bytes(_rectangle_document(hole=False)), _extractor(max_segments=3), "segment limit"),
    ],
)
def test_format_and_hard_limits_are_rejected(data: bytes, extractor: DxfFeatureExtractor, message: str) -> None:
    with pytest.raises(DxfAnalysisError, match=message):
        extractor.analyze(data, thickness_mm=2)


def test_open_and_nonfinite_geometry_are_rejected() -> None:
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4
    document.modelspace().add_line((0, 0), (20, 0))
    with pytest.raises(DxfAnalysisError, match="closed"):
        _extractor().analyze(_bytes(document), thickness_mm=2)

    nonfinite = (
        b"0\nSECTION\n2\nHEADER\n9\n$INSUNITS\n70\n4\n0\nENDSEC\n"
        b"0\nSECTION\n2\nENTITIES\n0\nLINE\n10\nnan\n20\n0\n30\n0\n11\n1\n21\n0\n31\n0\n"
        b"0\nENDSEC\n0\nEOF\n"
    )
    with pytest.raises(DxfAnalysisError):
        _extractor().analyze(nonfinite, thickness_mm=2)


def test_hole_outside_profile_requires_review_and_is_not_convertible() -> None:
    document = _rectangle_document()
    document.modelspace().delete_entity(next(entity for entity in document.modelspace() if entity.dxftype() == "CIRCLE"))
    document.modelspace().add_circle((30, 5), radius=2)

    result = _extractor().analyze(_bytes(document), thickness_mm=3)
    assert result.convertible is False
    assert result.proposed_spec is None
    assert result.validation is not None
    assert any(issue.code == "hole_outside_profile" for issue in result.validation.issues)

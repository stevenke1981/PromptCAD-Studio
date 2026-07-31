from __future__ import annotations

import io

import ezdxf
import pytest

from app.services.dxf_analysis import DxfAnalysisError, DxfFeatureExtractor


def _bytes(document) -> bytes:
    output = io.StringIO()
    document.write(output)
    return output.getvalue().encode("ascii")


def _extractor() -> DxfFeatureExtractor:
    return DxfFeatureExtractor(
        max_bytes=100_000,
        max_entities=64,
        max_segments=64,
        max_holes=16,
    )


def _rectangle_with_holes(centers: list[tuple[float, float]]):
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(-30, -30), (30, -30), (30, 30), (-30, 30)],
        close=True,
    )
    for center in centers:
        modelspace.add_circle(center, radius=1.5)
    return document


def _revolution_document(*, sloped_axis: bool = False):
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4
    document.layers.add("CENTER", linetype="CENTER")
    modelspace = document.modelspace()
    modelspace.add_line((0, 0), (20, 0))
    modelspace.add_line((20, 0), (20, 40))
    modelspace.add_line((20, 40), (0, 40))
    axis_end = (1, 40) if sloped_axis else (0, 40)
    modelspace.add_line((0, 0), axis_end, dxfattribs={"layer": "CENTER"})
    return document


def _rounded_rectangle_document():
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4
    modelspace = document.modelspace()
    modelspace.add_line((2, 0), (18, 0))
    modelspace.add_arc((18, 2), 2, 270, 360)
    modelspace.add_line((20, 2), (20, 8))
    modelspace.add_arc((18, 8), 2, 0, 90)
    modelspace.add_line((18, 10), (2, 10))
    modelspace.add_arc((2, 8), 2, 90, 180)
    modelspace.add_line((0, 8), (0, 2))
    modelspace.add_arc((2, 2), 2, 180, 270)
    return document


def test_centerline_half_profile_infers_revolution() -> None:
    result = _extractor().analyze(
        _bytes(_revolution_document()),
        thickness_mm=5,
    )

    assert result.convertible is True
    assert result.inferred_operation == "revolve"
    assert result.entity_counts.centerlines == 1
    assert result.revolution_axis is not None
    assert result.revolution_axis.orientation == "vertical"
    assert [node.operation for node in result.feature_tree] == [
        "profile_loop",
        "revolve_profile",
    ]
    assert result.proposed_spec is not None
    assert result.proposed_spec.schema_version == "1.2"
    assert result.proposed_spec.base.kind == "profile_revolution"
    assert min(
        point.x
        for segment in result.proposed_spec.base.outer.segments
        for point in (segment.start, segment.end)
    ) == pytest.approx(0)


def test_revolution_requires_a_straight_centerline() -> None:
    without_axis = _rectangle_with_holes([])
    with pytest.raises(DxfAnalysisError, match="requires one horizontal or vertical CENTER"):
        _extractor().analyze(
            _bytes(without_axis),
            thickness_mm=5,
            operation_mode="revolve",
        )
    with pytest.raises(DxfAnalysisError, match="horizontal or vertical"):
        _extractor().analyze(
            _bytes(_revolution_document(sloped_axis=True)),
            thickness_mm=5,
        )


def test_bylayer_center_linetype_is_resolved_from_layer_table() -> None:
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4
    document.layers.add("AXES", linetype="CENTER")
    modelspace = document.modelspace()
    modelspace.add_line((0, 0), (10, 0))
    modelspace.add_line((10, 0), (10, 20))
    modelspace.add_line((10, 20), (0, 20))
    modelspace.add_line(
        (0, 0),
        (0, 20),
        dxfattribs={"layer": "AXES", "linetype": "BYLAYER"},
    )

    result = _extractor().analyze(_bytes(document), thickness_mm=5)

    assert result.inferred_operation == "revolve"
    assert result.revolution_axis is not None
    assert result.revolution_axis.source == "linetype"

def test_linear_hole_pattern_is_editable_and_expands_to_holes() -> None:
    result = _extractor().analyze(
        _bytes(_rectangle_with_holes([(-10, 0), (0, 0), (10, 0)])),
        thickness_mm=4,
    )

    assert result.convertible is True
    assert len(result.patterns) == 1
    assert result.patterns[0].kind == "linear"
    assert result.patterns[0].spacing_mm == pytest.approx(10)
    assert [node.operation for node in result.feature_tree] == [
        "profile_loop",
        "extrude_profile",
        "hole_pattern",
    ]
    assert result.proposed_spec is not None
    assert sorted(hole.x for hole in result.proposed_spec.holes) == pytest.approx([-10, 0, 10])

    edited_tree = [node.model_copy(deep=True) for node in result.feature_tree]
    pattern_node = next(node for node in edited_tree if node.operation == "hole_pattern")
    assert pattern_node.pattern is not None
    pattern_node.pattern.spacing_mm = 8
    edited = _extractor().spec_from_feature_tree(edited_tree, result.provenance)
    assert sorted(hole.x for hole in edited.holes) == pytest.approx([-10, -2, 6])


def test_circular_hole_pattern_is_preserved_and_expanded() -> None:
    result = _extractor().analyze(
        _bytes(_rectangle_with_holes([(10, 0), (0, 10), (-10, 0), (0, -10)])),
        thickness_mm=4,
    )

    assert result.convertible is True
    assert len(result.patterns) == 1
    pattern = result.patterns[0]
    assert pattern.kind == "circular"
    assert pattern.pattern_center is not None
    assert pattern.pattern_center.x == pytest.approx(0)
    assert pattern.pattern_center.y == pytest.approx(0)
    assert pattern.pattern_radius_mm == pytest.approx(10)
    assert pattern.angular_spacing_deg == pytest.approx(90)
    assert result.proposed_spec is not None
    assert len(result.proposed_spec.holes) == 4


def test_valid_pattern_is_extracted_with_same_diameter_stray_hole() -> None:
    result = _extractor().analyze(
        _bytes(_rectangle_with_holes([(-10, 0), (0, 0), (10, 0), (17, 13)])),
        thickness_mm=4,
    )

    assert len(result.patterns) == 1
    assert result.patterns[0].kind == "linear"
    assert result.patterns[0].count == 3
    assert [node.operation for node in result.feature_tree].count("circle_hole") == 1
    assert result.proposed_spec is not None
    assert len(result.proposed_spec.holes) == 4


def test_two_same_diameter_linear_patterns_are_extracted_independently() -> None:
    centers = [(-10, -8), (0, -8), (10, -8), (-10, 8), (0, 8), (10, 8)]
    result = _extractor().analyze(
        _bytes(_rectangle_with_holes(centers)),
        thickness_mm=4,
    )

    assert len(result.patterns) == 2
    assert all(pattern.kind == "linear" for pattern in result.patterns)
    assert result.proposed_spec is not None
    assert len(result.proposed_spec.holes) == 6


def test_forced_extrusion_overrides_centerline_inference() -> None:
    result = _extractor().analyze(
        _bytes(_revolution_document()),
        thickness_mm=7,
        operation_mode="extrude",
    )

    assert result.inferred_operation == "extrude"
    assert result.proposed_spec is not None
    assert result.proposed_spec.base.kind == "profile_extrusion"
    assert result.proposed_spec.base.thickness == pytest.approx(7)


def test_uniform_rounded_rectangle_becomes_global_vertical_fillet() -> None:
    result = _extractor().analyze(
        _bytes(_rounded_rectangle_document()),
        thickness_mm=5,
    )

    assert result.convertible is True
    assert len(result.outer_profile.segments) == 8
    assert len(result.edge_treatments) == 1
    assert result.edge_treatments[0].kind == "fillet"
    assert result.edge_treatments[0].size_mm == pytest.approx(2)
    assert result.proposed_spec is not None
    assert len(result.proposed_spec.base.outer.segments) == 4
    assert result.proposed_spec.fillets[0].radius == pytest.approx(2)
    assert result.feature_tree[-1].operation == "fillet_edges"


def test_uniform_chamfered_rectangle_becomes_global_vertical_chamfer() -> None:
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4
    document.modelspace().add_lwpolyline(
        [(2, 0), (18, 0), (20, 2), (20, 8), (18, 10), (2, 10), (0, 8), (0, 2)],
        close=True,
    )

    result = _extractor().analyze(_bytes(document), thickness_mm=5)

    assert result.convertible is True
    assert len(result.edge_treatments) == 1
    assert result.edge_treatments[0].kind == "chamfer"
    assert result.edge_treatments[0].size_mm == pytest.approx(2)
    assert result.proposed_spec is not None
    assert result.proposed_spec.chamfers[0].distance == pytest.approx(2)
    assert result.feature_tree[-1].operation == "chamfer_edges"


def test_concave_corner_arcs_are_not_promoted_to_global_fillet() -> None:
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4
    concave = -0.41421356237
    document.modelspace().add_lwpolyline(
        [
            (2, 0, 0),
            (18, 0, concave),
            (20, 2, 0),
            (20, 8, concave),
            (18, 10, 0),
            (2, 10, concave),
            (0, 8, 0),
            (0, 2, concave),
        ],
        format="xyb",
        close=True,
    )

    result = _extractor().analyze(_bytes(document), thickness_mm=5)

    assert result.edge_treatments == []
    assert all(node.operation != "fillet_edges" for node in result.feature_tree)
    assert result.proposed_spec is not None
    assert result.proposed_spec.fillets == []
    assert len(result.proposed_spec.base.outer.segments) == 8

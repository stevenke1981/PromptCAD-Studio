from __future__ import annotations

import pytest

from app.models.cad import (
    ArcSegment2D,
    Axis,
    CadDocument,
    EnclosureBase,
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
from app.services.profile_geometry import loop_polyline
from app.services.validator import DesignValidator


def profile_document(*, holes=None, segments=None):
    segments = segments or [
        LineSegment2D(start=Point2D(x=-20, y=-10), end=Point2D(x=20, y=-10)),
        ArcSegment2D(
            start=Point2D(x=20, y=-10),
            mid=Point2D(x=25, y=0),
            end=Point2D(x=20, y=10),
        ),
        LineSegment2D(start=Point2D(x=20, y=10), end=Point2D(x=-20, y=10)),
        LineSegment2D(start=Point2D(x=-20, y=10), end=Point2D(x=-20, y=-10)),
    ]
    return CadDocument(
        schema_version="1.1",
        name="profile",
        source_prompt="profile",
        base=ProfileExtrusionBase(outer=ProfileLoop2D(segments=segments), thickness=5),
        holes=holes or [],
        planner=PlannerMetadata(planner="test"),
    )


def make_doc(holes):
    return CadDocument(
        name="test",
        source_prompt="test part",
        base=PlateBase(length=40, width=30, thickness=5),
        holes=holes,
        planner=PlannerMetadata(planner="test"),
    )


def test_overlapping_holes_are_error():
    report = DesignValidator().validate(
        make_doc([HoleFeature(x=0, y=0, diameter=10), HoleFeature(x=4, y=0, diameter=10)])
    )
    assert not report.valid
    assert any(issue.code == "overlapping_holes" for issue in report.issues)


def test_hole_outside_part_is_error():
    report = DesignValidator().validate(make_doc([HoleFeature(x=19, y=0, diameter=6)]))
    assert not report.valid
    assert any(issue.code == "hole_outside_part" for issue in report.issues)


def test_vertical_plate_fillet_can_equal_half_thickness():
    from app.models.cad import FilletFeature

    doc = make_doc([])
    doc.fillets = [FilletFeature(radius=5)]
    report = DesignValidator().validate(doc)
    assert not any(issue.code == "fillet_too_large" for issue in report.issues)


def test_blind_depth_equal_to_material_is_blocked():
    from app.models.cad import HoleType

    report = DesignValidator().validate(
        make_doc([HoleFeature(x=0, y=0, diameter=4, hole_type=HoleType.BLIND, depth=5)])
    )
    assert not report.valid
    assert any(issue.code == "blind_depth_exceeds_material" for issue in report.issues)


def test_counterbore_effective_diameter_respects_edge():
    from app.models.cad import HoleType

    report = DesignValidator().validate(
        make_doc(
            [
                HoleFeature(
                    x=15,
                    y=0,
                    diameter=4,
                    hole_type=HoleType.COUNTERBORE,
                    counterbore_diameter=12,
                    counterbore_depth=2,
                )
            ]
        )
    )
    assert not report.valid
    assert any(issue.code == "hole_outside_part" for issue in report.issues)


def test_enclosure_side_cutout_within_face_is_valid():
    doc = CadDocument(
        name="cutout",
        source_prompt="cutout",
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

    report = DesignValidator().validate(doc)

    assert report.valid
    assert not any(issue.code == "cutout_outside_face" for issue in report.issues)


def test_enclosure_side_cutout_outside_face_is_error():
    doc = CadDocument(
        name="cutout",
        source_prompt="cutout",
        base=EnclosureBase(length=94, width=58, height=22, wall_thickness=2),
        cutouts=[
            RectangularCutoutFeature(
                face=SideFace.POSITIVE_Y,
                x=44,
                z=20,
                width=14,
                height=8,
            )
        ],
        planner=PlannerMetadata(planner="test"),
    )

    report = DesignValidator().validate(doc)

    assert not report.valid
    assert any(issue.code == "cutout_outside_face" for issue in report.issues)


def test_profile_loop_and_hole_containment_are_valid():
    report = DesignValidator().validate(
        profile_document(holes=[HoleFeature(x=0, y=0, diameter=3)])
    )

    assert report.valid


def test_profile_loop_reports_discontinuous_self_crossing_and_zero_area_geometry():
    discontinuous = profile_document(
        segments=[
            LineSegment2D(start=Point2D(x=0, y=0), end=Point2D(x=10, y=0)),
            LineSegment2D(start=Point2D(x=12, y=0), end=Point2D(x=0, y=10)),
            LineSegment2D(start=Point2D(x=0, y=10), end=Point2D(x=0, y=0)),
        ]
    )
    bow_tie = profile_document(
        segments=[
            LineSegment2D(start=Point2D(x=-10, y=-10), end=Point2D(x=10, y=10)),
            LineSegment2D(start=Point2D(x=10, y=10), end=Point2D(x=-10, y=10)),
            LineSegment2D(start=Point2D(x=-10, y=10), end=Point2D(x=10, y=-10)),
            LineSegment2D(start=Point2D(x=10, y=-10), end=Point2D(x=-10, y=-10)),
        ]
    )
    zero_area = profile_document(
        segments=[
            LineSegment2D(start=Point2D(x=0, y=0), end=Point2D(x=10, y=0)),
            LineSegment2D(start=Point2D(x=10, y=0), end=Point2D(x=20, y=0)),
            LineSegment2D(start=Point2D(x=20, y=0), end=Point2D(x=0, y=0)),
        ]
    )
    touching_loops = profile_document(
        segments=[
            LineSegment2D(start=Point2D(x=0, y=0), end=Point2D(x=10, y=0)),
            LineSegment2D(start=Point2D(x=10, y=0), end=Point2D(x=5, y=10)),
            LineSegment2D(start=Point2D(x=5, y=10), end=Point2D(x=0, y=0)),
            LineSegment2D(start=Point2D(x=0, y=0), end=Point2D(x=-10, y=0)),
            LineSegment2D(start=Point2D(x=-10, y=0), end=Point2D(x=-5, y=10)),
            LineSegment2D(start=Point2D(x=-5, y=10), end=Point2D(x=0, y=0)),
        ]
    )

    assert any(
        issue.code == "profile_not_continuous"
        for issue in DesignValidator().validate(discontinuous).issues
    )
    assert any(
        issue.code == "profile_self_intersection"
        for issue in DesignValidator().validate(bow_tie).issues
    )
    assert any(
        issue.code == "profile_zero_area"
        for issue in DesignValidator().validate(zero_area).issues
    )
    assert any(
        issue.code == "profile_self_intersection"
        for issue in DesignValidator().validate(touching_loops).issues
    )


def test_profile_hole_outside_outline_is_error():
    report = DesignValidator().validate(
        profile_document(holes=[HoleFeature(x=28, y=0, diameter=6)])
    )

    assert not report.valid
    assert any(issue.code == "hole_outside_profile" for issue in report.issues)


def test_large_profile_arc_tessellation_has_a_hard_upper_bound():
    doc = profile_document(
        segments=[
            ArcSegment2D(
                start=Point2D(x=0, y=-100_000),
                mid=Point2D(x=100_000, y=0),
                end=Point2D(x=0, y=100_000),
            ),
            LineSegment2D(start=Point2D(x=0, y=100_000), end=Point2D(x=0, y=-100_000)),
        ]
    )

    with pytest.raises(ValueError, match="tessellation tolerance"):
        loop_polyline(doc.base.outer)
    report = DesignValidator().validate(doc)
    assert not report.valid
    assert any(issue.code == "profile_tessellation_limit" for issue in report.issues)


def test_profile_extrusion_rejects_side_axis_holes_until_supported():
    report = DesignValidator().validate(
        profile_document(
            holes=[HoleFeature(x=0, y=0, z=2.5, axis=Axis.X, diameter=3)]
        )
    )

    assert not report.valid
    assert any(issue.code == "profile_side_hole_unsupported" for issue in report.issues)

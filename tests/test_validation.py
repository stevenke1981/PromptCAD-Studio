from __future__ import annotations

from app.models.cad import CadDocument, HoleFeature, PlannerMetadata, PlateBase
from app.services.validator import DesignValidator


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

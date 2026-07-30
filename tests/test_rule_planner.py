from __future__ import annotations

import asyncio

from app.models.cad import HoleType, PlateBase, RingBase
from app.services.planners.rule_based import RuleBasedPlanner


def plan(prompt: str):
    return asyncio.run(RuleBasedPlanner().plan(prompt))


def test_chinese_plate_four_m6_holes_and_fillet():
    doc = plan("鋁合金固定板，長120mm、寬60mm、厚10mm，四角 M6 通孔，邊距10mm，R5")
    assert isinstance(doc.base, PlateBase)
    assert (doc.base.length, doc.base.width, doc.base.thickness) == (120, 60, 10)
    assert len(doc.holes) == 4
    assert all(hole.diameter == 6.6 for hole in doc.holes)
    assert all(hole.hole_type == HoleType.CLEARANCE for hole in doc.holes)
    assert doc.fillets[0].radius == 5
    assert {(h.x, h.y) for h in doc.holes} == {(-50.0, -20.0), (50.0, -20.0), (50.0, 20.0), (-50.0, 20.0)}


def test_ring_dimensions():
    doc = plan("做一個墊圈，外徑30mm、內徑15mm、厚5mm")
    assert isinstance(doc.base, RingBase)
    assert doc.base.outer_diameter == 30
    assert doc.base.inner_diameter == 15
    assert doc.base.height == 5


def test_triplet_enclosure():
    doc = plan("做一個100x70x30mm的開口盒，壁厚2mm")
    assert doc.base.kind == "enclosure"
    assert doc.base.length == 100
    assert doc.base.width == 70
    assert doc.base.height == 30
    assert doc.base.wall_thickness == 2


def test_english_l_bracket_two_holes_and_diameter():
    doc = plan("Create an L bracket 80 mm wide, 50 mm deep, 60 mm tall, 4 mm thick, with two 5 mm holes.")
    assert doc.base.kind == "l_bracket"
    assert doc.base.width == 80
    assert doc.base.depth == 50
    assert doc.base.vertical_height == 60
    assert doc.base.thickness == 4
    assert len(doc.holes) == 2
    assert all(hole.diameter == 5 for hole in doc.holes)
    assert doc.planner.review_required


def test_chinese_two_m6_holes_use_clearance_table():
    doc = plan("長100寬50厚8的板，兩個 M6 孔")
    assert len(doc.holes) == 2
    assert all(hole.diameter == 6.6 for hole in doc.holes)
    assert all(hole.thread == "M6" for hole in doc.holes)


def test_blind_hole_explicit_diameter_and_depth():
    doc = plan("做一個長80寬40厚10的板，中央一個直徑5mm、深6mm盲孔")
    assert len(doc.holes) == 1
    hole = doc.holes[0]
    assert hole.hole_type == HoleType.BLIND
    assert hole.diameter == 5
    assert hole.depth == 6
    assert (hole.x, hole.y) == (0, 0)


def test_english_plate_does_not_trigger_pla_material():
    doc = plan("100x60x10mm plate with two 5mm blind holes 6mm deep")
    assert doc.material is None
    assert len(doc.holes) == 2
    assert all(hole.diameter == 5 for hole in doc.holes)
    assert all(hole.depth == 6 for hole in doc.holes)


def test_l_bracket_vertical_plate_holes_use_y_axis():
    from app.models.cad import Axis

    doc = plan("做一個L型支架，寬80、深50、高60、厚4，立板兩個M5孔")
    assert len(doc.holes) == 2
    assert all(hole.axis == Axis.Y for hole in doc.holes)
    assert all(hole.diameter == 5.5 for hole in doc.holes)


def test_countersink_direct_diameter_is_preserved():
    doc = plan("長100寬60厚10，兩個5mm沉頭孔")
    assert len(doc.holes) == 2
    assert all(hole.hole_type == HoleType.COUNTERSINK for hole in doc.holes)
    assert all(hole.diameter == 5 for hole in doc.holes)
    assert all(hole.countersink_diameter == 10 for hole in doc.holes)

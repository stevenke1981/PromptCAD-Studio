from __future__ import annotations

import asyncio

from app.models.cad import Axis, LBracketBase
from app.services.planners.standard_agent import StandardAwarePlanner


def plan(prompt: str):
    return asyncio.run(StandardAwarePlanner().plan(prompt))


def test_nema17_agent_infers_standard_motor_interface_and_provenance():
    doc = plan("畫一個可固定 NEMA17 馬達的支架")

    assert isinstance(doc.base, LBracketBase)
    assert (doc.base.width, doc.base.depth, doc.base.vertical_height, doc.base.thickness) == (
        60,
        50,
        50,
        3,
    )
    motor_holes = [hole for hole in doc.holes if hole.axis == Axis.Y and hole.thread == "M3"]
    assert len(motor_holes) == 4
    assert {(hole.x, hole.z) for hole in motor_holes} == {
        (-15.5, 12.5),
        (15.5, 12.5),
        (15.5, 43.5),
        (-15.5, 43.5),
    }
    shaft_opening = [hole for hole in doc.holes if hole.axis == Axis.Y and hole.thread is None]
    assert len(shaft_opening) == 1
    assert shaft_opening[0].diameter == 22.5
    assert doc.standards[0].key == "nema17-face"
    assert "nanotec.com" in doc.standards[0].source_url
    assert "pololu.com" in doc.standards[1].source_url


def test_auto_factory_routes_nema17_to_standard_agent(settings):
    from app.core.config import Settings
    from app.services.planners.factory import PlannerFactory

    settings = Settings(
        env="test",
        data_dir=settings.data_dir,
        planner_mode="auto",
        render_backend="source_only",
    )
    doc, used = asyncio.run(
        PlannerFactory(settings).plan("Create a bracket for a NEMA 17 stepper motor", "auto")
    )

    assert used == "standard-agent"
    assert doc.name == "nema17-motor-bracket"


def test_configured_rule_mode_is_not_bypassed_by_agent(settings):
    from app.services.planners.factory import PlannerFactory

    doc, used = asyncio.run(PlannerFactory(settings).plan("NEMA17 馬達支架", "auto"))

    assert used == "rule"
    assert doc.name != "nema17-motor-bracket"


def test_nema17_agent_allows_bracket_parameter_overrides():
    doc = plan("NEMA17 馬達支架，板厚5mm，支架寬70mm，底板深60mm，立板高56mm")

    assert (doc.base.width, doc.base.depth, doc.base.vertical_height, doc.base.thickness) == (
        70,
        60,
        56,
        5,
    )
    motor_center_hole = next(
        hole for hole in doc.holes if hole.axis == Axis.Y and hole.thread is None
    )
    assert motor_center_hole.z == 33


def test_nema17_agent_detects_compact_chinese_and_converts_units():
    doc = plan("製作NEMA17馬達支架，板厚0.2in，支架寬7cm")

    assert doc.base.thickness == 5.08
    assert doc.base.width == 70


def test_validator_blocks_nema17_geometry_that_claims_standard_provenance():
    from app.services.validator import DesignValidator

    doc = plan("NEMA17 馬達支架")
    doc.holes[0].x = -14

    report = DesignValidator().validate(doc)

    assert not report.valid
    assert any(issue.code == "standard_geometry_mismatch" for issue in report.issues)


def test_validator_blocks_blind_hole_under_nema17_provenance():
    from app.models.cad import HoleType
    from app.services.validator import DesignValidator

    doc = plan("NEMA17 馬達支架")
    doc.holes[0].hole_type = HoleType.BLIND
    doc.holes[0].depth = 1

    report = DesignValidator().validate(doc)

    assert not report.valid
    assert any(issue.code == "standard_geometry_mismatch" for issue in report.issues)

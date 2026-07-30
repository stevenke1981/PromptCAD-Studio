from __future__ import annotations

import asyncio
import json

from app.core.config import Settings
from app.services.planners.base import PlannerError
from app.services.planners.factory import PlannerFactory
from app.services.planners.llm import OpenAICompatiblePlanner


def _intent_payload():
    return {
        "name": "llm-plate",
        "template": "plate",
        "unit": "mm",
        "material": "aluminum",
        "parameters": {
            "length": 120,
            "width": 60,
            "height": None,
            "thickness": 10,
            "diameter": None,
            "outer_diameter": None,
            "inner_diameter": None,
            "depth": None,
            "vertical_height": None,
            "wall_thickness": None,
            "edge_margin": 10,
        },
        "holes": [],
        "fillet_radius": 5,
        "chamfer_distance": None,
        "assumptions": [],
        "notes": [],
        "confidence": 0.98,
        "review_required": False,
    }


def test_llm_planner_sends_strict_json_schema(monkeypatch, tmp_path):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {"message": {"content": json.dumps(_intent_payload())}}
                ]
            }

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr("app.services.planners.llm.httpx.AsyncClient", FakeClient)
    settings = Settings(
        env="test",
        data_dir=tmp_path,
        llm_base_url="http://127.0.0.1:8080/v1",
        llm_api_key="local",
        llm_model="test-model",
        llm_structured_mode="json_schema",
    )
    doc = asyncio.run(OpenAICompatiblePlanner(settings).plan("make a plate"))

    assert captured["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer local"
    assert captured["payload"]["temperature"] == 0
    assert captured["payload"]["response_format"]["type"] == "json_schema"
    assert captured["payload"]["response_format"]["json_schema"]["strict"] is True
    assert doc.base.kind == "plate"
    assert doc.base.length == 120
    assert doc.fillets[0].radius == 5


def test_auto_planner_falls_back_to_rules(monkeypatch, tmp_path):
    settings = Settings(
        env="test",
        data_dir=tmp_path,
        planner_mode="auto",
        llm_base_url="http://127.0.0.1:8080/v1",
        llm_api_key="local",
        llm_model="test-model",
        llm_fallback_to_rule=True,
    )
    factory = PlannerFactory(settings)

    async def fail(_prompt):
        raise PlannerError("provider unavailable")

    monkeypatch.setattr(factory.llm, "plan", fail)
    doc, planner_used = asyncio.run(factory.plan("長100寬50厚8的板", "auto"))
    assert planner_used == "rule-fallback"
    assert doc.base.kind == "plate"
    assert doc.base.length == 100

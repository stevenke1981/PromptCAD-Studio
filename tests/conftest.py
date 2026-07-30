from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        env="test",
        data_dir=tmp_path / "generated",
        planner_mode="rule",
        render_backend="source_only",
        allow_source_fallback=True,
        api_token=None,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_manufacturing_review_transition_body_is_bounded_on_dynamic_job_path(
    tmp_path,
) -> None:
    settings = Settings(
        env="test",
        data_dir=tmp_path / "generated",
        planner_mode="rule",
        render_backend="source_only",
        allow_source_fallback=True,
        api_token=None,
        max_generate_body_bytes=10_000,
    )
    oversized = b'{"note":"' + b"x" * 10_000 + b'"}'

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/jobs/01234567-89ab-cdef-0123-456789abcdef/"
            "manufacturing-review/transitions",
            content=oversized,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body is too large"}

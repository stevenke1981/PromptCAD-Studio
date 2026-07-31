from __future__ import annotations

from app.worker import AsyncJobWorker


def _request() -> dict:
    return {
        "prompt": "畫一個長80mm、寬40mm、厚5mm的固定板",
        "planner": "rule",
        "formats": ["json", "py"],
        "render": False,
        "backend": "source_only",
    }


def test_async_api_submit_worker_poll_and_result(client, settings) -> None:
    response = client.post("/api/v1/async/generate", json=_request())
    assert response.status_code == 202
    queued = response.json()
    assert queued["status"] == "queued"

    worker = AsyncJobWorker(
        settings,
        queue=client.app.state.async_queue,
        worker_id="api-test-worker",
    )
    try:
        assert worker.process_next() is True
    finally:
        worker.close()

    status = client.get(f"/api/v1/async/jobs/{queued['queue_job_id']}")
    assert status.status_code == 200
    completed = status.json()
    assert completed["status"] == "completed"
    assert completed["result_url"].startswith("/api/v1/jobs/")
    manifest = client.get(completed["result_url"])
    assert manifest.status_code == 200
    assert manifest.json()["status"] == "source_only"
    assert client.get("/api/v1/async/jobs?limit=1").json()[0] == completed


def test_async_api_can_cancel_queued_job(client) -> None:
    queued = client.post("/api/v1/async/generate", json=_request()).json()
    response = client.post(
        f"/api/v1/async/jobs/{queued['queue_job_id']}/cancel"
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancellation_requested"] is True


def test_async_api_rejects_unknown_and_invalid_ids(client) -> None:
    missing = client.get(f"/api/v1/async/jobs/{'a' * 32}")
    invalid = client.get("/api/v1/async/jobs/not-valid")
    assert missing.status_code == 404
    assert invalid.status_code == 422


def test_async_api_enforces_runtime_prompt_limit(client, settings) -> None:
    payload = _request()
    payload["prompt"] = "x" * (settings.max_prompt_chars + 1)
    assert client.post("/api/v1/generate", json=payload).status_code == 422
    assert client.post("/api/v1/async/generate", json=payload).status_code == 422

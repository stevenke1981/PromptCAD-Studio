from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.services.async_queue import AsyncJobQueue, QueueFullError
from app.worker import AsyncJobWorker


def _prompt_payload() -> dict:
    return {
        "prompt": "畫一個長80mm、寬40mm、厚5mm的固定板",
        "planner": "rule",
        "formats": ["json", "py"],
        "render": False,
        "backend": "source_only",
    }


def test_queue_claim_is_atomic_and_completion_is_durable(settings) -> None:
    queue = AsyncJobQueue(settings)
    queued = queue.enqueue("prompt", _prompt_payload())
    peers = [AsyncJobQueue(settings), AsyncJobQueue(settings)]

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda item: item.claim("worker-a"), peers))

    claimed = [item for item in claims if item is not None]
    assert len(claimed) == 1
    assert claimed[0].queue_job_id == queued.queue_job_id
    completed = queue.complete(queued.queue_job_id, "worker-a", "a" * 32)
    assert completed.status == "completed"
    assert completed.result_url == f"/api/v1/jobs/{'a' * 32}"
    assert AsyncJobQueue(settings).get(queued.queue_job_id).status == "completed"


def test_queue_capacity_and_queued_cancellation(settings) -> None:
    limited = settings.model_copy(update={"async_queue_max_pending": 1})
    queue = AsyncJobQueue(limited)
    first = queue.enqueue("prompt", _prompt_payload())

    with pytest.raises(QueueFullError):
        queue.enqueue("prompt", _prompt_payload())

    cancelled = queue.cancel(first.queue_job_id)
    assert cancelled.status == "cancelled"
    assert cancelled.cancellation_requested is True
    assert queue.claim("worker-a") is None
    assert queue.enqueue("prompt", _prompt_payload()).status == "queued"


def test_expired_worker_lease_is_requeued(settings) -> None:
    queue = AsyncJobQueue(settings)
    queued = queue.enqueue("prompt", _prompt_payload())
    assert queue.claim("worker-a") is not None
    with sqlite3.connect(settings.queue_db_path) as connection:
        connection.execute(
            "UPDATE queue_jobs SET lease_expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", queued.queue_job_id),
        )

    recovered = queue.claim("worker-b")
    assert recovered is not None
    assert recovered.queue_job_id == queued.queue_job_id
    assert recovered.attempts == 2


def test_running_cancellation_wins_over_late_worker_completion(settings) -> None:
    queue = AsyncJobQueue(settings)
    queued = queue.enqueue("prompt", _prompt_payload())
    assert queue.claim("worker-a") is not None
    cancellation = queue.cancel(queued.queue_job_id)
    assert cancellation.status == "running"
    assert cancellation.cancellation_requested is True

    terminal = queue.complete(queued.queue_job_id, "worker-a", "b" * 32)
    assert terminal.status == "cancelled"
    assert terminal.result_job_id == "b" * 32
    assert terminal.error == "Cancelled by user request."


def test_worker_processes_prompt_job_and_publishes_manifest(settings) -> None:
    queue = AsyncJobQueue(settings)
    queued = queue.enqueue("prompt", _prompt_payload())
    worker = AsyncJobWorker(
        settings,
        queue=queue,
        worker_id="test-worker",
    )
    try:
        assert worker.process_next() is True
        assert worker.process_next() is False
    finally:
        worker.close()

    completed = queue.get(queued.queue_job_id)
    assert completed.status == "completed"
    assert completed.result_job_id is not None
    manifest = settings.data_dir / completed.result_job_id / "manifest.json"
    assert manifest.is_file()


def test_worker_survives_when_an_expired_lease_is_reclaimed(settings) -> None:
    queue = AsyncJobQueue(settings)
    queued = queue.enqueue("prompt", _prompt_payload())

    class LeaseStealingService:
        async def generate(self, *_args):
            with sqlite3.connect(settings.queue_db_path) as connection:
                connection.execute(
                    "UPDATE queue_jobs SET lease_expires_at = ? WHERE id = ?",
                    ("2000-01-01T00:00:00+00:00", queued.queue_job_id),
                )
            assert queue.claim("worker-b") is not None
            return SimpleNamespace(status="source_only", job_id="d" * 32, error=None)

        def close(self) -> None:
            return None

    worker = AsyncJobWorker(
        settings,
        queue=queue,
        service=LeaseStealingService(),
        worker_id="worker-a",
    )
    assert worker.process_next() is True
    recovered = queue.get(queued.queue_job_id)
    assert recovered.status == "running"
    assert recovered.attempts == 2


def test_worker_marks_corrupt_payload_failed_without_exiting(settings) -> None:
    queue = AsyncJobQueue(settings)
    queued = queue.enqueue("prompt", _prompt_payload())
    with sqlite3.connect(settings.queue_db_path) as connection:
        connection.execute(
            "UPDATE queue_jobs SET payload_json = ? WHERE id = ?",
            ("not-json", queued.queue_job_id),
        )
    worker = AsyncJobWorker(
        settings,
        queue=queue,
        worker_id="payload-test-worker",
    )
    try:
        assert worker.process_next() is True
    finally:
        worker.close()
    failed = queue.get(queued.queue_job_id)
    assert failed.status == "failed"
    assert failed.error == "Queue payload is invalid JSON"


def test_cancel_race_updates_result_manifest_to_cancelled(settings, monkeypatch) -> None:
    queue = AsyncJobQueue(settings)
    queued = queue.enqueue("prompt", _prompt_payload())
    worker = AsyncJobWorker(
        settings,
        queue=queue,
        worker_id="cancel-race-worker",
    )
    completed_manifest = asyncio.run(
        worker.service.generate(
            _prompt_payload()["prompt"],
            "rule",
            ["json", "py"],
            False,
            "source_only",
        )
    )

    async def finish_as_cancellation_arrives(*_args):
        queue.cancel(queued.queue_job_id)
        return completed_manifest

    monkeypatch.setattr(worker.service, "generate", finish_as_cancellation_arrives)
    try:
        assert worker.process_next() is True
    finally:
        worker.close()

    terminal = queue.get(queued.queue_job_id)
    persisted = settings.data_dir / completed_manifest.job_id / "manifest.json"
    assert terminal.status == "cancelled"
    assert terminal.result_job_id == completed_manifest.job_id
    assert '"status": "cancelled"' in persisted.read_text(encoding="utf-8")

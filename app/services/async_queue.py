from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.core.config import Settings
from app.models.api import QueueJobResponse

QUEUE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
QueueKind = Literal["prompt", "spec"]


class QueueFullError(RuntimeError):
    pass


class QueueJobNotFound(KeyError):
    pass


@dataclass(frozen=True, slots=True)
class ClaimedQueueJob:
    queue_job_id: str
    kind: QueueKind
    payload: dict[str, Any]
    attempts: int
    worker_id: str


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


class AsyncJobQueue:
    """SQLite-backed durable queue with atomic claims and cooperative cancellation."""

    def __init__(self, settings: Settings):
        self.path = settings.queue_db_path
        self.max_pending = settings.async_queue_max_pending
        self.lease_seconds = settings.async_queue_lease_seconds
        self.max_attempts = settings.async_queue_max_attempts
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS queue_jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK (kind IN ('prompt', 'spec')),
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('queued', 'running', 'completed', 'failed', 'cancelled')
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    worker_id TEXT,
                    lease_expires_at TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (
                        cancel_requested IN (0, 1)
                    ),
                    result_job_id TEXT,
                    error TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS queue_jobs_status_created "
                "ON queue_jobs(status, created_at)"
            )

    def enqueue(self, kind: QueueKind, payload: dict[str, Any]) -> QueueJobResponse:
        if kind not in {"prompt", "spec"}:
            raise ValueError("Unsupported async job kind")
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        queue_job_id = uuid.uuid4().hex
        now = _iso(_now())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                active = connection.execute(
                    "SELECT COUNT(*) FROM queue_jobs WHERE status IN ('queued', 'running')"
                ).fetchone()[0]
                if active >= self.max_pending:
                    raise QueueFullError(
                        f"Async queue is full ({self.max_pending} pending/running jobs)"
                    )
                connection.execute(
                    """
                    INSERT INTO queue_jobs (
                        id, kind, payload_json, status, created_at, updated_at
                    ) VALUES (?, ?, ?, 'queued', ?, ?)
                    """,
                    (queue_job_id, kind, payload_json, now, now),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get(queue_job_id)

    def get(self, queue_job_id: str) -> QueueJobResponse:
        self._validate_id(queue_job_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM queue_jobs WHERE id = ?",
                (queue_job_id,),
            ).fetchone()
        if row is None:
            raise QueueJobNotFound(queue_job_id)
        return self._response(row)

    def list(self, limit: int = 50) -> list[QueueJobResponse]:
        safe_limit = min(max(1, limit), 500)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM queue_jobs ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [self._response(row) for row in rows]

    def claim(self, worker_id: str) -> ClaimedQueueJob | None:
        worker_id = self._validated_worker_id(worker_id)
        now_value = _now()
        now = _iso(now_value)
        lease = _iso(now_value + timedelta(seconds=self.lease_seconds))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._recover_expired(connection, now)
                row = connection.execute(
                    """
                    SELECT * FROM queue_jobs
                    WHERE status = 'queued' AND cancel_requested = 0
                    ORDER BY created_at ASC
                    LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                updated = connection.execute(
                    """
                    UPDATE queue_jobs
                    SET status = 'running', updated_at = ?,
                        started_at = COALESCE(started_at, ?), worker_id = ?,
                        lease_expires_at = ?, attempts = attempts + 1
                    WHERE id = ? AND status = 'queued' AND cancel_requested = 0
                    """,
                    (now, now, worker_id, lease, row["id"]),
                )
                if updated.rowcount != 1:
                    connection.rollback()
                    return None
                claimed = connection.execute(
                    "SELECT * FROM queue_jobs WHERE id = ?",
                    (row["id"],),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        try:
            payload = json.loads(claimed["payload_json"])
        except json.JSONDecodeError as exc:
            self.fail(claimed["id"], worker_id, "Queue payload is invalid JSON", retry=False)
            raise RuntimeError("Queue payload is invalid JSON") from exc
        if not isinstance(payload, dict):
            self.fail(claimed["id"], worker_id, "Queue payload must be an object", retry=False)
            raise RuntimeError("Queue payload must be an object")
        return ClaimedQueueJob(
            queue_job_id=claimed["id"],
            kind=claimed["kind"],
            payload=payload,
            attempts=claimed["attempts"],
            worker_id=worker_id,
        )

    def heartbeat(self, queue_job_id: str, worker_id: str) -> bool:
        self._validate_id(queue_job_id)
        worker_id = self._validated_worker_id(worker_id)
        now_value = _now()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE queue_jobs
                SET updated_at = ?, lease_expires_at = ?
                WHERE id = ? AND status = 'running' AND worker_id = ?
                """,
                (
                    _iso(now_value),
                    _iso(now_value + timedelta(seconds=self.lease_seconds)),
                    queue_job_id,
                    worker_id,
                ),
            )
        return updated.rowcount == 1

    def is_cancel_requested(self, queue_job_id: str) -> bool:
        self._validate_id(queue_job_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested, status FROM queue_jobs WHERE id = ?",
                (queue_job_id,),
            ).fetchone()
        if row is None:
            return True
        return bool(row["cancel_requested"]) or row["status"] == "cancelled"

    def cancel(self, queue_job_id: str) -> QueueJobResponse:
        self._validate_id(queue_job_id)
        now = _iso(_now())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status FROM queue_jobs WHERE id = ?",
                    (queue_job_id,),
                ).fetchone()
                if row is None:
                    raise QueueJobNotFound(queue_job_id)
                if row["status"] == "queued":
                    connection.execute(
                        """
                        UPDATE queue_jobs
                        SET status = 'cancelled', cancel_requested = 1,
                            updated_at = ?, completed_at = ?, lease_expires_at = NULL
                        WHERE id = ?
                        """,
                        (now, now, queue_job_id),
                    )
                elif row["status"] == "running":
                    connection.execute(
                        """
                        UPDATE queue_jobs
                        SET cancel_requested = 1, updated_at = ?
                        WHERE id = ?
                        """,
                        (now, queue_job_id),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get(queue_job_id)

    def complete(
        self,
        queue_job_id: str,
        worker_id: str,
        result_job_id: str,
    ) -> QueueJobResponse:
        return self._finish(
            queue_job_id,
            worker_id,
            status="completed",
            result_job_id=result_job_id,
            error=None,
        )

    def mark_cancelled(
        self,
        queue_job_id: str,
        worker_id: str,
        result_job_id: str | None = None,
    ) -> QueueJobResponse:
        return self._finish(
            queue_job_id,
            worker_id,
            status="cancelled",
            result_job_id=result_job_id,
            error="Cancelled by user request.",
        )

    def mark_failed(
        self,
        queue_job_id: str,
        worker_id: str,
        error: str,
        result_job_id: str | None = None,
    ) -> QueueJobResponse:
        return self._finish(
            queue_job_id,
            worker_id,
            status="failed",
            result_job_id=result_job_id,
            error=str(error)[:4_000],
        )

    def fail(
        self,
        queue_job_id: str,
        worker_id: str,
        error: str,
        *,
        retry: bool = True,
    ) -> QueueJobResponse:
        self._validate_id(queue_job_id)
        worker_id = self._validated_worker_id(worker_id)
        now = _iso(_now())
        detail = str(error)[:4_000]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM queue_jobs WHERE id = ?",
                    (queue_job_id,),
                ).fetchone()
                if row is None:
                    raise QueueJobNotFound(queue_job_id)
                if row["status"] != "running" or row["worker_id"] != worker_id:
                    raise RuntimeError("Worker no longer owns this queue job")
                cancelled = bool(row["cancel_requested"])
                should_retry = retry and not cancelled and row["attempts"] < self.max_attempts
                if should_retry:
                    connection.execute(
                        """
                        UPDATE queue_jobs
                        SET status = 'queued', updated_at = ?, worker_id = NULL,
                            lease_expires_at = NULL, error = ?
                        WHERE id = ?
                        """,
                        (now, detail, queue_job_id),
                    )
                else:
                    status = "cancelled" if cancelled else "failed"
                    connection.execute(
                        """
                        UPDATE queue_jobs
                        SET status = ?, updated_at = ?, completed_at = ?,
                            lease_expires_at = NULL, error = ?
                        WHERE id = ?
                        """,
                        (status, now, now, detail, queue_job_id),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get(queue_job_id)

    def _finish(
        self,
        queue_job_id: str,
        worker_id: str,
        *,
        status: Literal["completed", "cancelled", "failed"],
        result_job_id: str | None,
        error: str | None,
    ) -> QueueJobResponse:
        self._validate_id(queue_job_id)
        worker_id = self._validated_worker_id(worker_id)
        now = _iso(_now())
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE queue_jobs
                SET status = ?, updated_at = ?, completed_at = ?,
                    lease_expires_at = NULL, result_job_id = ?, error = ?
                WHERE id = ? AND status = 'running' AND worker_id = ?
                """,
                (status, now, now, result_job_id, error, queue_job_id, worker_id),
            )
        if updated.rowcount != 1:
            raise RuntimeError("Worker no longer owns this queue job")
        return self.get(queue_job_id)

    def _recover_expired(self, connection: sqlite3.Connection, now: str) -> None:
        expired = connection.execute(
            """
            SELECT id, attempts, cancel_requested FROM queue_jobs
            WHERE status = 'running' AND lease_expires_at < ?
            """,
            (now,),
        ).fetchall()
        for row in expired:
            if row["cancel_requested"]:
                connection.execute(
                    """
                    UPDATE queue_jobs
                    SET status = 'cancelled', updated_at = ?, completed_at = ?,
                        worker_id = NULL, lease_expires_at = NULL,
                        error = 'Cancelled after worker lease expired.'
                    WHERE id = ?
                    """,
                    (now, now, row["id"]),
                )
            elif row["attempts"] >= self.max_attempts:
                connection.execute(
                    """
                    UPDATE queue_jobs
                    SET status = 'failed', updated_at = ?, completed_at = ?,
                        worker_id = NULL, lease_expires_at = NULL,
                        error = 'Worker lease expired; retry limit reached.'
                    WHERE id = ?
                    """,
                    (now, now, row["id"]),
                )
            else:
                connection.execute(
                    """
                    UPDATE queue_jobs
                    SET status = 'queued', updated_at = ?, worker_id = NULL,
                        lease_expires_at = NULL,
                        error = 'Worker lease expired; job requeued.'
                    WHERE id = ?
                    """,
                    (now, row["id"]),
                )

    @staticmethod
    def _response(row: sqlite3.Row) -> QueueJobResponse:
        result_job_id = row["result_job_id"]
        return QueueJobResponse(
            queue_job_id=row["id"],
            kind=row["kind"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            attempts=row["attempts"],
            cancellation_requested=bool(row["cancel_requested"]),
            result_job_id=result_job_id,
            result_url=(
                f"/api/v1/jobs/{result_job_id}" if result_job_id else None
            ),
            error=row["error"],
        )

    @staticmethod
    def _validate_id(queue_job_id: str) -> None:
        if not QUEUE_ID_PATTERN.fullmatch(queue_job_id):
            raise ValueError("Invalid async queue job id")

    @staticmethod
    def _validated_worker_id(worker_id: str) -> str:
        value = worker_id.strip()
        if not value or len(value) > 128 or any(ord(char) < 32 for char in value):
            raise ValueError("Invalid worker id")
        return value

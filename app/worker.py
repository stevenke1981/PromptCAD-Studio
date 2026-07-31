from __future__ import annotations

import argparse
import asyncio
import os
import signal
import socket
import threading
import uuid

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.models.api import GenerateFromSpecRequest, GenerateRequest
from app.services.async_queue import (
    AsyncJobQueue,
    ClaimedQueueJob,
    QueueJobNotFound,
    QueuePayloadError,
)
from app.services.cancellation import JobCancelled
from app.services.job_service import JobService


class AsyncJobWorker:
    def __init__(
        self,
        settings: Settings,
        *,
        queue: AsyncJobQueue | None = None,
        service: JobService | None = None,
        worker_id: str | None = None,
    ):
        self.settings = settings
        self.queue = queue or AsyncJobQueue(settings)
        self.service = service or JobService(settings)
        self.worker_id = worker_id or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )

    def close(self) -> None:
        self.service.close()

    def process_next(self) -> bool:
        try:
            claimed = self.queue.claim(self.worker_id)
        except QueuePayloadError:
            # claim() already moved this malformed row to failed.
            return True
        if claimed is None:
            return False
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(claimed.queue_job_id, heartbeat_stop),
            name=f"promptcad-heartbeat-{claimed.queue_job_id[:8]}",
            daemon=True,
        )
        heartbeat.start()
        try:
            self._process(claimed)
        except JobCancelled:
            self._mark_cancelled_if_owned(claimed.queue_job_id)
        except (ValidationError, ValueError) as exc:
            self._fail_if_owned(
                claimed.queue_job_id,
                f"Invalid queued request: {exc}",
                retry=False,
            )
        except Exception as exc:
            self._fail_if_owned(
                claimed.queue_job_id,
                f"Worker execution failed: {exc}",
                retry=True,
            )
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=2)
        return True

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            if not self.process_next():
                stop.wait(self.settings.worker_poll_seconds)

    def _process(self, claimed: ClaimedQueueJob) -> None:
        def cancel_check() -> bool:
            return self.queue.is_cancel_requested(claimed.queue_job_id)
        if cancel_check():
            raise JobCancelled("Job cancelled before worker execution")
        if claimed.kind == "prompt":
            request = GenerateRequest.model_validate(claimed.payload)
            manifest = asyncio.run(
                self.service.generate(
                    request.prompt,
                    request.planner,
                    request.formats,
                    request.render,
                    request.backend,
                    cancel_check,
                )
            )
        elif claimed.kind == "spec":
            request = GenerateFromSpecRequest.model_validate(claimed.payload)
            manifest = asyncio.run(
                self.service.generate_from_spec(
                    request.spec,
                    request.formats,
                    request.render,
                    request.backend,
                    cancel_check,
                )
            )
        else:
            raise ValueError(f"Unsupported queued job kind: {claimed.kind}")
        if manifest.status == "cancelled" or cancel_check():
            terminal = self.queue.mark_cancelled(
                claimed.queue_job_id,
                self.worker_id,
                manifest.job_id,
            )
        elif manifest.status == "failed":
            terminal = self.queue.mark_failed(
                claimed.queue_job_id,
                self.worker_id,
                manifest.error or "CAD generation failed",
                manifest.job_id,
            )
        else:
            terminal = self.queue.complete(
                claimed.queue_job_id,
                self.worker_id,
                manifest.job_id,
            )
        if terminal.status == "cancelled" and manifest.status != "cancelled":
            self.service.mark_cancelled(manifest)

    def _mark_cancelled_if_owned(self, queue_job_id: str) -> None:
        try:
            self.queue.mark_cancelled(queue_job_id, self.worker_id)
        except (RuntimeError, QueueJobNotFound):
            # A recovered lease belongs to another worker; this worker must stop
            # publishing state without terminating the worker process.
            return

    def _fail_if_owned(self, queue_job_id: str, error: str, *, retry: bool) -> None:
        try:
            self.queue.fail(
                queue_job_id,
                self.worker_id,
                error,
                retry=retry,
            )
        except (RuntimeError, QueueJobNotFound):
            # Lease ownership can change while native CAD code is still exiting.
            return

    def _heartbeat_loop(self, queue_job_id: str, stop: threading.Event) -> None:
        interval = max(1.0, self.settings.async_queue_lease_seconds / 3)
        while not stop.wait(interval):
            try:
                if not self.queue.heartbeat(queue_job_id, self.worker_id):
                    return
            except Exception:
                # A transient heartbeat failure is retried before the lease expires.
                continue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="promptcad-worker",
        description="Run the durable PromptCAD CAD worker",
    )
    parser.add_argument("--once", action="store_true", help="Process at most one job")
    parser.add_argument(
        "--poll-seconds",
        type=float,
        help="Override idle queue polling interval for this process",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    if args.poll_seconds is not None:
        if not 0.05 <= args.poll_seconds <= 30:
            raise SystemExit("--poll-seconds must be between 0.05 and 30")
        settings.worker_poll_seconds = args.poll_seconds
    settings.ensure_directories()
    worker = AsyncJobWorker(settings)
    stop = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stop.set()

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), request_stop)
    try:
        if args.once:
            processed = worker.process_next()
            print("processed" if processed else "queue empty")
            raise SystemExit(0)
        print(f"PromptCAD worker started: {worker.worker_id}")
        worker.run_forever(stop)
    except KeyboardInterrupt:
        stop.set()
    finally:
        worker.close()


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from app.core.config import Settings


class JobStorage:
    def __init__(self, settings: Settings):
        self.root = settings.data_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self) -> tuple[str, Path]:
        job_id = uuid.uuid4().hex
        path = self.root / job_id
        path.mkdir(parents=False, exist_ok=False)
        return job_id, path

    def path(self, job_id: str) -> Path:
        return self.root / job_id

    def write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def write_bytes(
        self,
        path: Path,
        content: bytes,
        *,
        overwrite: bool = True,
    ) -> None:
        """Atomically persist a binary artifact in the destination directory."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if overwrite:
                os.replace(temp, path)
            else:
                # Linking a complete, fsynced temporary file is an atomic
                # create-if-absent operation on the destination filesystem.
                os.link(temp, path)
                os.unlink(temp)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def write_json(self, path: Path, value: Any) -> None:
        self.write_text(path, json.dumps(value, ensure_ascii=False, indent=2))

    def write_json_once(self, path: Path, value: Any) -> None:
        content = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        self.write_bytes(path, content, overwrite=False)

    def read_manifest(self, job_id: str) -> dict[str, Any] | None:
        path = self.path(job_id) / "manifest.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_manifests(self, limit: int) -> list[dict[str, Any]]:
        candidates = sorted(
            (p for p in self.root.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        manifests = []
        for directory in candidates:
            path = directory / "manifest.json"
            if not path.is_file():
                continue
            try:
                manifests.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
            if len(manifests) >= limit:
                break
        return manifests

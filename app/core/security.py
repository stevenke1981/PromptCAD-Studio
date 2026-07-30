from __future__ import annotations

import hmac
import re
from pathlib import Path

from fastapi import Header, HTTPException, Request, status

_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


async def require_api_token(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    expected = request.app.state.settings.api_token
    if not expected:
        return

    supplied = x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()

    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token")


def validate_job_id(job_id: str) -> str:
    if not _JOB_ID.fullmatch(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return job_id


def safe_job_file(job_dir: Path, filename: str) -> Path:
    if not _FILENAME.fullmatch(filename):
        raise HTTPException(status_code=404, detail="File not found")
    candidate = (job_dir / filename).resolve()
    root = job_dir.resolve()
    if candidate.parent != root:
        raise HTTPException(status_code=404, detail="File not found")
    return candidate

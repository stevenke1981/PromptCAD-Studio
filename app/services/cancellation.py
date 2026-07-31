from __future__ import annotations

from collections.abc import Callable

CancelCheck = Callable[[], bool]


class JobCancelled(RuntimeError):
    """Raised when a durable queue cancellation request reaches active work."""


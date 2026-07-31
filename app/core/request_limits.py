from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any


class RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Reject oversized request bodies before multipart parsing and spooling."""

    def __init__(
        self,
        app,
        *,
        path: str | None = None,
        path_prefix: str | None = None,
        path_suffix: str | None = None,
        max_body_bytes: int,
        max_concurrency: int,
    ):
        self.app = app
        self.path = path
        self.path_prefix = path_prefix
        self.path_suffix = path_suffix
        self.max_body_bytes = max_body_bytes
        self._slots = asyncio.Semaphore(max_concurrency)

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        request_path = str(scope.get("path", ""))
        matches_path = request_path == self.path if self.path is not None else False
        if self.path_prefix is not None and self.path_suffix is not None:
            matches_path = request_path.startswith(
                self.path_prefix
            ) and request_path.endswith(self.path_suffix)
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or not matches_path
        ):
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_body_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                await self._reject(send)
                return

        received = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise RequestBodyTooLarge
            return message

        async with self._slots:
            try:
                await self.app(scope, limited_receive, send)
            except RequestBodyTooLarge:
                await self._reject(send)

    @staticmethod
    async def _reject(
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        body = json.dumps({"detail": "Request body is too large"}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

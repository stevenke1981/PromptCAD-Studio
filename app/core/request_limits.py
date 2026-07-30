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
        path: str,
        max_body_bytes: int,
        max_concurrency: int,
    ):
        self.app = app
        self.path = path
        self.max_body_bytes = max_body_bytes
        self._slots = asyncio.Semaphore(max_concurrency)

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != self.path
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

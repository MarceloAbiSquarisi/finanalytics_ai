"""
Propagacao de correlation_id via structlog contextvars.

Uso na API:
    app.add_middleware(CorrelationMiddleware)

Uso no worker (por evento):
    bind_correlation_id(str(event.event_id))
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import uuid

import structlog

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

CORRELATION_HEADER = "X-Correlation-ID"


def bind_correlation_id(correlation_id: str | None = None) -> str:
    cid = correlation_id or str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(correlation_id=cid)
    return cid


def clear_correlation_id() -> None:
    structlog.contextvars.unbind_contextvars("correlation_id")


class CorrelationMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        incoming = headers.get(CORRELATION_HEADER.lower().encode(), b"").decode()
        cid = bind_correlation_id(incoming or None)

        async def _send_with_header(message: dict) -> None:
            if message["type"] == "http.response.start":
                hdrs = list(message.get("headers", []))
                hdrs.append((CORRELATION_HEADER.lower().encode(), cid.encode()))
                message = {**message, "headers": hdrs}
            await send(message)

        try:
            await self.app(scope, receive, _send_with_header)
        finally:
            clear_correlation_id()

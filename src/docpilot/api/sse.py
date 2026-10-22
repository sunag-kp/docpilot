"""Server-Sent Events encoding with keep-alive pings for slow generators."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

KEEPALIVE = b": keep-alive\n\n"


def encode(event: str, data: Any) -> bytes:
    payload = json.dumps(data, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode()


async def with_keepalive(
    source: AsyncIterator[bytes], interval_seconds: float = 15.0
) -> AsyncIterator[bytes]:
    """Re-yield ``source``; emit an SSE comment whenever it is silent for ``interval``.

    Proxies and load balancers close idle connections; a slow local model can take longer
    than their timeout to produce the first token. The pending ``__anext__`` is awaited as a
    task (not with ``wait_for``) so a timeout never cancels the underlying generator.
    """
    iterator = source.__aiter__()
    pending: asyncio.Task[bytes] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=interval_seconds)
            if not done:
                yield KEEPALIVE
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
            yield chunk
    finally:
        if pending is not None:
            pending.cancel()

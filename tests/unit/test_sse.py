from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from docpilot.api import sse


def test_encode_event() -> None:
    assert sse.encode("token", {"text": "hi"}) == b'event: token\ndata: {"text":"hi"}\n\n'


async def test_keepalive_pings_during_silence_without_cancelling_the_source() -> None:
    finished = False

    async def slow() -> AsyncIterator[bytes]:
        nonlocal finished
        yield b"a"
        await asyncio.sleep(0.25)
        yield b"b"
        finished = True

    out = [chunk async for chunk in sse.with_keepalive(slow(), interval_seconds=0.1)]
    assert out[0] == b"a"
    assert out[-1] == b"b"
    assert out.count(sse.KEEPALIVE) >= 1
    assert finished


async def test_keepalive_cancels_pending_work_when_consumer_stops() -> None:
    cancelled = asyncio.Event()

    async def endless() -> AsyncIterator[bytes]:
        try:
            while True:
                await asyncio.sleep(10)
                yield b"x"
        finally:
            cancelled.set()

    stream = sse.with_keepalive(endless(), interval_seconds=0.05)
    assert await stream.__anext__() == sse.KEEPALIVE
    await stream.aclose()  # client disconnected
    await asyncio.wait_for(cancelled.wait(), timeout=1)

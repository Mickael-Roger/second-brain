"""Shared helpers for SSE-streaming endpoints.

Two pieces:

- ``sse_event(event_type, data)`` formats a JSON event into the
  ``event: …\\ndata: …\\n\\n`` wire format.
- ``with_heartbeat(stream)`` wraps an SSE byte stream so that we
  emit a ``: keepalive\\n\\n`` comment whenever no real event has
  flowed for a few seconds. Comment lines are ignored by the browser
  but stop proxies / NAT / browsers from treating the connection as
  idle and dropping it — the failure mode that surfaces as
  ``net::ERR_EMPTY_RESPONSE`` after 30-90s of LLM-only silence.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator


_HEARTBEAT_INTERVAL_SEC = 15.0
_HEARTBEAT_BYTES = b": keepalive\n\n"


def sse_event(event_type: str, data: dict) -> bytes:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


async def with_heartbeat(
    events: AsyncIterator[bytes],
    interval: float = _HEARTBEAT_INTERVAL_SEC,
) -> AsyncIterator[bytes]:
    """Wrap an SSE byte stream so we emit a heartbeat comment whenever
    no real event has flowed for ``interval`` seconds.

    A producer task pumps the upstream generator into a queue; the
    consumer waits on the queue with ``asyncio.wait_for(..., interval)``
    and yields a heartbeat each time the wait fires."""
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def producer() -> None:
        try:
            async for ev in events:
                await queue.put(ev)
        finally:
            await queue.put(None)  # sentinel — end of stream

    task = asyncio.create_task(producer())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                yield _HEARTBEAT_BYTES
                continue
            if item is None:
                return
            yield item
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

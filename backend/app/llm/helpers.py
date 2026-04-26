"""Non-streaming helpers built on top of the streaming providers.

Used by background jobs (Organize, future news/email summarization, …)
that don't need token-by-token output. Wraps the provider with bounded
retry on transient infrastructure failures (5xx, timeouts, connection
resets) so an upstream blip doesn't poison a long-running pass.
"""

from __future__ import annotations

import asyncio
import logging

from .router import get_llm_router
from .types import Message, TextBlock

log = logging.getLogger(__name__)

# Substrings that mark an error as worth retrying. We can't rely on a
# typed error class because the message comes from a heterogeneous mix of
# httpx exceptions, upstream HTTP bodies, and our own provider stream
# error events.
_RETRYABLE_MARKERS = (
    "503",
    "502",
    "500",
    "504",
    "connection refused",
    "connection reset",
    "transport failure",
    "remote connection failure",
    "delayed connect error",
    "upstream connect error",
    "timed out",
    "timeout",
    "no route to host",
    "dns",
    "temporarily unavailable",
)


def _is_retryable(error_msg: str) -> bool:
    em = (error_msg or "").lower()
    return any(marker in em for marker in _RETRYABLE_MARKERS)


async def _attempt(
    *,
    system: str,
    messages: list[Message],
    provider_name: str | None,
    model: str | None,
) -> tuple[str | None, str | None]:
    """One attempt. Returns (text, None) on success, (None, error_msg) on
    a soft error (raise inside-stream errors as messages so the caller
    can decide whether to retry)."""
    provider = get_llm_router().get(provider_name)
    text_buf: list[str] = []
    try:
        async for ev in provider.stream(
            messages=messages, tools=[], system=system, model=model
        ):
            if ev.type == "error":
                return None, ev.error or "LLM stream error"
            if ev.type == "text_delta" and ev.text:
                text_buf.append(ev.text)
            if ev.type == "message_done":
                text = "".join(text_buf)
                if text:
                    return text, None
                if ev.message:
                    return (
                        "".join(b.text for b in ev.message.content if isinstance(b, TextBlock)),
                        None,
                    )
                return "", None
    except Exception as exc:  # network / parse / provider crash
        return None, str(exc)
    return None, "LLM produced no message"


async def complete(
    system: str,
    messages: list[Message],
    *,
    provider_name: str | None = None,
    model: str | None = None,
    max_attempts: int = 3,
    initial_backoff_sec: float = 1.0,
) -> str:
    """Run an LLM turn and return the concatenated assistant text.

    Retries up to `max_attempts - 1` extra times on transient infrastructure
    errors (5xx, connection refused/reset, timeouts) with exponential
    backoff (1s, 2s, 4s by default). Non-retryable errors (4xx, malformed
    arguments, etc.) raise immediately.
    """
    last_error: str = ""
    for attempt in range(1, max_attempts + 1):
        text, error = await _attempt(
            system=system,
            messages=messages,
            provider_name=provider_name,
            model=model,
        )
        if error is None and text is not None:
            return text

        last_error = error or "no response"
        if attempt >= max_attempts or not _is_retryable(last_error):
            break

        delay = initial_backoff_sec * (2 ** (attempt - 1))
        log.warning(
            "LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
            attempt, max_attempts, last_error, delay,
        )
        await asyncio.sleep(delay)

    raise RuntimeError(last_error)

"""Non-streaming helpers built on top of the streaming providers.

Used by background jobs (Organize, future news/email summarization, …)
that don't need token-by-token output.
"""

from __future__ import annotations

from .router import get_llm_router
from .types import Message, TextBlock


async def complete(
    system: str,
    messages: list[Message],
    *,
    provider_name: str | None = None,
    model: str | None = None,
) -> str:
    """Run an LLM turn and return the concatenated assistant text.

    Tool calls are not invoked here — this helper assumes a no-tools call
    (jobs that need tools should use the chat orchestrator).
    """
    provider = get_llm_router().get(provider_name)
    text_buf: list[str] = []
    async for ev in provider.stream(
        messages=messages, tools=[], system=system, model=model
    ):
        if ev.type == "error":
            raise RuntimeError(ev.error or "LLM stream error")
        if ev.type == "text_delta" and ev.text:
            text_buf.append(ev.text)
        if ev.type == "message_done":
            text = "".join(text_buf)
            if text:
                return text
            if ev.message:
                return "".join(b.text for b in ev.message.content if isinstance(b, TextBlock))
            return ""
    raise RuntimeError("LLM produced no message")

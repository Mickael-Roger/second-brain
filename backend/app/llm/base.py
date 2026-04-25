"""LLMProvider protocol implemented by adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from .types import Message, StreamEvent, ToolDef


class LLMProvider(Protocol):
    """Streaming LLM client.

    Implementations must:
      - Stream `text_delta` events for assistant text as it arrives.
      - Emit a `tool_use` event for each fully-formed tool call.
      - Emit exactly one `message_done` event with the complete assistant `Message`
        (text + tool_use blocks) at the end of a turn.
      - Translate provider tool-call format to/from the unified types.
    """

    name: str
    model: str

    async def stream(
        self,
        *,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]: ...

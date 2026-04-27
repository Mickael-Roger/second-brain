"""LLM tool registry.

Holds tool definitions (name + JSON Schema for input) and dispatches calls
to async handlers. Implements the orchestrator's `ToolDispatcher` protocol.
The registry is built once at startup; new tool families register on import.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any

from app.llm.types import TextBlock, ToolDef, ToolResultBlock

log = logging.getLogger(__name__)

ToolHandler = Callable[[dict[str, Any]], Awaitable[ToolResultBlock]]


class ToolRegistry:
    def __init__(self) -> None:
        self._defs: dict[str, ToolDef] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: ToolHandler,
    ) -> None:
        if name in self._defs:
            raise ValueError(f"tool {name!r} is already registered")
        self._defs[name] = ToolDef(name=name, description=description, input_schema=input_schema)
        self._handlers[name] = handler

    def defs(self) -> list[ToolDef]:
        return list(self._defs.values())

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResultBlock:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResultBlock(
                tool_use_id="(unset)",
                content=[TextBlock(text=f"No tool named {name!r} is registered.")],
                is_error=True,
            )
        try:
            return await handler(arguments)
        except Exception as exc:
            log.exception("tool %s failed", name)
            return ToolResultBlock(
                tool_use_id="(unset)",
                content=[TextBlock(text=f"Tool {name!r} error: {exc!s}")],
                is_error=True,
            )


@lru_cache(maxsize=1)
def get_registry() -> ToolRegistry:
    """Construct the registry on first call. Importing the tool modules
    triggers their registration via side-effects."""
    registry = ToolRegistry()
    # Late imports so registration runs lazily on the first request.
    from . import anki_tools, chat_tools, daily_tools, news_tools, vault_tools

    vault_tools.register_all(registry)
    daily_tools.register_all(registry)
    chat_tools.register_all(registry)
    news_tools.register_all(registry)
    anki_tools.register_all(registry)
    return registry


def text_result(text: str, *, is_error: bool = False) -> ToolResultBlock:
    return ToolResultBlock(
        tool_use_id="(unset)",
        content=[TextBlock(text=text)],
        is_error=is_error,
    )

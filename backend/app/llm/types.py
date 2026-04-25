"""Provider-agnostic LLM message + streaming types.

Adapters translate to and from these on the way in and out. A `Message` is the
canonical record we persist to the chat markdown file and replay on resume.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    mime: str  # e.g. image/jpeg
    data: str  # base64-encoded


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: list[Union["TextBlock", "ImageBlock"]]
    is_error: bool = False


ContentBlock = Annotated[
    Union[TextBlock, ImageBlock, ToolUseBlock, ToolResultBlock],
    Field(discriminator="type"),
]


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: list[ContentBlock]


class ToolDef(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class StreamEvent(BaseModel):
    """Internal streaming event yielded by provider adapters and the orchestrator.

    `text_delta`: incremental token (`text`).
    `tool_use`: a complete tool call decoded from the stream (`tool_use`).
    `tool_result`: result of dispatching a tool (`tool_result`).
    `message_done`: end of one assistant turn; carries the full assistant `message`.
    `done`: end of the entire request.
    `error`: terminal error (`error`).
    """

    type: Literal[
        "text_delta",
        "tool_use",
        "tool_result",
        "message_done",
        "done",
        "error",
    ]
    text: str | None = None
    tool_use: ToolUseBlock | None = None
    tool_result: ToolResultBlock | None = None
    message: Message | None = None
    error: str | None = None

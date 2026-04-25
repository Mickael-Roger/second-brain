"""Chat endpoints: stream LLM replies via SSE, plus chat CRUD."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Literal

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth import current_user
from app.chat import persistence
from app.chat.orchestrator import run_chat
from app.db.connection import get_db
from app.db.models import Chat
from app.llm import get_llm_router
from app.llm.types import ImageBlock, Message, TextBlock

router = APIRouter(prefix="/api", tags=["chat"])
log = logging.getLogger(__name__)


# ---- DTOs ----------------------------------------------------------------


class TextBlockIn(BaseModel):
    type: Literal["text"]
    text: str


class ImageBlockIn(BaseModel):
    type: Literal["image"]
    mime: str
    data: str


ContentBlockIn = Annotated[TextBlockIn | ImageBlockIn, Field(discriminator="type")]


class ChatRequest(BaseModel):
    chat_id: str | None = None
    module_id: str | None = None
    provider: str | None = None
    model: str | None = None
    content: list[ContentBlockIn]


class ChatSummary(BaseModel):
    id: str
    title: str
    module_id: str | None
    model: str | None
    created_at: str
    updated_at: str
    archived: bool

    @classmethod
    def from_chat(cls, c: Chat) -> "ChatSummary":
        return cls(
            id=c.id,
            title=c.title,
            module_id=c.module_id,
            model=c.model,
            created_at=c.created_at.isoformat(),
            updated_at=c.updated_at.isoformat(),
            archived=c.archived,
        )


class ChatDetail(ChatSummary):
    messages: list[Message]


class ChatPatch(BaseModel):
    title: str | None = None
    archived: bool | None = None


class ProviderInfo(BaseModel):
    name: str
    kind: str
    models: list[str]
    default_model: str
    is_default: bool


# ---- Helpers -------------------------------------------------------------


def _to_blocks(content: list[ContentBlockIn]) -> list:
    out: list = []
    for b in content:
        if isinstance(b, TextBlockIn):
            out.append(TextBlock(text=b.text))
        elif isinstance(b, ImageBlockIn):
            out.append(ImageBlock(mime=b.mime, data=b.data))
    return out


def _sse(event_type: str, data: dict) -> bytes:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


# ---- Routes --------------------------------------------------------------


@router.get("/llm/providers", response_model=list[ProviderInfo])
def list_providers(_user: str = Depends(current_user)) -> list[ProviderInfo]:
    router_ = get_llm_router()
    default = router_.default_name()
    return [
        ProviderInfo(
            name=p["name"],
            kind=p["kind"],
            models=p["models"],
            default_model=p["default_model"],
            is_default=(p["name"] == default),
        )
        for p in router_.list_providers()
    ]


@router.get("/chats", response_model=list[ChatSummary])
def list_chats(
    module_id: str | None = None,
    archived: bool = False,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ChatSummary]:
    return [
        ChatSummary.from_chat(c)
        for c in persistence.list_chats(conn, module_id=module_id, archived=archived)
    ]


@router.post("/chats", response_model=ChatSummary, status_code=201)
def create_chat(
    module_id: str | None = None,
    title: str = "New chat",
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ChatSummary:
    c = persistence.create_chat(conn, title=title, module_id=module_id)
    return ChatSummary.from_chat(c)


@router.get("/chats/{chat_id}", response_model=ChatDetail)
def get_chat(
    chat_id: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ChatDetail:
    c = persistence.get_chat(conn, chat_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    msgs = persistence.read_messages(c)
    base = ChatSummary.from_chat(c).model_dump()
    return ChatDetail(**base, messages=msgs)


@router.patch("/chats/{chat_id}", response_model=ChatSummary)
def patch_chat(
    chat_id: str,
    payload: ChatPatch,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ChatSummary:
    c = persistence.get_chat(conn, chat_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    if payload.title is not None:
        c = persistence.rename_chat(conn, c, payload.title)
    if payload.archived is not None:
        c = persistence.set_archived(conn, c, payload.archived)
    return ChatSummary.from_chat(c)


@router.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat(
    chat_id: str,
    hard: bool = False,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    c = persistence.get_chat(conn, chat_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    persistence.delete_chat(conn, c, hard=hard)


@router.post("/chat")
async def chat_stream(
    payload: ChatRequest,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> StreamingResponse:
    """Stream a chat completion as Server-Sent Events.

    Each event has a `type` (text_delta, tool_use, tool_result, message_done,
    done, error, chat) carrying the matching data.
    """
    if not payload.content:
        raise HTTPException(status_code=400, detail="content is required")

    router_ = get_llm_router()
    provider_name = payload.provider or router_.default_name()
    model_name = payload.model or router_.default_model_for(provider_name)
    if payload.model is not None and not router_.has_model(provider_name, model_name):
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model_name}' is not configured for provider '{provider_name}'",
        )

    if payload.chat_id:
        chat = persistence.get_chat(conn, payload.chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found")
    else:
        # Title from first text block, truncated.
        first_text = next(
            (b.text for b in payload.content if isinstance(b, TextBlockIn) and b.text.strip()),
            "New chat",
        )
        chat = persistence.create_chat(
            conn,
            title=first_text[:60].strip() or "New chat",
            module_id=payload.module_id,
            model=f"{provider_name}/{model_name}",
        )

    blocks = _to_blocks(payload.content)
    user_message = Message(role="user", content=blocks)

    async def event_gen() -> AsyncIterator[bytes]:
        # Front-matter event so the client knows the chat id (if it just got created)
        yield _sse("chat", {"id": chat.id, "title": chat.title, "module_id": chat.module_id})

        try:
            from app.tools import get_registry

            registry = get_registry()
            async for ev in run_chat(
                conn,
                chat=chat,
                user_message=user_message,
                provider_name=provider_name,
                model=model_name,
                tools=registry.defs(),
                dispatcher=registry,
            ):
                if ev.type == "text_delta":
                    yield _sse("text_delta", {"text": ev.text or ""})
                elif ev.type == "tool_use" and ev.tool_use:
                    yield _sse("tool_use", ev.tool_use.model_dump())
                elif ev.type == "tool_result" and ev.tool_result:
                    yield _sse("tool_result", ev.tool_result.model_dump())
                elif ev.type == "message_done" and ev.message:
                    yield _sse("message_done", ev.message.model_dump())
                elif ev.type == "error":
                    yield _sse("error", {"error": ev.error or "unknown error"})
                elif ev.type == "done":
                    yield _sse("done", {})
        except Exception as exc:
            log.exception("chat stream crashed")
            yield _sse("error", {"error": str(exc)})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

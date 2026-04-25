"""Module catalog endpoint.

Phase 1 only ships the global `chat` module. Subsequent phases will replace
this with a registry-backed listing.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import current_user

router = APIRouter(prefix="/api", tags=["modules"])


class ModuleInfo(BaseModel):
    id: str
    name: dict[str, str]
    icon: str
    ui: str  # "chat" | "news" | "obsidian" | "anki" | "tasks" | "personal_life" | "chat-only"


@router.get("/modules", response_model=list[ModuleInfo])
def list_modules(_user: str = Depends(current_user)) -> list[ModuleInfo]:
    return [
        ModuleInfo(
            id="chat",
            name={"en": "Chat", "fr": "Discussion"},
            icon="message-square",
            ui="chat",
        ),
    ]

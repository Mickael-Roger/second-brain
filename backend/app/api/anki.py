"""Authenticated Anki card creation endpoints for the SPA."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from app.anki import AnkiConnectError, AnkiDeck, add_card, list_decks
from app.auth import current_user
from app.config import get_settings

router = APIRouter(prefix="/api/anki", tags=["anki"], dependencies=[Depends(current_user)])

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CardCreateRequest(BaseModel):
    deck: NonEmptyText
    front: NonEmptyText
    back: NonEmptyText
    mode: Literal["normal", "reverse"] = "normal"
    tags: list[str] = Field(default_factory=list)


def require_enabled() -> None:
    if not get_settings().anki.enabled:
        raise HTTPException(status_code=403, detail="Anki is disabled in config.yml.")


@router.get("/config")
def config() -> dict[str, bool]:
    return {"enabled": get_settings().anki.enabled}


@router.get("/decks", dependencies=[Depends(require_enabled)])
async def decks() -> list[AnkiDeck]:
    try:
        return await list_decks()
    except AnkiConnectError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/cards", status_code=201, dependencies=[Depends(require_enabled)])
async def create_card(payload: CardCreateRequest) -> dict[str, int]:
    try:
        note_id = await add_card(
            deck=payload.deck,
            front=payload.front,
            back=payload.back,
            mode=payload.mode,
            tags=[tag.strip() for tag in payload.tags if tag.strip()],
        )
    except AnkiConnectError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"note_id": note_id, "cards_created": 2 if payload.mode == "reverse" else 1}

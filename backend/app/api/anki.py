"""Authenticated Anki creation, deck statistics and card review endpoints."""

import mimetypes
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field, StringConstraints

from app.anki import (
    AnkiCardInfo,
    AnkiConnectError,
    AnkiDeck,
    AnkiDeckStats,
    add_card,
    answer_card,
    deck_stats,
    list_decks,
    list_due_cards,
    retrieve_media,
    sync,
)
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


class CardAnswerRequest(BaseModel):
    ease: int = Field(ge=1, le=4, strict=True)


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


@router.post("/sync", dependencies=[Depends(require_enabled)])
async def synchronize() -> dict[str, bool]:
    try:
        await sync()
    except AnkiConnectError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"synced": True}


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


@router.get("/deck-stats", dependencies=[Depends(require_enabled)])
async def get_deck_stats() -> list[AnkiDeckStats]:
    try:
        return await deck_stats()
    except AnkiConnectError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/due", dependencies=[Depends(require_enabled)])
async def due_cards(
    deck: NonEmptyText,
    limit: int = Query(default=1000, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
    include_new: bool = True,
) -> list[AnkiCardInfo]:
    try:
        return await list_due_cards(deck=deck, limit=limit, offset=offset, include_new=include_new)
    except AnkiConnectError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/cards/{card_id}/answer", dependencies=[Depends(require_enabled)])
async def review_card(
    card_id: Annotated[int, Path(gt=0)],
    payload: CardAnswerRequest,
) -> dict[str, bool]:
    try:
        answered = await answer_card(card_id, payload.ease)
    except AnkiConnectError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not answered:
        raise HTTPException(status_code=404, detail="Anki card not found.")
    return {"answered": True}


@router.get("/media", dependencies=[Depends(require_enabled)])
async def media(filename: NonEmptyText) -> Response:
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        raise HTTPException(status_code=422, detail="Expected an Anki media filename.")
    try:
        data = await retrieve_media(filename)
    except AnkiConnectError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if data is None:
        raise HTTPException(status_code=404, detail="Anki media not found.")
    return Response(
        content=data,
        media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )

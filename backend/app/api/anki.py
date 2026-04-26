"""Anki endpoints: decks, notes, review, sync.

  - GET    /api/anki/decks                          list decks with counts
  - POST   /api/anki/decks                          create
  - PATCH  /api/anki/decks/{id}                     rename
  - DELETE /api/anki/decks/{id}                     delete (writes graves)
  - GET    /api/anki/decks/{id}/notes               list notes (search)
  - POST   /api/anki/notes                          create note + 1 or 2 cards
  - GET    /api/anki/notes/{id}                     fetch one note
  - PATCH  /api/anki/notes/{id}                     update fields/tags
  - DELETE /api/anki/notes/{id}                     delete
  - GET    /api/anki/review/{deck_id}/next          next due card
  - POST   /api/anki/review/{card_id}               record an answer
  - GET    /api/anki/sync/status                    last sync info + local mod
  - POST   /api/anki/sync/upload                    full upload to AnkiWeb
  - POST   /api/anki/sync/download                  full download from AnkiWeb
"""

from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.anki import (
    AnkiSyncError,
    NOTETYPE_BASIC,
    NOTETYPE_BASIC_REVERSE,
    add_note,
    answer_card,
    create_deck,
    delete_deck,
    delete_note,
    get_note,
    list_decks,
    list_notes,
    next_due_card,
    rename_deck,
    sync_download,
    sync_status,
    sync_upload,
    update_note,
)
from app.anki.connection import get_anki_db
from app.anki.repo import card_render, get_card
from app.auth import current_user
from app.config import get_settings

router = APIRouter(prefix="/api/anki", tags=["anki"])
log = logging.getLogger(__name__)


# ── Dependency: enforce anki.enabled before any work ────────────────


def _require_enabled() -> None:
    if not get_settings().anki.enabled:
        raise HTTPException(status_code=503, detail="anki feature is disabled in config")


# ── DTOs ────────────────────────────────────────────────────────────


class DeckDTO(BaseModel):
    id: int
    name: str
    card_count: int
    new_count: int
    due_count: int


class CardDTO(BaseModel):
    id: int
    nid: int
    did: int
    ord: int
    type: int
    queue: int
    due: int
    ivl: int
    factor: int
    reps: int
    lapses: int


class NoteDTO(BaseModel):
    id: int
    deck_id: int
    notetype: str
    fields: list[str]
    tags: list[str]
    cards: list[CardDTO]


class CreateDeckRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class RenameDeckRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class CreateNoteRequest(BaseModel):
    deck_id: int
    notetype: str = Field(pattern=f"^({NOTETYPE_BASIC}|{NOTETYPE_BASIC_REVERSE})$")
    fields: list[str] = Field(min_length=2, max_length=2)
    tags: list[str] = Field(default_factory=list)


class UpdateNoteRequest(BaseModel):
    fields: list[str] | None = None
    tags: list[str] | None = None


class ReviewAnswerRequest(BaseModel):
    ease: int = Field(ge=1, le=4)
    time_ms: int = 0


class CardForReviewDTO(BaseModel):
    card: CardDTO
    front_html: str
    back_html: str


class ReviewResultDTO(BaseModel):
    card: CardDTO
    show_in_seconds: int


class SyncStatusDTO(BaseModel):
    enabled: bool
    last_sync_ms: int | None
    last_action: str | None
    last_error: str | None
    local_mod_ms: int | None


class TriggerResponse(BaseModel):
    ok: bool


def _deck_dto(d) -> DeckDTO:
    return DeckDTO(
        id=d.id, name=d.name,
        card_count=d.card_count, new_count=d.new_count, due_count=d.due_count,
    )


def _card_dto(c) -> CardDTO:
    return CardDTO(
        id=c.id, nid=c.nid, did=c.did, ord=c.ord,
        type=c.type, queue=c.queue, due=c.due, ivl=c.ivl,
        factor=c.factor, reps=c.reps, lapses=c.lapses,
    )


def _note_dto(n) -> NoteDTO:
    return NoteDTO(
        id=n.id, deck_id=n.deck_id, notetype=n.notetype,
        fields=n.fields, tags=n.tags,
        cards=[_card_dto(c) for c in n.cards],
    )


# ── Decks ───────────────────────────────────────────────────────────


@router.get("/decks", response_model=list[DeckDTO])
def get_decks(
    _user: str = Depends(current_user),
    _enabled: None = Depends(_require_enabled),
    conn: sqlite3.Connection = Depends(get_anki_db),
) -> list[DeckDTO]:
    return [_deck_dto(d) for d in list_decks(conn)]


@router.post("/decks", response_model=DeckDTO, status_code=201)
def post_deck(
    body: CreateDeckRequest,
    _user: str = Depends(current_user),
    _enabled: None = Depends(_require_enabled),
    conn: sqlite3.Connection = Depends(get_anki_db),
) -> DeckDTO:
    try:
        return _deck_dto(create_deck(conn, body.name))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/decks/{deck_id}", response_model=DeckDTO)
def patch_deck(
    deck_id: int,
    body: RenameDeckRequest,
    _user: str = Depends(current_user),
    _enabled: None = Depends(_require_enabled),
    conn: sqlite3.Connection = Depends(get_anki_db),
) -> DeckDTO:
    try:
        rename_deck(conn, deck_id, body.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="deck not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    decks = [d for d in list_decks(conn) if d.id == deck_id]
    if not decks:
        raise HTTPException(status_code=404, detail="deck not found")
    return _deck_dto(decks[0])


@router.delete("/decks/{deck_id}", status_code=204)
def del_deck(
    deck_id: int,
    _user: str = Depends(current_user),
    _enabled: None = Depends(_require_enabled),
    conn: sqlite3.Connection = Depends(get_anki_db),
) -> None:
    try:
        delete_deck(conn, deck_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="deck not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Notes ───────────────────────────────────────────────────────────


@router.get("/decks/{deck_id}/notes", response_model=list[NoteDTO])
def get_deck_notes(
    deck_id: int,
    search: str | None = None,
    limit: int = 200,
    _user: str = Depends(current_user),
    _enabled: None = Depends(_require_enabled),
    conn: sqlite3.Connection = Depends(get_anki_db),
) -> list[NoteDTO]:
    return [_note_dto(n) for n in list_notes(conn, deck_id=deck_id, search=search, limit=limit)]


@router.post("/notes", response_model=NoteDTO, status_code=201)
def post_note(
    body: CreateNoteRequest,
    _user: str = Depends(current_user),
    _enabled: None = Depends(_require_enabled),
    conn: sqlite3.Connection = Depends(get_anki_db),
) -> NoteDTO:
    try:
        n = add_note(
            conn, deck_id=body.deck_id, notetype=body.notetype,
            fields=body.fields, tags=body.tags,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _note_dto(n)


@router.get("/notes/{note_id}", response_model=NoteDTO)
def get_one_note(
    note_id: int,
    _user: str = Depends(current_user),
    _enabled: None = Depends(_require_enabled),
    conn: sqlite3.Connection = Depends(get_anki_db),
) -> NoteDTO:
    n = get_note(conn, note_id)
    if n is None:
        raise HTTPException(status_code=404, detail="note not found")
    return _note_dto(n)


@router.patch("/notes/{note_id}", response_model=NoteDTO)
def patch_note(
    note_id: int,
    body: UpdateNoteRequest,
    _user: str = Depends(current_user),
    _enabled: None = Depends(_require_enabled),
    conn: sqlite3.Connection = Depends(get_anki_db),
) -> NoteDTO:
    try:
        n = update_note(conn, note_id, fields=body.fields, tags=body.tags)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="note not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _note_dto(n)


@router.delete("/notes/{note_id}", status_code=204)
def del_note(
    note_id: int,
    _user: str = Depends(current_user),
    _enabled: None = Depends(_require_enabled),
    conn: sqlite3.Connection = Depends(get_anki_db),
) -> None:
    try:
        delete_note(conn, note_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="note not found") from exc


# ── Review ──────────────────────────────────────────────────────────


@router.get("/review/{deck_id}/next", response_model=CardForReviewDTO | None)
def get_next_review(
    deck_id: int,
    _user: str = Depends(current_user),
    _enabled: None = Depends(_require_enabled),
    conn: sqlite3.Connection = Depends(get_anki_db),
) -> CardForReviewDTO | None:
    card = next_due_card(conn, deck_id)
    if card is None:
        return None
    rendered = card_render(conn, card)
    return CardForReviewDTO(
        card=_card_dto(card),
        front_html=rendered["front"],
        back_html=rendered["back"],
    )


@router.post("/review/{card_id}", response_model=ReviewResultDTO)
def post_review_answer(
    card_id: int,
    body: ReviewAnswerRequest,
    _user: str = Depends(current_user),
    _enabled: None = Depends(_require_enabled),
    conn: sqlite3.Connection = Depends(get_anki_db),
) -> ReviewResultDTO:
    if get_card(conn, card_id) is None:
        raise HTTPException(status_code=404, detail="card not found")
    try:
        result = answer_card(conn, card_id, body.ease, time_ms=body.time_ms)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="card not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ReviewResultDTO(card=_card_dto(result.card), show_in_seconds=result.show_in)


# ── Sync ────────────────────────────────────────────────────────────


@router.get("/sync/status", response_model=SyncStatusDTO)
def get_sync_status(
    _user: str = Depends(current_user),
) -> SyncStatusDTO:
    s = sync_status()
    return SyncStatusDTO(
        enabled=s.enabled,
        last_sync_ms=s.last_sync_ms,
        last_action=s.last_action,
        last_error=s.last_error,
        local_mod_ms=s.local_mod_ms,
    )


@router.post("/sync/upload", response_model=TriggerResponse)
async def post_sync_upload(
    _user: str = Depends(current_user),
    _enabled: None = Depends(_require_enabled),
) -> TriggerResponse:
    try:
        await sync_upload()
    except AnkiSyncError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    return TriggerResponse(ok=True)


@router.post("/sync/download", response_model=TriggerResponse)
async def post_sync_download(
    _user: str = Depends(current_user),
    _enabled: None = Depends(_require_enabled),
) -> TriggerResponse:
    try:
        await sync_download()
    except AnkiSyncError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    return TriggerResponse(ok=True)

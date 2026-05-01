"""HTTP endpoints for the Wiki review feature."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import current_user
from app.db.connection import get_db
from app.vault import read_note
from app.vault.paths import VaultPathError
from app.wiki_reviews import (
    RATINGS,
    pick_next,
    record_rating,
    review_state,
    review_status,
)

router = APIRouter(prefix="/api/wiki-reviews", tags=["wiki-reviews"])


class StatusResponse(BaseModel):
    has_reviewed_today: bool
    reviewed_today_count: int
    excluded_count: int
    total_in_state: int


class StateDTO(BaseModel):
    last_reviewed_at: str
    last_rating: str
    next_due_at: str
    excluded: bool
    review_count: int


class NextReviewResponse(BaseModel):
    path: str
    content: str
    state: StateDTO | None


class RateRequest(BaseModel):
    path: str = Field(..., min_length=1)
    rating: str = Field(..., min_length=1)


class RateResponse(BaseModel):
    path: str
    state: StateDTO


def _state_dto(state) -> StateDTO:
    return StateDTO(
        last_reviewed_at=state.last_reviewed_at,
        last_rating=state.last_rating,
        next_due_at=state.next_due_at,
        excluded=state.excluded,
        review_count=state.review_count,
    )


@router.get("/status", response_model=StatusResponse)
def get_status(
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> StatusResponse:
    s = review_status(conn)
    return StatusResponse(
        has_reviewed_today=s.has_reviewed_today,
        reviewed_today_count=s.reviewed_today_count,
        excluded_count=s.excluded_count,
        total_in_state=s.total_in_state,
    )


@router.get("/next", response_model=NextReviewResponse)
def get_next(
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> NextReviewResponse:
    pick = pick_next(conn)
    if pick is None:
        raise HTTPException(
            status_code=404,
            detail="no Wiki/ pages available for review",
        )
    try:
        note = read_note(pick.path)
    except FileNotFoundError as exc:
        # Stale row — file gone since we listed it. Surface a clean error.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VaultPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return NextReviewResponse(
        path=note.path,
        content=note.content,
        state=_state_dto(pick.state) if pick.state else None,
    )


@router.post("", response_model=RateResponse)
def post_rating(
    payload: RateRequest,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> RateResponse:
    if payload.rating not in RATINGS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown rating {payload.rating!r}; expected one of {list(RATINGS)}",
        )
    # Path sanity: must resolve under the vault and live under Wiki/.
    try:
        from app.vault.paths import resolve_vault_path

        resolve_vault_path(payload.path)
    except VaultPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not payload.path.startswith("Wiki/"):
        raise HTTPException(
            status_code=400,
            detail="only Wiki/ pages can be reviewed",
        )

    state = record_rating(conn, payload.path, payload.rating)
    # We left the connection in autocommit mode; the inserts above are
    # already durable. Surface the fresh state to the caller.
    _ = review_state(conn, payload.path)
    return RateResponse(path=payload.path, state=_state_dto(state))

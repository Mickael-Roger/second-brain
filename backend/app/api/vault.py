"""HTTP endpoints for the wiki view (read-only at this step)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import current_user
from app.vault import (
    list_tree,
    read_note,
    search_vault,
)
from app.vault.backlinks import find_backlinks
from app.vault.paths import VaultPathError

router = APIRouter(prefix="/api/vault", tags=["vault"])


class TreeEntry(BaseModel):
    path: str
    type: str
    depth: int


class NoteResponse(BaseModel):
    path: str
    content: str
    backlinks: list[dict]


class SearchHit(BaseModel):
    path: str
    line_number: int
    snippet: str


@router.get("/tree", response_model=list[TreeEntry])
def get_tree(
    folder: str = Query(default=""),
    _user: str = Depends(current_user),
) -> list[TreeEntry]:
    try:
        return [TreeEntry(**e) for e in list_tree(folder)]
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VaultPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/note", response_model=NoteResponse)
def get_note(
    path: str = Query(...),
    _user: str = Depends(current_user),
) -> NoteResponse:
    try:
        n = read_note(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VaultPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    backlinks = [
        {"path": b.path, "snippet": b.snippet} for b in find_backlinks(n.path)
    ]
    return NoteResponse(path=n.path, content=n.content, backlinks=backlinks)


@router.get("/search", response_model=list[SearchHit])
def get_search(
    q: str = Query(..., min_length=1),
    folder: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
    _user: str = Depends(current_user),
) -> list[SearchHit]:
    try:
        return [SearchHit(**h) for h in search_vault(q, in_folder=folder, limit=limit)]
    except VaultPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

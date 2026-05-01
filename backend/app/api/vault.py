"""HTTP endpoints for the wiki view."""

from __future__ import annotations

import mimetypes
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.auth import current_user
from app.db.connection import get_db
from app.vault import (
    find_notes,
    list_tree,
    read_note,
    resolve_vault_path,
    search_vault,
    write_note,
)
from app.vault.backlinks import find_backlinks
from app.vault.guard import GitConflictError
from app.vault.locks import LockConflict, LockInvalid, acquire, release, verify
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


@router.get("/file")
def get_file(
    path: str = Query(...),
    _user: str = Depends(current_user),
) -> FileResponse:
    """Serve any file from the vault (images, PDFs, …) with proper
    content-type. Used by the wiki to render `![[image.png]]` embeds and
    standard markdown image links inside notes."""
    try:
        abs_path = resolve_vault_path(path)
    except VaultPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {path}")
    mime, _ = mimetypes.guess_type(str(abs_path))
    return FileResponse(str(abs_path), media_type=mime or "application/octet-stream")


@router.get("/search", response_model=list[SearchHit])
def get_search(
    q: str = Query(..., min_length=1),
    path: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
    _user: str = Depends(current_user),
) -> list[SearchHit]:
    try:
        return [SearchHit(**h) for h in search_vault(q, in_path=path, limit=limit)]
    except VaultPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/find", response_model=list[str])
def get_find(
    q: str = Query(..., min_length=1),
    folder: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=200),
    _user: str = Depends(current_user),
) -> list[str]:
    """Match notes by NAME (basename + relative path), case-insensitive.
    The wiki search bar uses this alongside the ripgrep `/search` so a
    user typing a note's title gets the file at the top of the list,
    not buried under content matches that happen to mention the word."""
    try:
        return find_notes(q, in_folder=folder, limit=limit)
    except (FileNotFoundError, VaultPathError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ── Edit flow ────────────────────────────────────────────────────────


class LockRequest(BaseModel):
    path: str


class LockResponse(BaseModel):
    path: str
    token: str
    expires_at: str


class ReleaseRequest(BaseModel):
    path: str
    token: str


class WriteRequest(BaseModel):
    path: str
    content: str
    token: str


@router.post("/edit/lock", response_model=LockResponse)
def lock_path(
    payload: LockRequest,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> LockResponse:
    # Check the path is sane (resolves under the vault) before locking.
    try:
        from app.vault.paths import resolve_vault_path

        resolve_vault_path(payload.path)
    except VaultPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        grant = acquire(conn, payload.path)
    except LockConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return LockResponse(
        path=grant.path, token=grant.token, expires_at=grant.expires_at.isoformat()
    )


@router.delete("/edit/lock", status_code=204)
def unlock_path(
    payload: ReleaseRequest,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    try:
        release(conn, payload.path, payload.token)
    except LockInvalid as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.put("/note", response_model=NoteResponse)
async def put_note(
    payload: WriteRequest,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> NoteResponse:
    try:
        verify(conn, payload.path, payload.token)
    except LockInvalid as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    try:
        n = await write_note(payload.path, payload.content, message=f"wiki edit: {payload.path}")
    except VaultPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GitConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    backlinks = [{"path": b.path, "snippet": b.snippet} for b in find_backlinks(n.path)]
    return NoteResponse(path=n.path, content=n.content, backlinks=backlinks)

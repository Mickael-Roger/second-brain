"""Organize endpoints — backed by SQLite-stored runs + proposals.

The webapp's Organize view drives this:
  - POST /api/organize/runs               start a run (async, returns run_id)
  - GET  /api/organize/runs/current       most recent run + proposals
  - GET  /api/organize/runs/{id}          specific run + proposals
  - DELETE .../proposals?path=…           discard a single proposal
  - POST .../apply                        apply all pending proposals
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.auth import current_user
from app.db.connection import get_db
from app.organize import (
    StoredRun,
    discard_proposal,
    get_current_run,
    get_run,
)
from app.organize.store import create_run, set_run_status

router = APIRouter(prefix="/api/organize", tags=["organize"])
log = logging.getLogger(__name__)


# ── DTOs ────────────────────────────────────────────────────────────


class ProposalDTO(BaseModel):
    path: str
    move_to: str | None
    tags: list[str] | None
    wikilinks: list[dict[str, str]]
    refactor: str | None
    notes: str | None
    parse_error: str | None
    state: str
    apply_error: str | None
    apply_ops: list[str]
    created_at: str


class RunDTO(BaseModel):
    id: str
    started_at: str
    finished_at: str | None
    mode: str
    status: str
    notes_total: int
    summary: str | None
    error: str | None
    counts: dict[str, int]
    proposals: list[ProposalDTO]


def _run_to_dto(run: StoredRun) -> RunDTO:
    counts = {"pending": 0, "applied": 0, "discarded": 0, "failed": 0}
    for p in run.proposals:
        counts[p.state] = counts.get(p.state, 0) + 1
    return RunDTO(
        id=run.id,
        started_at=run.started_at.isoformat(),
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        mode=run.mode,
        status=run.status,
        notes_total=run.notes_total,
        summary=run.summary,
        error=run.error,
        counts=counts,
        proposals=[
            ProposalDTO(
                path=p.path,
                move_to=p.move_to,
                tags=p.tags,
                wikilinks=p.wikilinks,
                refactor=p.refactor,
                notes=p.notes,
                parse_error=p.parse_error,
                state=p.state,
                apply_error=p.apply_error,
                apply_ops=p.apply_ops,
                created_at=p.created_at.isoformat(),
            )
            for p in run.proposals
        ],
    )


# ── Endpoints ───────────────────────────────────────────────────────


class StartRunResponse(BaseModel):
    run_id: str


@router.post("/runs", response_model=StartRunResponse, status_code=202)
async def start_run(
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> StartRunResponse:
    """Start a new organize pass in the background. Returns immediately
    with the run id; the webapp polls /current to see progress."""
    from app.config import get_settings

    run_id = create_run(conn, mode=get_settings().organize.mode)

    async def _go() -> None:
        from app.jobs.organize import run_organize

        try:
            await run_organize(run_id=run_id)
        except Exception as exc:
            log.exception("background organize run failed")
            # Ensure the run row reflects the failure so the UI doesn't
            # spin forever waiting for a status change.
            from app.db.connection import open_connection

            c = open_connection()
            try:
                set_run_status(c, run_id, status="failed", error=str(exc))
            finally:
                c.close()

    asyncio.create_task(_go())
    return StartRunResponse(run_id=run_id)


@router.get("/runs/current", response_model=RunDTO | None)
def get_current(
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> RunDTO | None:
    run = get_current_run(conn)
    return _run_to_dto(run) if run is not None else None


@router.get("/runs/{run_id}", response_model=RunDTO)
def get_one(
    run_id: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> RunDTO:
    run = get_run(conn, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _run_to_dto(run)


@router.delete("/runs/{run_id}/proposals", status_code=status.HTTP_204_NO_CONTENT)
def discard_one(
    run_id: str,
    path: str = Query(...),
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    """Discard a single pending proposal so it won't be applied."""
    if not discard_proposal(conn, run_id, path):
        raise HTTPException(status_code=404, detail="no pending proposal at that path")


class ApplyOneResponse(BaseModel):
    state: str
    operations: list[str]
    error: str | None


@router.post("/runs/{run_id}/proposals/apply", response_model=ApplyOneResponse)
async def apply_one(
    run_id: str,
    path: str = Query(...),
    _user: str = Depends(current_user),
) -> ApplyOneResponse:
    """Apply just one pending proposal — used by the per-card Apply button."""
    from app.jobs.organize import apply_one_proposal

    try:
        result = await apply_one_proposal(run_id, path)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ApplyOneResponse(**result)


class ReviseRequest(BaseModel):
    instruction: str


@router.post("/runs/{run_id}/proposals/revise", response_model=ProposalDTO)
async def revise_one(
    run_id: str,
    payload: ReviseRequest,
    path: str = Query(...),
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ProposalDTO:
    """Re-prompt the LLM with the user's revision instruction. Replaces
    the stored proposal in place; the front-end card re-renders with the
    new content."""
    from app.jobs.organize import revise_proposal

    if not payload.instruction.strip():
        raise HTTPException(status_code=400, detail="instruction is required")
    try:
        await revise_proposal(run_id, path, payload.instruction)
    except (LookupError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc

    run = get_run(conn, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run vanished")
    return _run_to_dto(run).proposals[
        next(i for i, p in enumerate(run.proposals) if p.path == path)
    ]


class ApplyResponse(BaseModel):
    applied: int
    failed: int


@router.post("/runs/{run_id}/apply", response_model=ApplyResponse)
async def apply_run(
    run_id: str,
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ApplyResponse:
    """Apply every still-pending proposal of the run through the vault
    primitives. Each proposal's state is updated as the writes go through."""
    run = get_run(conn, run_id, include_proposals=False)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status not in ("completed", "applied"):
        raise HTTPException(
            status_code=409,
            detail=f"run is not ready to apply (status={run.status})",
        )

    from app.jobs.organize import apply_pending_proposals

    summary = await apply_pending_proposals(run_id)
    return ApplyResponse(**summary)


# Keep the legacy on-demand endpoint working — same code path under the hood.

class OrganizeRunResponse(BaseModel):
    report: str


@router.post("/run", response_model=OrganizeRunResponse)
async def trigger_run(_user: str = Depends(current_user)) -> OrganizeRunResponse:
    """Run the same nightly job (journal archive + organize pass) right now,
    synchronously. Used by `second-brain organize` and other tooling that
    expects the rendered report inline."""
    from app.jobs import run_nightly

    report = await run_nightly()
    return OrganizeRunResponse(report=report)

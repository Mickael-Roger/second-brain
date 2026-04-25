"""On-demand Organize trigger.

Phase 1 ships a single synchronous endpoint — useful for testing the flow
during the dry-run period. The nightly cron runs the same code path.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import current_user
from app.jobs import run_nightly

router = APIRouter(prefix="/api/organize", tags=["organize"])


class OrganizeRunResponse(BaseModel):
    report: str


@router.post("/run", response_model=OrganizeRunResponse)
async def trigger_run(_user: str = Depends(current_user)) -> OrganizeRunResponse:
    """Run the same nightly job (journal archive + organize pass) right now."""
    report = await run_nightly()
    return OrganizeRunResponse(report=report)

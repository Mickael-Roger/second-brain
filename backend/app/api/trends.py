"""HTTP endpoints for the Trends feature.

  GET  /api/trends                  active trends, weight DESC
  GET  /api/trends/history?since=   snapshot stream for the bubble chart
  POST /api/trends/process          trigger the worker (manual catch-up)

Read-only access checks auth via `current_user`; the process endpoint
honours `trends.enabled` and refuses when off.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import current_user
from app.config import get_settings
from app.db.connection import get_db
from app.trends import store
from app.trends.engine import softmax

router = APIRouter(prefix="/api/trends", tags=["trends"])


# ── DTOs ────────────────────────────────────────────────────────────


class TrendDTO(BaseModel):
    id: str
    name: str
    description: str
    weight: float                # raw weight from the engine
    weight_softmax: float        # normalised over the active set, useful for the bubble UI
    created_at: str
    last_reinforced_at: str
    reinforcement_count: int
    examples: list[str]


class TrendsListResponse(BaseModel):
    trends: list[TrendDTO]


class TrendSnapshotDTO(BaseModel):
    id: int
    recorded_at: str
    trigger_article_id: str | None
    trigger_action: str
    trigger_trend_id: str | None
    weights: dict[str, float]


class TrendsHistoryResponse(BaseModel):
    snapshots: list[TrendSnapshotDTO]
    # Metadata for pruned (soft-deleted) trends referenced in the
    # snapshots so the UI can label points on the chart even after a
    # trend has been evicted.
    trend_meta: dict[str, "TrendMetaDTO"]


class TrendMetaDTO(BaseModel):
    name: str
    description: str
    pruned_at: str | None


class TrendsProcessResponse(BaseModel):
    processed: int


# Forward-reference resolution for TrendMetaDTO inside the history
# response (pydantic v2 needs the rebuild because the forward-ref
# string is used before TrendMetaDTO is bound at module import time).
TrendsHistoryResponse.model_rebuild()


# ── Endpoints ───────────────────────────────────────────────────────


@router.get("", response_model=TrendsListResponse)
def list_trends(
    _user: str = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> TrendsListResponse:
    trends = store.list_active_trends(conn)
    soft = softmax({t.id: t.weight for t in trends})
    out = [
        TrendDTO(
            id=t.id,
            name=t.name,
            description=t.description,
            weight=t.weight,
            weight_softmax=soft.get(t.id, 0.0),
            created_at=t.created_at,
            last_reinforced_at=t.last_reinforced_at,
            reinforcement_count=t.reinforcement_count,
            examples=t.examples,
        )
        for t in trends
    ]
    return TrendsListResponse(trends=out)


@router.get("/history", response_model=TrendsHistoryResponse)
def get_history(
    _user: str = Depends(current_user),
    hours: int = Query(default=24, ge=1, le=24 * 30),
    limit: int = Query(default=500, ge=10, le=5000),
    conn: sqlite3.Connection = Depends(get_db),
) -> TrendsHistoryResponse:
    """Return the trend_snapshots within the last `hours` (default 24h).

    Each snapshot carries the full {trend_id: weight} map, so the
    frontend can directly render N parallel time-series. We bundle
    metadata for every trend (including pruned ones) referenced by
    the returned snapshots, so the chart can label everything."""
    since_iso = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat()
    snaps = store.list_snapshots_since(conn, since_iso=since_iso, limit=limit)

    out = [
        TrendSnapshotDTO(
            id=s.id,
            recorded_at=s.recorded_at,
            trigger_article_id=s.trigger_article_id,
            trigger_action=s.trigger_action,
            trigger_trend_id=s.trigger_trend_id,
            weights=s.weights,
        )
        for s in snaps
    ]

    # Pull metadata for every trend referenced in the snapshot
    # window. We do this in one round-trip rather than per-snapshot.
    referenced: set[str] = set()
    for s in snaps:
        referenced.update(s.weights.keys())
        if s.trigger_trend_id:
            referenced.add(s.trigger_trend_id)

    meta: dict[str, TrendMetaDTO] = {}
    for tid in referenced:
        row = conn.execute(
            "SELECT name, description, pruned_at FROM trends WHERE id = ?",
            (tid,),
        ).fetchone()
        if row is not None:
            meta[tid] = TrendMetaDTO(
                name=str(row["name"]),
                description=str(row["description"]),
                pruned_at=row["pruned_at"],
            )

    return TrendsHistoryResponse(snapshots=out, trend_meta=meta)


@router.post("/process", response_model=TrendsProcessResponse)
async def process_pending_endpoint(
    _user: str = Depends(current_user),
) -> TrendsProcessResponse:
    settings = get_settings()
    if not settings.trends.enabled:
        raise HTTPException(status_code=409, detail="trends feature is disabled")
    from app.trends.worker import process_pending

    n = await process_pending()
    return TrendsProcessResponse(processed=n)

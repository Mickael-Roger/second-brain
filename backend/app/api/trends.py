"""HTTP endpoints for the Trends feature.

  GET  /api/trends                  active trends, weight DESC
  GET  /api/trends/history?since=   snapshot stream for the bubble chart
  POST /api/trends/process          trigger the worker (manual catch-up)

Read-only access checks auth via `current_user`; the process endpoint
honours `trends.enabled` and refuses when off.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

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
    category: str
    weight: float  # raw weight from the engine
    weight_softmax: float  # normalised over the active set, useful for the bubble UI
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
    trend_meta: dict[str, TrendMetaDTO]


class TrendMetaDTO(BaseModel):
    name: str
    description: str
    category: str
    pruned_at: str | None


class TrendsProcessResponse(BaseModel):
    processed: int


class TrendMomentArticleDTO(BaseModel):
    article_id: str
    title: str
    feed_title: str | None
    feed_group: str | None
    published_at: str
    article_type: str
    intensity: float
    confidence: float
    evidence: str


class TrendMomentDTO(BaseModel):
    id: str
    title: str
    description: str
    category: str
    virality_score: float
    mention_count: int
    source_count: int
    article_count: int
    first_seen_at: str
    last_seen_at: str
    direction: str
    articles: list[TrendMomentArticleDTO]


class TrendMomentsResponse(BaseModel):
    moments: list[TrendMomentDTO]


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
            category=t.category,
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
    since_iso = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
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
            "SELECT name, description, category, pruned_at FROM trends WHERE id = ?",
            (tid,),
        ).fetchone()
        if row is not None:
            meta[tid] = TrendMetaDTO(
                name=str(row["name"]),
                description=str(row["description"]),
                category=str(row["category"]) if row["category"] else "Other",
                pruned_at=row["pruned_at"],
            )

    return TrendsHistoryResponse(snapshots=out, trend_meta=meta)


@router.get("/moments", response_model=TrendMomentsResponse)
def list_moments(
    _user: str = Depends(current_user),
    hours: int = Query(default=24, ge=1, le=24 * 7),
    limit: int = Query(default=8, ge=1, le=20),
    conn: sqlite3.Connection = Depends(get_db),
) -> TrendMomentsResponse:
    since_iso = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    mentions = store.list_mentions_since(conn, since_iso=since_iso)
    by_cluster: dict[str, list[store.TrendMentionRecord]] = {}
    for mention in mentions:
        by_cluster.setdefault(mention.cluster_id, []).append(mention)

    scored: list[tuple[float, TrendMomentDTO]] = []
    now = datetime.now(UTC)
    for cluster_id, cluster_mentions in by_cluster.items():
        row = conn.execute(
            "SELECT * FROM trend_clusters WHERE id = ? AND status = 'active'",
            (cluster_id,),
        ).fetchone()
        if row is None:
            continue
        feeds = {m.feed_title or m.feed_group or m.article_id for m in cluster_mentions}
        article_ids = {m.article_id for m in cluster_mentions}
        weighted = sum(
            m.intensity * m.confidence * _article_type_weight(m.article_type)
            for m in cluster_mentions
        )
        source_factor = 1.0 + min(len(feeds), 6) * 0.18
        newest = max(_parse_iso(m.published_at) for m in cluster_mentions)
        age_hours = max((now - newest).total_seconds() / 3600.0, 0.0)
        freshness = 1.0 / (1.0 + age_hours / 18.0)
        score = weighted * source_factor * freshness
        if score < 0.35:
            continue
        first_seen = min(m.published_at for m in cluster_mentions)
        last_seen = max(m.published_at for m in cluster_mentions)
        direction = _direction(cluster_mentions, hours)
        articles = [
            TrendMomentArticleDTO(
                article_id=m.article_id,
                title=m.article_title,
                feed_title=m.feed_title,
                feed_group=m.feed_group,
                published_at=m.published_at,
                article_type=m.article_type,
                intensity=m.intensity,
                confidence=m.confidence,
                evidence=m.evidence,
            )
            for m in sorted(
                cluster_mentions,
                key=lambda item: item.intensity * item.confidence,
                reverse=True,
            )[:6]
        ]
        scored.append(
            (
                score,
                TrendMomentDTO(
                    id=cluster_id,
                    title=str(row["title"]),
                    description=str(row["description"]),
                    category=str(row["category"]),
                    virality_score=round(score, 4),
                    mention_count=len(cluster_mentions),
                    source_count=len(feeds),
                    article_count=len(article_ids),
                    first_seen_at=first_seen,
                    last_seen_at=last_seen,
                    direction=direction,
                    articles=articles,
                ),
            )
        )
    scored.sort(key=lambda item: item[0], reverse=True)
    return TrendMomentsResponse(moments=[item for _, item in scored[:limit]])


def _article_type_weight(article_type: str) -> float:
    return {
        "single_news": 1.0,
        "roundup": 0.65,
        "youtube": 0.55,
        "podcast": 0.55,
        "blogpost": 0.15,
        "opinion": 0.15,
        "tutorial": 0.0,
        "evergreen": 0.0,
    }.get(article_type, 0.5)


def _parse_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    except ValueError:
        return datetime.now(UTC)


def _direction(mentions: list[store.TrendMentionRecord], hours: int) -> str:
    midpoint = datetime.now(UTC) - timedelta(hours=hours / 2)
    recent = 0.0
    previous = 0.0
    for mention in mentions:
        value = mention.intensity * mention.confidence
        if _parse_iso(mention.published_at) >= midpoint:
            recent += value
        else:
            previous += value
    if previous <= 0 and recent > 0:
        return "new"
    if recent > previous * 1.25:
        return "up"
    if recent < previous * 0.75:
        return "down"
    return "stable"


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

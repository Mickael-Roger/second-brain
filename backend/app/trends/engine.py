"""Trends evolution engine.

The math behind the "neural-like" weight system. Each event is one
of:

  - reinforce  →  bump one trend's weight by `intensity`
  - create     →  insert a new trend with `weight = intensity`
  - rename     →  no weight change, just metadata
  - noop       →  LLM didn't call any tool

In every case, AFTER the primary update we apply a multiplicative
decay to all OTHER active trends:

    w'[j] = w[j] * (1 - decay_rate)

This is the back-propagation-like piece: a trend that isn't
reinforced slowly loses ground to those that are. Trends whose
weight ends up below `prune_threshold` are soft-deleted (their
history stays).

The engine is a pure orchestrator over the store: it never touches
the DB directly except through `app.trends.store`. Math first,
persistence second.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Literal

from . import store

log = logging.getLogger(__name__)


# ── Tunables (kept here, exposed through config) ────────────────────

# Default values mirror the `trends:` section of config.yml. The
# settings loader picks them up; the engine reads from settings at
# event time so config changes take effect on restart.


EventAction = Literal["reinforce", "create", "rename", "noop"]


@dataclass(slots=True)
class EventOutcome:
    """What changed after one article was processed by the engine."""
    action: EventAction
    trend_id: str | None
    pruned_ids: list[str]
    snapshot_id: int


def _settings_or_defaults():
    """Read the live tunables from config. Centralised so the engine
    is the only place that knows the defaults."""
    from app.config import get_settings

    cfg = get_settings().trends
    return {
        "decay_rate": cfg.decay_rate,
        "prune_threshold": cfg.prune_threshold,
        "max_trends": cfg.max_trends,
        "examples_cap": cfg.examples_cap,
    }


def softmax(weights: dict[str, float]) -> dict[str, float]:
    """Numerically stable softmax over a {id: weight} mapping. Used
    by the API for display; the engine itself stores raw weights so
    the time-series stays interpretable."""
    if not weights:
        return {}
    m = max(weights.values())
    exps = {k: math.exp(v - m) for k, v in weights.items()}
    total = sum(exps.values()) or 1.0
    return {k: v / total for k, v in exps.items()}


def _apply_decay(
    conn,
    *,
    decay_rate: float,
    skip_trend_id: str | None,
) -> dict[str, float]:
    """Multiply every active trend's weight by (1 - decay_rate),
    except the one just touched. Returns the post-decay map of
    {trend_id: weight} for ALL active trends (including the skipped
    one — caller fills in its post-event value)."""
    active = store.list_active_trends(conn)
    weights: dict[str, float] = {}
    for t in active:
        if t.id == skip_trend_id:
            weights[t.id] = t.weight  # untouched by decay this round
            continue
        new_w = t.weight * (1.0 - decay_rate)
        store.update_weight(conn, t.id, new_w)
        weights[t.id] = new_w
    return weights


def _maybe_prune(
    conn, weights: dict[str, float], *, prune_threshold: float,
    max_trends: int, protect_id: str | None,
) -> list[str]:
    """Soft-delete trends whose weight is under the threshold, plus
    the lowest-weighted trends past `max_trends`. Never prunes the
    trend just reinforced/created (`protect_id`)."""
    pruned: list[str] = []
    for trend_id, w in list(weights.items()):
        if trend_id == protect_id:
            continue
        if w < prune_threshold:
            store.prune_trend(conn, trend_id)
            pruned.append(trend_id)
            weights.pop(trend_id, None)

    if max_trends > 0 and len(weights) > max_trends:
        ranked = sorted(weights.items(), key=lambda kv: kv[1])
        # Drop weakest until under the cap, but never the protected one.
        for trend_id, _ in ranked:
            if len(weights) <= max_trends:
                break
            if trend_id == protect_id:
                continue
            store.prune_trend(conn, trend_id)
            pruned.append(trend_id)
            weights.pop(trend_id, None)
    return pruned


def _snapshot(
    conn,
    *,
    article_id: str | None,
    action: EventAction,
    trend_id: str | None,
    weights: dict[str, float],
) -> int:
    return store.insert_snapshot(
        conn,
        trigger_article_id=article_id,
        trigger_action=action,
        trigger_trend_id=trend_id,
        weights=weights,
    )


# ── Public surface ──────────────────────────────────────────────────


def apply_reinforce(
    conn,
    *,
    article_id: str,
    article_title: str,
    trend_id: str,
    intensity: float,
) -> EventOutcome:
    """`intensity ∈ [0, 1]` from the LLM. We add it to the weight (no
    cap — `softmax` normalises for display); the decay on every other
    trend keeps things finite."""
    cfg = _settings_or_defaults()
    intensity = max(0.0, min(1.0, intensity))
    current = store.get_trend(conn, trend_id)
    if current is None or current.pruned_at is not None:
        log.warning("reinforce on unknown/pruned trend_id=%s — ignoring", trend_id)
        # Treat as noop so the worker still snapshots + marks the article.
        return apply_noop(conn, article_id=article_id)

    new_weight = current.weight + intensity
    store.reinforce_trend(
        conn,
        trend_id=trend_id,
        new_weight=new_weight,
        example_title=article_title,
        examples_cap=cfg["examples_cap"],
    )
    weights = _apply_decay(conn, decay_rate=cfg["decay_rate"], skip_trend_id=trend_id)
    weights[trend_id] = new_weight
    pruned = _maybe_prune(
        conn, weights,
        prune_threshold=cfg["prune_threshold"],
        max_trends=cfg["max_trends"],
        protect_id=trend_id,
    )
    snap_id = _snapshot(
        conn, article_id=article_id, action="reinforce",
        trend_id=trend_id, weights=weights,
    )
    return EventOutcome(
        action="reinforce", trend_id=trend_id,
        pruned_ids=pruned, snapshot_id=snap_id,
    )


def apply_create(
    conn,
    *,
    article_id: str,
    article_title: str,
    name: str,
    description: str,
    intensity: float,
) -> EventOutcome:
    cfg = _settings_or_defaults()
    intensity = max(0.0, min(1.0, intensity))
    initial_weight = max(intensity, cfg["prune_threshold"] * 2.0)

    # If the LLM happens to repeat a known active trend by name, treat
    # it as a reinforce instead of a duplicate — the description gets
    # the new wording.
    existing = store.get_trend_by_name(conn, name)
    if existing is not None:
        store.rename_trend(
            conn, trend_id=existing.id,
            new_name=None, new_description=description,
        )
        return apply_reinforce(
            conn,
            article_id=article_id,
            article_title=article_title,
            trend_id=existing.id,
            intensity=intensity,
        )

    created = store.create_trend(
        conn,
        name=name,
        description=description,
        weight=initial_weight,
        example_title=article_title,
    )
    weights = _apply_decay(
        conn, decay_rate=cfg["decay_rate"], skip_trend_id=created.id,
    )
    weights[created.id] = initial_weight
    pruned = _maybe_prune(
        conn, weights,
        prune_threshold=cfg["prune_threshold"],
        max_trends=cfg["max_trends"],
        protect_id=created.id,
    )
    snap_id = _snapshot(
        conn, article_id=article_id, action="create",
        trend_id=created.id, weights=weights,
    )
    return EventOutcome(
        action="create", trend_id=created.id,
        pruned_ids=pruned, snapshot_id=snap_id,
    )


def apply_rename(
    conn,
    *,
    article_id: str,
    trend_id: str,
    new_name: str | None,
    new_description: str | None,
) -> EventOutcome:
    """Rename + optional description refresh. No weight change, no
    decay (pure metadata edit). A snapshot is still recorded so the
    history captures the rename event."""
    if not store.rename_trend(
        conn, trend_id=trend_id,
        new_name=new_name, new_description=new_description,
    ):
        return apply_noop(conn, article_id=article_id)

    active = store.list_active_trends(conn)
    weights = {t.id: t.weight for t in active}
    snap_id = _snapshot(
        conn, article_id=article_id, action="rename",
        trend_id=trend_id, weights=weights,
    )
    return EventOutcome(
        action="rename", trend_id=trend_id,
        pruned_ids=[], snapshot_id=snap_id,
    )


def apply_noop(conn, *, article_id: str) -> EventOutcome:
    """Article didn't trigger any tool call. Still apply decay so
    every news event contributes to the trend lifecycle, then
    snapshot."""
    cfg = _settings_or_defaults()
    weights = _apply_decay(
        conn, decay_rate=cfg["decay_rate"], skip_trend_id=None,
    )
    pruned = _maybe_prune(
        conn, weights,
        prune_threshold=cfg["prune_threshold"],
        max_trends=cfg["max_trends"],
        protect_id=None,
    )
    snap_id = _snapshot(
        conn, article_id=article_id, action="noop",
        trend_id=None, weights=weights,
    )
    return EventOutcome(
        action="noop", trend_id=None,
        pruned_ids=pruned, snapshot_id=snap_id,
    )


def apply_event(
    conn, *, action: EventAction, article_id: str, article_title: str,
    trend_id: str | None = None,
    intensity: float | None = None,
    name: str | None = None,
    description: str | None = None,
    new_name: str | None = None,
    new_description: str | None = None,
) -> EventOutcome:
    """Dispatch helper for callers that already have the parsed
    tool-call payload. Most code uses the typed helpers above directly."""
    if action == "reinforce":
        assert trend_id is not None and intensity is not None
        return apply_reinforce(
            conn, article_id=article_id, article_title=article_title,
            trend_id=trend_id, intensity=intensity,
        )
    if action == "create":
        assert name and description and intensity is not None
        return apply_create(
            conn, article_id=article_id, article_title=article_title,
            name=name, description=description, intensity=intensity,
        )
    if action == "rename":
        assert trend_id is not None
        return apply_rename(
            conn, article_id=article_id, trend_id=trend_id,
            new_name=new_name, new_description=new_description,
        )
    return apply_noop(conn, article_id=article_id)

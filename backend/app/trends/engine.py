"""Trends evolution engine.

The math behind the "neural-like" weight system. Each article
triggers a batch of intents from the LLM:

  - reinforce  →  bump one trend's weight by `intensity`
  - create     →  insert a new trend with `weight = intensity`
  - rename     →  no weight change, just metadata
  - (no tools) →  noop

Multi-topic articles (typically YouTube / podcast descriptions that
list several subjects) can emit several reinforce / create intents
in the SAME LLM turn. We apply them atomically: all the boosts
first, then ONE decay pass on every other active trend, then ONE
prune, then ONE snapshot. That way a podcast that bumps three trends
doesn't decay any of those three on its way down the list.

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


EventAction = Literal["reinforce", "create", "rename", "noop", "multi"]


@dataclass(slots=True)
class IntentReinforce:
    trend_id: str
    intensity: float


@dataclass(slots=True)
class IntentCreate:
    name: str
    description: str
    category: str
    intensity: float


@dataclass(slots=True)
class IntentRename:
    trend_id: str
    new_name: str | None = None
    new_description: str | None = None
    new_category: str | None = None


Intent = IntentReinforce | IntentCreate | IntentRename


@dataclass(slots=True)
class EventOutcome:
    """What changed after one article was processed by the engine."""
    action: EventAction
    touched_ids: list[str]
    pruned_ids: list[str]
    snapshot_id: int


def _settings_or_defaults():
    """Read the live tunables from config."""
    from app.config import get_settings

    cfg = get_settings().trends
    return {
        "decay_rate": cfg.decay_rate,
        "prune_threshold": cfg.prune_threshold,
        "max_trends": cfg.max_trends,
        "examples_cap": cfg.examples_cap,
        "categories": list(cfg.categories),
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


def _normalise_category(category: str, allowed: list[str]) -> str:
    """Coerce a free-form category from the LLM into the closed set.
    Case-insensitive match; falls back to 'Other' (or the last entry,
    whichever exists)."""
    if not category:
        return allowed[-1] if allowed else "Other"
    low = category.strip().lower()
    for c in allowed:
        if c.lower() == low:
            return c
    return "Other" if "Other" in allowed else (allowed[-1] if allowed else "Other")


def _apply_decay(
    conn,
    *,
    decay_rate: float,
    skip_ids: set[str],
    fresh_weights: dict[str, float],
) -> dict[str, float]:
    """Multiply every active trend's weight by (1 - decay_rate),
    except those just touched (`skip_ids`). Returns the post-decay
    {trend_id: weight} for ALL active trends.

    `fresh_weights` carries the post-event weights for the touched
    trends — we trust those values rather than re-reading them, since
    a CREATE may have happened in the same batch and the DB row is
    already in its final state."""
    active = store.list_active_trends(conn)
    weights: dict[str, float] = {}
    for t in active:
        if t.id in skip_ids:
            weights[t.id] = fresh_weights.get(t.id, t.weight)
            continue
        new_w = t.weight * (1.0 - decay_rate)
        store.update_weight(conn, t.id, new_w)
        weights[t.id] = new_w
    # Touched trends may include freshly-created ones that aren't yet
    # in `active` if the caller computed them before this call — the
    # store should have inserted them by the time we get here, but be
    # defensive and merge anyway.
    for tid in skip_ids:
        if tid not in weights:
            weights[tid] = fresh_weights.get(tid, 0.0)
    return weights


def _maybe_prune(
    conn, weights: dict[str, float], *, prune_threshold: float,
    max_trends: int, protect_ids: set[str],
) -> list[str]:
    """Soft-delete trends under the threshold OR weakest past the
    cap. Never prunes anything in `protect_ids` (just touched)."""
    pruned: list[str] = []
    for trend_id, w in list(weights.items()):
        if trend_id in protect_ids:
            continue
        if w < prune_threshold:
            store.prune_trend(conn, trend_id)
            pruned.append(trend_id)
            weights.pop(trend_id, None)

    if max_trends > 0 and len(weights) > max_trends:
        ranked = sorted(weights.items(), key=lambda kv: kv[1])
        for trend_id, _ in ranked:
            if len(weights) <= max_trends:
                break
            if trend_id in protect_ids:
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


def _summary_action(
    n_reinforce: int, n_create: int, n_rename: int,
) -> EventAction:
    """Pick a single label for the snapshot's trigger_action when the
    article fired multiple events. 'multi' if there's more than one
    actionable intent, else the dominant single action."""
    actionable = n_reinforce + n_create + n_rename
    if actionable == 0:
        return "noop"
    if actionable > 1:
        return "multi"
    if n_create:
        return "create"
    if n_reinforce:
        return "reinforce"
    return "rename"


# ── Public surface: batch apply ─────────────────────────────────────


def apply_batch(
    conn,
    *,
    article_id: str,
    article_title: str,
    intents: list[Intent],
) -> EventOutcome:
    """Apply one article's worth of LLM intents as a SINGLE event:

      1. Renames first (metadata only).
      2. Creates (new rows, deduped by name → reinforce if already known).
      3. Reinforces (weight bumps + example bookkeeping).
      4. ONE decay pass on every non-touched active trend.
      5. ONE prune pass (touched trends protected).
      6. ONE snapshot.

    Empty intents → noop event (decay + snapshot still happen, so
    every article contributes to the trend lifecycle).
    """
    cfg = _settings_or_defaults()
    allowed = cfg["categories"]

    touched: set[str] = set()
    fresh: dict[str, float] = {}   # post-update weights for touched trends
    n_reinforce = 0
    n_create = 0
    n_rename = 0
    primary_trend_id: str | None = None  # for snapshot's trigger_trend_id

    # 1. Renames
    for it in intents:
        if not isinstance(it, IntentRename):
            continue
        new_cat = (
            _normalise_category(it.new_category, allowed)
            if it.new_category else None
        )
        if store.rename_trend(
            conn, trend_id=it.trend_id,
            new_name=it.new_name,
            new_description=it.new_description,
            new_category=new_cat,
        ):
            n_rename += 1
            primary_trend_id = primary_trend_id or it.trend_id

    # 2. Creates (with dedup-by-name → reinforce existing)
    for it in intents:
        if not isinstance(it, IntentCreate):
            continue
        intensity = max(0.0, min(1.0, it.intensity))
        category = _normalise_category(it.category, allowed)
        existing = store.get_trend_by_name(conn, it.name)
        if existing is not None:
            # LLM picked an existing name — treat as a reinforce, but
            # also refresh the description + category to the new wording.
            store.rename_trend(
                conn, trend_id=existing.id,
                new_name=None,
                new_description=it.description,
                new_category=category if category != existing.category else None,
            )
            new_weight = existing.weight + intensity
            store.reinforce_trend(
                conn, trend_id=existing.id,
                new_weight=new_weight,
                example_title=article_title,
                examples_cap=cfg["examples_cap"],
            )
            touched.add(existing.id)
            fresh[existing.id] = new_weight
            n_reinforce += 1
            primary_trend_id = primary_trend_id or existing.id
            continue

        initial = max(intensity, cfg["prune_threshold"] * 2.0)
        created = store.create_trend(
            conn,
            name=it.name,
            description=it.description,
            category=category,
            weight=initial,
            example_title=article_title,
        )
        touched.add(created.id)
        fresh[created.id] = initial
        n_create += 1
        primary_trend_id = primary_trend_id or created.id

    # 3. Reinforces
    for it in intents:
        if not isinstance(it, IntentReinforce):
            continue
        intensity = max(0.0, min(1.0, it.intensity))
        current = store.get_trend(conn, it.trend_id)
        if current is None or current.pruned_at is not None:
            log.warning(
                "reinforce on unknown/pruned trend_id=%s — ignoring",
                it.trend_id,
            )
            continue
        # If the same trend was both renamed and reinforced in this
        # batch the rename already happened above.
        new_weight = (
            fresh[current.id]
            if current.id in fresh
            else current.weight
        ) + intensity
        store.reinforce_trend(
            conn, trend_id=current.id,
            new_weight=new_weight,
            example_title=article_title,
            examples_cap=cfg["examples_cap"],
        )
        touched.add(current.id)
        fresh[current.id] = new_weight
        n_reinforce += 1
        primary_trend_id = primary_trend_id or current.id

    # 4. ONE decay pass (skips every touched trend, including those
    #    only renamed — a rename shouldn't penalise the trend on
    #    this round).
    weights = _apply_decay(
        conn,
        decay_rate=cfg["decay_rate"],
        skip_ids=touched | {
            it.trend_id for it in intents if isinstance(it, IntentRename)
        },
        fresh_weights=fresh,
    )

    # 5. Prune
    pruned = _maybe_prune(
        conn, weights,
        prune_threshold=cfg["prune_threshold"],
        max_trends=cfg["max_trends"],
        protect_ids=touched | {
            it.trend_id for it in intents if isinstance(it, IntentRename)
        },
    )

    # 6. Snapshot
    action = _summary_action(n_reinforce, n_create, n_rename)
    snap_id = _snapshot(
        conn, article_id=article_id, action=action,
        trend_id=primary_trend_id, weights=weights,
    )
    return EventOutcome(
        action=action,
        touched_ids=sorted(touched),
        pruned_ids=pruned,
        snapshot_id=snap_id,
    )

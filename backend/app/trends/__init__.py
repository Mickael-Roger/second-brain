"""Trends feature.

A "neural-like" weight system over the news stream. Each incoming
article is sent (title + summary) to a small LLM with three tools:

  - reinforce_trend(trend_id, intensity)
  - create_trend(name, description, intensity)
  - rename_trend(trend_id, new_name, new_description?)

After the call we apply a multiplicative decay to every other active
trend, prune trends whose weight falls below a threshold (soft-delete),
and snapshot the full state to `trend_snapshots`. The frontend can
read the current list (`store.list_active_trends`) for the bubble
sizes and the snapshot history for the evolution trend lines.
"""

from .engine import apply_event, EventOutcome
from .assigner import process_article, AssignerError
from .store import (
    TrendRecord,
    list_active_trends,
    list_snapshots_since,
    pending_articles,
    mark_article_processed,
)
from .worker import process_pending, start_worker, stop_worker

__all__ = [
    "AssignerError",
    "EventOutcome",
    "TrendRecord",
    "apply_event",
    "list_active_trends",
    "list_snapshots_since",
    "mark_article_processed",
    "pending_articles",
    "process_article",
    "process_pending",
    "start_worker",
    "stop_worker",
]

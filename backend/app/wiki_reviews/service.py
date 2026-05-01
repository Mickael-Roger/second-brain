"""Selection logic for the wiki review feature.

The candidate pool is `Wiki/**/*.md` minus the `excluded` set. Within
the pool, a note is "ripe" when either:
  - it has never been reviewed (no row in `wiki_reviews`), or
  - its `next_due_at` is in the past.

When at least one ripe candidate exists, we draw from the ripe set.
Otherwise we fall back to the full pool weighted by 1 / (review_count + 1)
so notes the user has seen many times come up less often.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.vault.paths import vault_root

from .repo import ReviewState, all_states

# The user's wiki lives under this folder by convention. Hard-coded by
# design — the review feature is *for* the wiki, not a generic vault
# spaced-repetition tool.
WIKI_FOLDER = "Wiki"


@dataclass(slots=True)
class Pick:
    path: str
    state: ReviewState | None  # None if the note has never been reviewed


def scope_root() -> Path:
    """Absolute filesystem path of the Wiki/ folder inside the vault."""
    return vault_root() / WIKI_FOLDER


def list_candidates() -> list[str]:
    """All `Wiki/**/*.md` paths, vault-relative, hidden entries skipped."""
    base = scope_root()
    if not base.is_dir():
        return []
    root = vault_root()
    out: list[str] = []
    for p in base.rglob("*.md"):
        rel_parts = p.relative_to(root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        out.append(p.relative_to(root).as_posix())
    return out


def pick_next(
    conn: sqlite3.Connection,
    *,
    rng: random.Random | None = None,
) -> Pick | None:
    """Return the next note to review, or None if the candidate pool
    is empty (no Wiki/ pages, or every page is excluded)."""
    rng = rng or random.Random()

    states_by_path: dict[str, ReviewState] = {s.path: s for s in all_states(conn)}

    pool: list[str] = [
        p for p in list_candidates()
        if not (states_by_path.get(p) and states_by_path[p].excluded)
    ]
    if not pool:
        return None

    now_iso = datetime.now(timezone.utc).isoformat()

    ripe: list[str] = []
    for p in pool:
        st = states_by_path.get(p)
        if st is None or st.next_due_at <= now_iso:
            ripe.append(p)

    if ripe:
        chosen = rng.choice(ripe)
    else:
        # Everything has a future due date — weighted draw so notes
        # reviewed often slide to the back of the line.
        weights = [
            1.0 / (1.0 + states_by_path[p].review_count) for p in pool
        ]
        chosen = rng.choices(pool, weights=weights, k=1)[0]

    return Pick(path=chosen, state=states_by_path.get(chosen))

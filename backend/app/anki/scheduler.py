"""SM-2 review scheduler — answer a card and update its row.

Anki's actual scheduler is more involved (learning steps, fuzz,
hard/easy multipliers tied to deck config, leech detection). We
implement a clean SM-2 that's compatible enough for AnkiWeb to
accept the resulting cards/revlog rows when uploaded.

Storage conventions we follow (matching Anki):
- `factor` is stored ×1000 (2500 = 2.5 ease).
- `queue`: 0 new, 1 learning, 2 review, 3 relearning.
- `type`:  0 new, 1 learning, 2 review, 3 relearning.
- `due` for new/review: integer days since the collection's `crt`.
- `due` for (re)learning: epoch seconds.

We use UTC-day for "today" — same drift Anki has for users in non-UTC
timezones, but consistent and simple.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from app.anki.repo import AnkiCard, get_card

# Ease (button) values.
EASE_AGAIN = 1
EASE_HARD = 2
EASE_GOOD = 3
EASE_EASY = 4

# Tunables.
STARTING_EASE = 2500       # 2.5 — Anki default for new cards graduating
EASE_MIN = 1300            # 1.3 floor
HARD_MULTIPLIER = 1.2
EASY_BONUS = 1.3
EASE_DELTA_HARD = -150
EASE_DELTA_EASY = 150
EASE_DELTA_AGAIN = -200    # lapse penalty
GRADUATING_INTERVAL_GOOD = 1   # days
GRADUATING_INTERVAL_EASY = 4   # days
RELEARN_DELAY_S = 600          # 10 min
MAX_INTERVAL = 36500           # ~100 years cap (Anki default)


@dataclass(slots=True)
class ReviewResult:
    card: AnkiCard
    show_in: int          # seconds until the card is due again (for the UI)


def _now_s() -> int:
    return int(time.time())


def _today_days(crt: int) -> int:
    """Days since the collection's creation timestamp (`col.crt`, seconds)."""
    return max(0, (_now_s() - crt) // 86400)


def _read_crt(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT crt FROM col WHERE id = 1").fetchone()
    return int(row["crt"]) if row else _now_s()


def _next_state(card: AnkiCard, ease: int, today: int) -> tuple[int, int, int, int, int, int, int]:
    """Compute (type, queue, due, ivl, factor, reps, lapses) after `ease`.

    Returns ints in the same units the cards table stores them.
    """
    typ = card.type
    queue = card.queue
    ivl = card.ivl
    factor = card.factor or STARTING_EASE
    reps = card.reps + 1
    lapses = card.lapses

    if ease == EASE_AGAIN:
        # Lapse: drop into relearning, due 10 min from now.
        if typ == 2:
            lapses += 1
            factor = max(EASE_MIN, factor + EASE_DELTA_AGAIN)
        new_typ = 3 if typ == 2 else 1
        new_queue = 3 if typ == 2 else 1
        new_due = _now_s() + RELEARN_DELAY_S
        new_ivl = 0
        return new_typ, new_queue, new_due, new_ivl, factor, reps, lapses

    # New card → schedule first interval based on the button.
    if typ == 0:
        if ease == EASE_EASY:
            new_ivl = GRADUATING_INTERVAL_EASY
            factor = max(EASE_MIN, STARTING_EASE + EASE_DELTA_EASY)
        else:
            new_ivl = GRADUATING_INTERVAL_GOOD
            factor = STARTING_EASE
        new_due = today + new_ivl
        return 2, 2, new_due, min(new_ivl, MAX_INTERVAL), factor, reps, lapses

    # Learning / relearning → graduate to review on Good or Easy.
    if typ in (1, 3):
        if ease == EASE_HARD:
            new_ivl = max(1, int(round(max(ivl, 1) * HARD_MULTIPLIER)))
        elif ease == EASE_EASY:
            new_ivl = max(GRADUATING_INTERVAL_EASY, int(round(max(ivl, 1) * factor / 1000 * EASY_BONUS)))
            factor = max(EASE_MIN, factor + EASE_DELTA_EASY)
        else:  # GOOD
            new_ivl = max(GRADUATING_INTERVAL_GOOD, int(round(max(ivl, 1) * factor / 1000)))
        new_ivl = min(new_ivl, MAX_INTERVAL)
        return 2, 2, today + new_ivl, new_ivl, factor, reps, lapses

    # Review card → SM-2 update.
    if ease == EASE_HARD:
        new_ivl = max(ivl + 1, int(round(ivl * HARD_MULTIPLIER)))
        factor = max(EASE_MIN, factor + EASE_DELTA_HARD)
    elif ease == EASE_EASY:
        new_ivl = max(ivl + 1, int(round(ivl * factor / 1000 * EASY_BONUS)))
        factor = max(EASE_MIN, factor + EASE_DELTA_EASY)
    else:  # GOOD
        new_ivl = max(ivl + 1, int(round(ivl * factor / 1000)))
    new_ivl = min(new_ivl, MAX_INTERVAL)
    return 2, 2, today + new_ivl, new_ivl, factor, reps, lapses


def answer_card(
    conn: sqlite3.Connection,
    card_id: int,
    ease: int,
    *,
    time_ms: int = 0,
) -> ReviewResult:
    """Record a review answer (ease 1..4) on `card_id`.

    Updates the card in-place and writes a `revlog` row. Bumps
    `col.mod` so AnkiWeb sees changes.
    """
    if ease not in (EASE_AGAIN, EASE_HARD, EASE_GOOD, EASE_EASY):
        raise ValueError(f"ease must be 1..4, got {ease}")

    card = get_card(conn, card_id)
    if card is None:
        raise KeyError("card not found")

    crt = _read_crt(conn)
    today = _today_days(crt)
    last_ivl = card.ivl

    new_type, new_queue, new_due, new_ivl, new_factor, new_reps, new_lapses = _next_state(
        card, ease, today
    )

    now_ms = int(time.time() * 1000)
    revlog_id = now_ms
    # Ensure unique revlog id (rapid clicks).
    while conn.execute("SELECT 1 FROM revlog WHERE id = ?", (revlog_id,)).fetchone():
        revlog_id += 1

    conn.execute("BEGIN")
    try:
        conn.execute(
            """UPDATE cards
               SET type = ?, queue = ?, due = ?, ivl = ?, factor = ?, reps = ?, lapses = ?,
                   mod = ?, usn = -1
               WHERE id = ?""",
            (
                new_type, new_queue, new_due, new_ivl, new_factor, new_reps, new_lapses,
                int(time.time()), card_id,
            ),
        )
        conn.execute(
            """INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type)
               VALUES (?, ?, -1, ?, ?, ?, ?, ?, ?)""",
            (
                revlog_id, card_id, ease,
                new_ivl, last_ivl, new_factor,
                max(0, time_ms),
                # revlog.type: 0=learn, 1=review, 2=relearn, 3=cram
                0 if card.type in (0, 1) else (2 if card.type == 3 else 1),
            ),
        )
        conn.execute("UPDATE col SET mod = ?, usn = -1 WHERE id = 1", (now_ms,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    refreshed = get_card(conn, card_id)
    assert refreshed is not None
    if refreshed.queue == 2:
        show_in = max(0, (refreshed.due - today)) * 86400
    elif refreshed.queue in (1, 3):
        show_in = max(0, refreshed.due - _now_s())
    else:
        show_in = 0
    return ReviewResult(card=refreshed, show_in=show_in)


__all__ = [
    "EASE_AGAIN",
    "EASE_EASY",
    "EASE_GOOD",
    "EASE_HARD",
    "ReviewResult",
    "answer_card",
]

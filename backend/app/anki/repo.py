"""CRUD operations against the Anki collection (raw SQL).

Slim by design: read access for decks and notes, plus add_note.
Decks are managed externally (Anki desktop / AnkiWeb) and pulled
in via sync_download. Mutations beyond add_note are NOT exposed.

After a write we bump `col.mod` and set `usn=-1` so the next
sync_upload knows there are pending changes for AnkiWeb.
"""

from __future__ import annotations

import hashlib
import random
import re
import sqlite3
import string
import time
from dataclasses import dataclass

from app.anki.schema import (
    NOTETYPE_BASIC_ID,
    NOTETYPE_BASIC_REVERSE_ID,
)

# Public notetype string IDs.
NOTETYPE_BASIC = "basic"
NOTETYPE_BASIC_REVERSE = "basic_reverse"

_NOTETYPE_TO_MID = {
    NOTETYPE_BASIC: NOTETYPE_BASIC_ID,
    NOTETYPE_BASIC_REVERSE: NOTETYPE_BASIC_REVERSE_ID,
}

_MID_TO_NOTETYPE = {v: k for k, v in _NOTETYPE_TO_MID.items()}

FIELD_SEP = "\x1f"


# ── Dataclasses ──────────────────────────────────────────────────────


@dataclass(slots=True)
class AnkiDeck:
    id: int
    name: str
    card_count: int
    new_count: int
    due_count: int


@dataclass(slots=True)
class AnkiCard:
    id: int
    nid: int
    did: int
    ord: int
    type: int
    queue: int
    due: int
    ivl: int
    factor: int
    reps: int
    lapses: int


@dataclass(slots=True)
class AnkiNote:
    id: int
    deck_id: int
    notetype: str
    fields: list[str]
    tags: list[str]
    cards: list[AnkiCard]


# ── Helpers ──────────────────────────────────────────────────────────


_GUID_ALPHABET = (
    string.ascii_lowercase + string.ascii_uppercase + string.digits + "!#$%&()*+,-./:;<=>?@[]^_`{|}~"
)


def _new_guid() -> str:
    """10-char base-91-ish identifier matching Anki's note GUID format."""
    return "".join(random.choices(_GUID_ALPHABET, k=10))


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]*>", "", s)


def _csum(field0: str) -> int:
    h = hashlib.sha1(_strip_html(field0).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id_ms(conn: sqlite3.Connection, table: str, column: str = "id") -> int:
    base = _now_ms()
    while True:
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE {column} = ?", (base,)
        ).fetchone()
        if row is None:
            return base
        base += 1


def _bump_col_mod(conn: sqlite3.Connection) -> None:
    """Mark the collection as modified — `usn=-1` is Anki's convention
    for 'has unsynced changes', and bumping `mod` makes AnkiWeb's
    /sync/meta detect that we're ahead of the server."""
    conn.execute("UPDATE col SET mod = ?, usn = ? WHERE id = 1", (_now_ms(), -1))


# ── Decks (read-only) ────────────────────────────────────────────────


def list_decks(conn: sqlite3.Connection) -> list[AnkiDeck]:
    """All decks with card counts. Today is computed in UTC days."""
    today = int(time.time() / 86400)
    rows = conn.execute(
        """
        SELECT
          d.id   AS id,
          d.name AS name,
          (SELECT COUNT(*) FROM cards c WHERE c.did = d.id)                                 AS card_count,
          (SELECT COUNT(*) FROM cards c WHERE c.did = d.id AND c.queue = 0)                 AS new_count,
          (SELECT COUNT(*) FROM cards c WHERE c.did = d.id AND c.queue IN (1,2,3) AND c.due <= ?) AS due_count
        FROM decks d
        ORDER BY d.name COLLATE NOCASE
        """,
        (today,),
    ).fetchall()
    return [
        AnkiDeck(
            id=r["id"],
            name=r["name"],
            card_count=r["card_count"],
            new_count=r["new_count"],
            due_count=r["due_count"],
        )
        for r in rows
    ]


def find_deck_by_name(conn: sqlite3.Connection, name: str) -> AnkiDeck | None:
    """Lookup a deck by name (case-insensitive). None if not found.
    Useful when importing from vault files that reference decks by name."""
    row = conn.execute(
        """
        SELECT
          d.id   AS id,
          d.name AS name,
          (SELECT COUNT(*) FROM cards c WHERE c.did = d.id)                                 AS card_count,
          (SELECT COUNT(*) FROM cards c WHERE c.did = d.id AND c.queue = 0)                 AS new_count,
          (SELECT COUNT(*) FROM cards c WHERE c.did = d.id AND c.queue IN (1,2,3) AND c.due <= ?) AS due_count
        FROM decks d
        WHERE d.name = ? COLLATE NOCASE
        """,
        (int(time.time() / 86400), name),
    ).fetchone()
    if row is None:
        return None
    return AnkiDeck(
        id=row["id"], name=row["name"],
        card_count=row["card_count"], new_count=row["new_count"], due_count=row["due_count"],
    )


# ── Notes (read + add) ───────────────────────────────────────────────


def _row_to_card(r: sqlite3.Row) -> AnkiCard:
    return AnkiCard(
        id=r["id"], nid=r["nid"], did=r["did"], ord=r["ord"],
        type=r["type"], queue=r["queue"], due=r["due"], ivl=r["ivl"],
        factor=r["factor"], reps=r["reps"], lapses=r["lapses"],
    )


def _load_note(conn: sqlite3.Connection, note_id: int) -> AnkiNote | None:
    row = conn.execute(
        "SELECT id, mid, tags, flds FROM notes WHERE id = ?", (note_id,)
    ).fetchone()
    if row is None:
        return None
    cards = [
        _row_to_card(r)
        for r in conn.execute(
            "SELECT * FROM cards WHERE nid = ? ORDER BY ord", (note_id,)
        ).fetchall()
    ]
    deck_id = cards[0].did if cards else 1
    notetype = _MID_TO_NOTETYPE.get(row["mid"], NOTETYPE_BASIC)
    return AnkiNote(
        id=row["id"],
        deck_id=deck_id,
        notetype=notetype,
        fields=row["flds"].split(FIELD_SEP),
        tags=[t for t in row["tags"].strip().split(" ") if t],
        cards=cards,
    )


def get_note(conn: sqlite3.Connection, note_id: int) -> AnkiNote | None:
    return _load_note(conn, note_id)


def list_notes(
    conn: sqlite3.Connection,
    *,
    deck_id: int | None = None,
    search: str | None = None,
    limit: int = 200,
) -> list[AnkiNote]:
    query = """
        SELECT DISTINCT n.id
        FROM notes n
    """
    params: list[object] = []
    where: list[str] = []
    if deck_id is not None:
        query += " JOIN cards c ON c.nid = n.id "
        where.append("c.did = ?")
        params.append(deck_id)
    if search:
        where.append("n.flds LIKE ?")
        params.append(f"%{search}%")
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY n.id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [n for n in (_load_note(conn, r["id"]) for r in rows) if n is not None]


def add_note(
    conn: sqlite3.Connection,
    *,
    deck_id: int,
    notetype: str,
    fields: list[str],
    tags: list[str] | None = None,
) -> AnkiNote:
    if notetype not in _NOTETYPE_TO_MID:
        raise ValueError(f"unknown notetype '{notetype}'")
    if len(fields) != 2:
        raise ValueError("Basic / Basic+Reverse notes require exactly 2 fields (Front, Back)")
    front, back = fields[0].strip(), fields[1].strip()
    if not front or not back:
        raise ValueError("both Front and Back must be non-empty")

    deck_row = conn.execute("SELECT id FROM decks WHERE id = ?", (deck_id,)).fetchone()
    if deck_row is None:
        raise KeyError("deck not found")

    mid = _NOTETYPE_TO_MID[notetype]
    n_cards = 1 if notetype == NOTETYPE_BASIC else 2
    tag_str = " " + " ".join(t.strip() for t in (tags or []) if t.strip()) + " " if tags else ""

    conn.execute("BEGIN")
    try:
        nid = _new_id_ms(conn, "notes")
        flds = FIELD_SEP.join([front, back])
        conn.execute(
            """INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data)
               VALUES (?, ?, ?, ?, -1, ?, ?, ?, ?, 0, '')""",
            (nid, _new_guid(), mid, int(time.time()), tag_str, flds, front, _csum(front)),
        )
        # Cards. New cards get queue=0/type=0, due=note id (Anki orders new
        # cards by insertion).
        for ord_ in range(n_cards):
            cid = _new_id_ms(conn, "cards") + ord_
            while conn.execute("SELECT 1 FROM cards WHERE id = ?", (cid,)).fetchone():
                cid += 1
            conn.execute(
                """INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, left, odue, odid, flags, data)
                   VALUES (?, ?, ?, ?, ?, -1, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')""",
                (cid, nid, deck_id, ord_, int(time.time()), nid),
            )
        _bump_col_mod(conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    note = _load_note(conn, nid)
    assert note is not None
    return note


__all__ = [
    "AnkiCard",
    "AnkiDeck",
    "AnkiNote",
    "FIELD_SEP",
    "NOTETYPE_BASIC",
    "NOTETYPE_BASIC_REVERSE",
    "add_note",
    "find_deck_by_name",
    "get_note",
    "list_decks",
    "list_notes",
]

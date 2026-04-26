"""CRUD operations against the Anki collection (raw SQL).

We touch the `col` JSON columns (`models`, `decks`) for back-compat
on top of the schema-18 structured tables, and bump `col.mod` after
every write so AnkiWeb sees we have changes pending.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
import string
import time
from dataclasses import dataclass

from app.anki.schema import (
    DEFAULT_DECK_ID,
    NOTETYPE_BASIC_ID,
    NOTETYPE_BASIC_REVERSE_ID,
)

# Public notetype string IDs used by the API layer.
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
    """Anki-compatible note checksum: first 8 hex chars of SHA-1 of the
    de-HTML'd first field, parsed as int."""
    h = hashlib.sha1(_strip_html(field0).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id_ms(conn: sqlite3.Connection, table: str, column: str = "id") -> int:
    """Generate a unique millisecond-resolution id for a notes/cards/revlog
    insert. Anki uses ms-since-epoch as the primary key for these rows;
    on rapid creation we may collide, so bump forward until free."""
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


def _read_col_json(conn: sqlite3.Connection) -> tuple[dict, dict, dict, dict]:
    row = conn.execute(
        "SELECT conf, models, decks, dconf FROM col WHERE id = 1"
    ).fetchone()
    return (
        json.loads(row["conf"]),
        json.loads(row["models"]),
        json.loads(row["decks"]),
        json.loads(row["dconf"]),
    )


def _write_col_json(
    conn: sqlite3.Connection,
    *,
    conf: dict | None = None,
    models: dict | None = None,
    decks: dict | None = None,
    dconf: dict | None = None,
) -> None:
    fields = []
    values: list[str] = []
    if conf is not None:
        fields.append("conf = ?")
        values.append(json.dumps(conf))
    if models is not None:
        fields.append("models = ?")
        values.append(json.dumps(models))
    if decks is not None:
        fields.append("decks = ?")
        values.append(json.dumps(decks))
    if dconf is not None:
        fields.append("dconf = ?")
        values.append(json.dumps(dconf))
    if not fields:
        return
    conn.execute(f"UPDATE col SET {', '.join(fields)} WHERE id = 1", values)


# ── Decks ────────────────────────────────────────────────────────────


def list_decks(conn: sqlite3.Connection) -> list[AnkiDeck]:
    """All decks with card counts. Today is computed in UTC."""
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


def create_deck(conn: sqlite3.Connection, name: str) -> AnkiDeck:
    name = name.strip()
    if not name:
        raise ValueError("deck name cannot be empty")
    existing = conn.execute(
        "SELECT id FROM decks WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if existing is not None:
        raise ValueError(f"deck '{name}' already exists")

    deck_id = _new_id_ms(conn, "decks")
    now_s = int(time.time())

    conn.execute("BEGIN")
    try:
        conn.execute(
            """INSERT INTO decks (id, name, mtime_secs, usn, common, kind)
               VALUES (?, ?, ?, -1, ?, ?)""",
            (deck_id, name, now_s, b"", b""),
        )
        # Mirror into the col.decks JSON.
        _, _, decks_json, _ = _read_col_json(conn)
        decks_json[str(deck_id)] = {
            "id": deck_id,
            "name": name,
            "mod": now_s,
            "usn": -1,
            "lrnToday": [0, 0],
            "revToday": [0, 0],
            "newToday": [0, 0],
            "timeToday": [0, 0],
            "collapsed": False,
            "browserCollapsed": False,
            "desc": "",
            "dyn": 0,
            "conf": 1,
            "extendNew": 10,
            "extendRev": 50,
        }
        _write_col_json(conn, decks=decks_json)
        _bump_col_mod(conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return AnkiDeck(id=deck_id, name=name, card_count=0, new_count=0, due_count=0)


def rename_deck(conn: sqlite3.Connection, deck_id: int, new_name: str) -> None:
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("deck name cannot be empty")
    if deck_id == DEFAULT_DECK_ID:
        raise ValueError("cannot rename the Default deck")
    row = conn.execute("SELECT 1 FROM decks WHERE id = ?", (deck_id,)).fetchone()
    if row is None:
        raise KeyError("deck not found")

    conn.execute("BEGIN")
    try:
        conn.execute(
            "UPDATE decks SET name = ?, mtime_secs = ?, usn = -1 WHERE id = ?",
            (new_name, int(time.time()), deck_id),
        )
        _, _, decks_json, _ = _read_col_json(conn)
        if str(deck_id) in decks_json:
            decks_json[str(deck_id)]["name"] = new_name
            decks_json[str(deck_id)]["mod"] = int(time.time())
            decks_json[str(deck_id)]["usn"] = -1
        _write_col_json(conn, decks=decks_json)
        _bump_col_mod(conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def delete_deck(conn: sqlite3.Connection, deck_id: int) -> None:
    if deck_id == DEFAULT_DECK_ID:
        raise ValueError("cannot delete the Default deck")
    row = conn.execute("SELECT 1 FROM decks WHERE id = ?", (deck_id,)).fetchone()
    if row is None:
        raise KeyError("deck not found")

    conn.execute("BEGIN")
    try:
        # Find notes that will become orphaned, then their cards.
        note_ids = [
            r["id"]
            for r in conn.execute(
                "SELECT DISTINCT n.id FROM notes n JOIN cards c ON c.nid = n.id WHERE c.did = ?",
                (deck_id,),
            ).fetchall()
        ]
        # Anki distinguishes deck graves from card/note graves.
        # graves.type: 0 = card, 1 = note, 2 = deck.
        for nid in note_ids:
            for cr in conn.execute("SELECT id FROM cards WHERE nid = ? AND did = ?", (nid, deck_id)).fetchall():
                conn.execute("INSERT INTO graves (oid, type, usn) VALUES (?, 0, -1)", (cr["id"],))
            conn.execute("INSERT INTO graves (oid, type, usn) VALUES (?, 1, -1)", (nid,))
        conn.execute("DELETE FROM cards WHERE did = ?", (deck_id,))
        conn.execute(
            "DELETE FROM notes WHERE id IN (SELECT id FROM notes WHERE id NOT IN (SELECT nid FROM cards))"
        )
        conn.execute("INSERT INTO graves (oid, type, usn) VALUES (?, 2, -1)", (deck_id,))
        conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))

        _, _, decks_json, _ = _read_col_json(conn)
        decks_json.pop(str(deck_id), None)
        _write_col_json(conn, decks=decks_json)
        _bump_col_mod(conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ── Notes & cards ────────────────────────────────────────────────────


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
    deck_id = cards[0].did if cards else DEFAULT_DECK_ID
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
            cid = _new_id_ms(conn, "cards")
            # Bump uniqueness: if multiple cards within same ms, the inner
            # loop's _new_id_ms may return the same value because we haven't
            # inserted yet — explicitly offset by ord.
            cid = cid + ord_
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


def update_note(
    conn: sqlite3.Connection,
    note_id: int,
    *,
    fields: list[str] | None = None,
    tags: list[str] | None = None,
) -> AnkiNote:
    note = _load_note(conn, note_id)
    if note is None:
        raise KeyError("note not found")

    new_fields = fields if fields is not None else note.fields
    if len(new_fields) != 2:
        raise ValueError("Basic / Basic+Reverse notes require exactly 2 fields")
    front, back = new_fields[0].strip(), new_fields[1].strip()
    if not front or not back:
        raise ValueError("both Front and Back must be non-empty")
    flds = FIELD_SEP.join([front, back])

    new_tags = tags if tags is not None else note.tags
    tag_str = " " + " ".join(t.strip() for t in new_tags if t.strip()) + " " if new_tags else ""

    conn.execute("BEGIN")
    try:
        conn.execute(
            """UPDATE notes
               SET flds = ?, sfld = ?, csum = ?, tags = ?, mod = ?, usn = -1
               WHERE id = ?""",
            (flds, front, _csum(front), tag_str, int(time.time()), note_id),
        )
        _bump_col_mod(conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    refreshed = _load_note(conn, note_id)
    assert refreshed is not None
    return refreshed


def delete_note(conn: sqlite3.Connection, note_id: int) -> None:
    note = _load_note(conn, note_id)
    if note is None:
        raise KeyError("note not found")

    conn.execute("BEGIN")
    try:
        for c in note.cards:
            conn.execute("INSERT INTO graves (oid, type, usn) VALUES (?, 0, -1)", (c.id,))
        conn.execute("DELETE FROM cards WHERE nid = ?", (note_id,))
        conn.execute("INSERT INTO graves (oid, type, usn) VALUES (?, 1, -1)", (note_id,))
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        _bump_col_mod(conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ── Review queue ─────────────────────────────────────────────────────


def next_due_card(conn: sqlite3.Connection, deck_id: int) -> AnkiCard | None:
    """Pick the next card to review in this deck.

    Order:
      1. learning/relearning cards whose `due` (epoch seconds) is in the past
      2. review cards whose `due` (days-since-collection-start) is today or earlier
      3. new cards by `due` ascending
    """
    today_days = int(time.time() / 86400)
    now_s = int(time.time())

    # learning / relearning: due is epoch seconds.
    row = conn.execute(
        """SELECT * FROM cards
           WHERE did = ? AND queue IN (1, 3) AND due <= ?
           ORDER BY due ASC LIMIT 1""",
        (deck_id, now_s),
    ).fetchone()
    if row is not None:
        return _row_to_card(row)

    # review cards: due is days.
    row = conn.execute(
        """SELECT * FROM cards
           WHERE did = ? AND queue = 2 AND due <= ?
           ORDER BY due ASC LIMIT 1""",
        (deck_id, today_days),
    ).fetchone()
    if row is not None:
        return _row_to_card(row)

    # new cards.
    row = conn.execute(
        """SELECT * FROM cards
           WHERE did = ? AND queue = 0
           ORDER BY due ASC LIMIT 1""",
        (deck_id,),
    ).fetchone()
    if row is not None:
        return _row_to_card(row)

    return None


def get_card(conn: sqlite3.Connection, card_id: int) -> AnkiCard | None:
    row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return _row_to_card(row) if row is not None else None


def card_render(conn: sqlite3.Connection, card: AnkiCard) -> dict[str, str]:
    """Return {front, back} HTML for the card based on its notetype/template.

    Basic = template ord 0: front field on front, back field on back.
    Basic+Reverse: ord 0 same as Basic; ord 1 swaps Front and Back.
    """
    note = conn.execute(
        "SELECT mid, flds FROM notes WHERE id = ?", (card.nid,)
    ).fetchone()
    if note is None:
        return {"front": "", "back": ""}
    parts = note["flds"].split(FIELD_SEP)
    front_fld = parts[0] if len(parts) > 0 else ""
    back_fld = parts[1] if len(parts) > 1 else ""
    if note["mid"] == NOTETYPE_BASIC_REVERSE_ID and card.ord == 1:
        return {"front": back_fld, "back": front_fld}
    return {"front": front_fld, "back": back_fld}


__all__ = [
    "AnkiCard",
    "AnkiDeck",
    "AnkiNote",
    "FIELD_SEP",
    "NOTETYPE_BASIC",
    "NOTETYPE_BASIC_REVERSE",
    "add_note",
    "card_render",
    "create_deck",
    "delete_deck",
    "delete_note",
    "get_card",
    "get_note",
    "list_decks",
    "list_notes",
    "next_due_card",
    "rename_deck",
    "update_note",
]

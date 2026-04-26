"""Bootstraps an empty `collection.anki2` matching Anki schema-18.

Why this file matters: AnkiWeb validates the SQLite file we POST to
/sync/upload. If the schema, indexes, or `col` row are off, the
upload fails. We only create the file once, on first run; after that
all writes go through `repo.py`.

References:
- ankitects/anki rslib/src/storage/sqlite.rs (table DDL)
- ankitects/anki rslib/src/storage/upgrades/v18.rs (schema 18 layout)
- ankidroid wiki Database-Structure
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

from app.anki.connection import anki_db_path, anki_dir

log = logging.getLogger(__name__)


# Schema constants
SCHEMA_VERSION = 18
DEFAULT_DECK_ID = 1
DEFAULT_DCONF_ID = 1
NOTETYPE_BASIC_ID = 1
NOTETYPE_BASIC_REVERSE_ID = 2


# ── SQL: schema-18 tables ────────────────────────────────────────────


_DDL: list[str] = [
    # `col` keeps a single row. The JSON columns (conf/models/decks/dconf/tags)
    # are kept in addition to the dedicated tables for back-compat — older Anki
    # readers (and importer code paths) still look at them.
    """CREATE TABLE col (
        id     INTEGER PRIMARY KEY,
        crt    INTEGER NOT NULL,
        mod    INTEGER NOT NULL,
        scm    INTEGER NOT NULL,
        ver    INTEGER NOT NULL,
        dty    INTEGER NOT NULL,
        usn    INTEGER NOT NULL,
        ls     INTEGER NOT NULL,
        conf   TEXT    NOT NULL,
        models TEXT    NOT NULL,
        decks  TEXT    NOT NULL,
        dconf  TEXT    NOT NULL,
        tags   TEXT    NOT NULL
    )""",
    """CREATE TABLE notes (
        id    INTEGER PRIMARY KEY,
        guid  TEXT    NOT NULL,
        mid   INTEGER NOT NULL,
        mod   INTEGER NOT NULL,
        usn   INTEGER NOT NULL,
        tags  TEXT    NOT NULL,
        flds  TEXT    NOT NULL,
        sfld  INTEGER NOT NULL,
        csum  INTEGER NOT NULL,
        flags INTEGER NOT NULL,
        data  TEXT    NOT NULL
    )""",
    """CREATE TABLE cards (
        id     INTEGER PRIMARY KEY,
        nid    INTEGER NOT NULL,
        did    INTEGER NOT NULL,
        ord    INTEGER NOT NULL,
        mod    INTEGER NOT NULL,
        usn    INTEGER NOT NULL,
        type   INTEGER NOT NULL,
        queue  INTEGER NOT NULL,
        due    INTEGER NOT NULL,
        ivl    INTEGER NOT NULL,
        factor INTEGER NOT NULL,
        reps   INTEGER NOT NULL,
        lapses INTEGER NOT NULL,
        left   INTEGER NOT NULL,
        odue   INTEGER NOT NULL,
        odid   INTEGER NOT NULL,
        flags  INTEGER NOT NULL,
        data   TEXT    NOT NULL
    )""",
    """CREATE TABLE revlog (
        id      INTEGER PRIMARY KEY,
        cid     INTEGER NOT NULL,
        usn     INTEGER NOT NULL,
        ease    INTEGER NOT NULL,
        ivl     INTEGER NOT NULL,
        lastIvl INTEGER NOT NULL,
        factor  INTEGER NOT NULL,
        time    INTEGER NOT NULL,
        type    INTEGER NOT NULL
    )""",
    """CREATE TABLE graves (
        oid  INTEGER NOT NULL,
        type INTEGER NOT NULL,
        usn  INTEGER NOT NULL
    )""",
    # Schema 18: structured tables that mirror the legacy JSON blobs.
    """CREATE TABLE decks (
        id          INTEGER PRIMARY KEY NOT NULL,
        name        TEXT             NOT NULL COLLATE NOCASE,
        mtime_secs  INTEGER          NOT NULL,
        usn         INTEGER          NOT NULL,
        common      BLOB             NOT NULL,
        kind        BLOB             NOT NULL
    )""",
    """CREATE TABLE deck_config (
        id          INTEGER PRIMARY KEY NOT NULL,
        name        TEXT             NOT NULL COLLATE NOCASE,
        mtime_secs  INTEGER          NOT NULL,
        usn         INTEGER          NOT NULL,
        config      BLOB             NOT NULL
    )""",
    """CREATE TABLE notetypes (
        id          INTEGER PRIMARY KEY NOT NULL,
        name        TEXT             NOT NULL COLLATE NOCASE,
        mtime_secs  INTEGER          NOT NULL,
        usn         INTEGER          NOT NULL,
        config      BLOB             NOT NULL
    )""",
    """CREATE TABLE fields (
        ntid     INTEGER NOT NULL,
        ord      INTEGER NOT NULL,
        name     TEXT    NOT NULL COLLATE NOCASE,
        config   BLOB    NOT NULL,
        PRIMARY KEY (ntid, ord)
    ) WITHOUT ROWID""",
    """CREATE TABLE templates (
        ntid        INTEGER NOT NULL,
        ord         INTEGER NOT NULL,
        name        TEXT    NOT NULL COLLATE NOCASE,
        mtime_secs  INTEGER NOT NULL,
        usn         INTEGER NOT NULL,
        config      BLOB    NOT NULL,
        PRIMARY KEY (ntid, ord)
    ) WITHOUT ROWID""",
    """CREATE TABLE tags (
        tag    TEXT NOT NULL PRIMARY KEY COLLATE NOCASE,
        usn    INTEGER NOT NULL,
        collapsed BOOLEAN NOT NULL,
        config BLOB
    ) WITHOUT ROWID""",
    """CREATE TABLE config (
        KEY        TEXT NOT NULL PRIMARY KEY,
        usn        INTEGER NOT NULL,
        mtime_secs INTEGER NOT NULL,
        val        BLOB NOT NULL
    ) WITHOUT ROWID""",
    # Anki's own schema-tracking table. Required by the Rust backend
    # readers if they ever open the file; harmless otherwise.
    """CREATE TABLE android_metadata ( locale TEXT )""",
    # Indexes Anki itself creates.
    "CREATE INDEX ix_notes_csum ON notes (csum)",
    "CREATE INDEX ix_notes_usn ON notes (usn)",
    "CREATE INDEX ix_cards_nid ON cards (nid)",
    "CREATE INDEX ix_cards_sched ON cards (did, queue, due)",
    "CREATE INDEX ix_cards_usn ON cards (usn)",
    "CREATE INDEX ix_revlog_cid ON revlog (cid)",
    "CREATE INDEX ix_revlog_usn ON revlog (usn)",
    "CREATE INDEX idx_notes_mid ON notes (mid)",
]


# ── Default JSON blobs for the `col` row ─────────────────────────────


def _default_conf() -> dict[str, Any]:
    """Anki's default `col.conf` JSON (collection-wide settings)."""
    return {
        "activeDecks": [DEFAULT_DECK_ID],
        "addToCur": True,
        "collapseTime": 1200,
        "curDeck": DEFAULT_DECK_ID,
        "curModel": NOTETYPE_BASIC_ID,
        "dueCounts": True,
        "estTimes": True,
        "newBury": True,
        "newSpread": 0,
        "nextPos": 1,
        "schedVer": 2,
        "sortBackwards": False,
        "sortType": "noteFld",
        "timeLim": 0,
    }


def _default_dconf_json() -> dict[str, Any]:
    """One-entry deck-config JSON keyed by id."""
    return {
        str(DEFAULT_DCONF_ID): {
            "id": DEFAULT_DCONF_ID,
            "name": "Default",
            "replayq": True,
            "lapse": {
                "delays": [10],
                "leechAction": 1,
                "leechFails": 8,
                "minInt": 1,
                "mult": 0,
            },
            "rev": {
                "perDay": 200,
                "ease4": 1.3,
                "ivlFct": 1.0,
                "maxIvl": 36500,
                "hardFactor": 1.2,
                "bury": False,
                "minSpace": 1,
                "fuzz": 0.05,
            },
            "new": {
                "delays": [1, 10],
                "ints": [1, 4, 0],
                "initialFactor": 2500,
                "perDay": 20,
                "order": 1,
                "bury": False,
                "separate": True,
            },
            "timer": 0,
            "maxTaken": 60,
            "usn": 0,
            "mod": 0,
            "autoplay": True,
            "dyn": False,
        }
    }


def _default_decks_json(now_s: int) -> dict[str, Any]:
    return {
        str(DEFAULT_DECK_ID): {
            "id": DEFAULT_DECK_ID,
            "name": "Default",
            "mod": now_s,
            "usn": 0,
            "lrnToday": [0, 0],
            "revToday": [0, 0],
            "newToday": [0, 0],
            "timeToday": [0, 0],
            "collapsed": False,
            "browserCollapsed": False,
            "desc": "",
            "dyn": 0,
            "conf": DEFAULT_DCONF_ID,
            "extendNew": 10,
            "extendRev": 50,
        }
    }


def _basic_notetype_json(now_s: int) -> dict[str, Any]:
    return {
        "id": NOTETYPE_BASIC_ID,
        "name": "Basic",
        "type": 0,
        "mod": now_s,
        "usn": 0,
        "sortf": 0,
        "did": DEFAULT_DECK_ID,
        "tmpls": [
            {
                "name": "Card 1",
                "ord": 0,
                "qfmt": "{{Front}}",
                "afmt": '{{FrontSide}}\n\n<hr id="answer">\n\n{{Back}}',
                "did": None,
                "bqfmt": "",
                "bafmt": "",
            }
        ],
        "flds": [
            {"name": "Front", "ord": 0, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []},
            {"name": "Back", "ord": 1, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []},
        ],
        "css": ".card { font-family: arial; font-size: 20px; text-align: center; color: black; background-color: white; }",
        "latexPre": "",
        "latexPost": "",
        "req": [[0, "any", [0]]],
        "tags": [],
        "vers": [],
    }


def _basic_reverse_notetype_json(now_s: int) -> dict[str, Any]:
    return {
        "id": NOTETYPE_BASIC_REVERSE_ID,
        "name": "Basic (and reversed card)",
        "type": 0,
        "mod": now_s,
        "usn": 0,
        "sortf": 0,
        "did": DEFAULT_DECK_ID,
        "tmpls": [
            {
                "name": "Card 1",
                "ord": 0,
                "qfmt": "{{Front}}",
                "afmt": '{{FrontSide}}\n\n<hr id="answer">\n\n{{Back}}',
                "did": None,
                "bqfmt": "",
                "bafmt": "",
            },
            {
                "name": "Card 2",
                "ord": 1,
                "qfmt": "{{Back}}",
                "afmt": '{{FrontSide}}\n\n<hr id="answer">\n\n{{Front}}',
                "did": None,
                "bqfmt": "",
                "bafmt": "",
            },
        ],
        "flds": [
            {"name": "Front", "ord": 0, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []},
            {"name": "Back", "ord": 1, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []},
        ],
        "css": ".card { font-family: arial; font-size: 20px; text-align: center; color: black; background-color: white; }",
        "latexPre": "",
        "latexPost": "",
        "req": [[0, "any", [0]], [1, "any", [1]]],
        "tags": [],
        "vers": [],
    }


def _default_models_json(now_s: int) -> dict[str, Any]:
    return {
        str(NOTETYPE_BASIC_ID): _basic_notetype_json(now_s),
        str(NOTETYPE_BASIC_REVERSE_ID): _basic_reverse_notetype_json(now_s),
    }


# ── Bootstrap ────────────────────────────────────────────────────────


def is_bootstrapped(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='col'"
    ).fetchone()
    return row is not None


def bootstrap_collection() -> None:
    """Create `<data_dir>/anki/collection.anki2` if missing.

    Idempotent: bails out if the file already exists with a `col` row.
    """
    path = anki_db_path()
    anki_dir().mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        if is_bootstrapped(conn):
            log.info("anki collection already bootstrapped at %s", path)
            return

        log.info("bootstrapping anki collection at %s", path)
        conn.execute("BEGIN")
        try:
            for ddl in _DDL:
                conn.execute(ddl)

            now_ms = int(time.time() * 1000)
            now_s = int(time.time())
            crt_s = now_s - (now_s % 86400)  # midnight UTC of today

            # `col` row.
            conn.execute(
                """INSERT INTO col
                   (id, crt, mod, scm, ver, dty, usn, ls,
                    conf, models, decks, dconf, tags)
                   VALUES (1, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?, '{}')""",
                (
                    crt_s,
                    now_ms,
                    now_ms,
                    SCHEMA_VERSION,
                    json.dumps(_default_conf()),
                    json.dumps(_default_models_json(now_s)),
                    json.dumps(_default_decks_json(now_s)),
                    json.dumps(_default_dconf_json()),
                ),
            )

            # Schema-18 structured tables. We mirror the JSON above:
            # one default deck, one deck-config, two notetypes, four
            # fields (2 per notetype), three templates (1 + 2).
            #
            # The BLOB columns (`common`, `kind`, `config`) are protobuf
            # in real Anki — but the `col`-row JSON is enough for the
            # AnkiWeb upload path: the server re-derives the structured
            # tables from the JSON on its side. We populate the
            # structured tables with empty / minimal protobuf so reads
            # don't fail; AnkiWeb itself doesn't validate their content.
            empty = b""
            conn.execute(
                """INSERT INTO decks (id, name, mtime_secs, usn, common, kind)
                   VALUES (?, 'Default', ?, 0, ?, ?)""",
                (DEFAULT_DECK_ID, now_s, empty, empty),
            )
            conn.execute(
                """INSERT INTO deck_config (id, name, mtime_secs, usn, config)
                   VALUES (?, 'Default', ?, 0, ?)""",
                (DEFAULT_DCONF_ID, now_s, empty),
            )
            conn.execute(
                """INSERT INTO notetypes (id, name, mtime_secs, usn, config)
                   VALUES (?, 'Basic', ?, 0, ?)""",
                (NOTETYPE_BASIC_ID, now_s, empty),
            )
            conn.execute(
                """INSERT INTO notetypes (id, name, mtime_secs, usn, config)
                   VALUES (?, 'Basic (and reversed card)', ?, 0, ?)""",
                (NOTETYPE_BASIC_REVERSE_ID, now_s, empty),
            )
            for ntid in (NOTETYPE_BASIC_ID, NOTETYPE_BASIC_REVERSE_ID):
                conn.execute(
                    "INSERT INTO fields (ntid, ord, name, config) VALUES (?, 0, 'Front', ?)",
                    (ntid, empty),
                )
                conn.execute(
                    "INSERT INTO fields (ntid, ord, name, config) VALUES (?, 1, 'Back', ?)",
                    (ntid, empty),
                )
            conn.execute(
                """INSERT INTO templates (ntid, ord, name, mtime_secs, usn, config)
                   VALUES (?, 0, 'Card 1', ?, 0, ?)""",
                (NOTETYPE_BASIC_ID, now_s, empty),
            )
            conn.execute(
                """INSERT INTO templates (ntid, ord, name, mtime_secs, usn, config)
                   VALUES (?, 0, 'Card 1', ?, 0, ?)""",
                (NOTETYPE_BASIC_REVERSE_ID, now_s, empty),
            )
            conn.execute(
                """INSERT INTO templates (ntid, ord, name, mtime_secs, usn, config)
                   VALUES (?, 1, 'Card 2', ?, 0, ?)""",
                (NOTETYPE_BASIC_REVERSE_ID, now_s, empty),
            )

            conn.execute("INSERT INTO android_metadata (locale) VALUES ('en_US')")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

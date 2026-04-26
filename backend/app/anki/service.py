"""High-level Anki orchestration: bootstrap, sync upload, sync download.

The sync calls touch the on-disk `collection.anki2` (close any open
connections first; replace atomically on download). To avoid two
sync ops racing, we serialize them with a process-wide lock.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from dataclasses import dataclass

from app.anki.connection import anki_db_path, anki_dir
from app.anki.schema import bootstrap_collection, is_bootstrapped
from app.anki.sync import AnkiSyncError, SyncSession, download, host_key, upload
from app.config import get_settings
from app.db.connection import open_connection

log = logging.getLogger(__name__)


_sync_lock = asyncio.Lock()


@dataclass(slots=True)
class SyncStatus:
    last_sync_ms: int | None
    last_action: str | None
    last_error: str | None
    local_mod_ms: int | None
    enabled: bool


def ensure_collection() -> None:
    """Create the local collection.anki2 if missing; called at startup
    when anki.enabled is true."""
    settings = get_settings()
    if not settings.anki.enabled:
        return
    bootstrap_collection()


def sync_status() -> SyncStatus:
    settings = get_settings()
    enabled = settings.anki.enabled
    last_sync_ms: int | None = None
    last_action: str | None = None
    last_error: str | None = None
    local_mod_ms: int | None = None

    main = open_connection()
    try:
        row = main.execute(
            "SELECT last_sync_ms, last_action, last_error FROM anki_sync_state WHERE id = 1"
        ).fetchone()
        if row is not None:
            last_sync_ms = row["last_sync_ms"]
            last_action = row["last_action"]
            last_error = row["last_error"]
    finally:
        main.close()

    if anki_db_path().is_file():
        try:
            anki = sqlite3.connect(anki_db_path())
            try:
                r = anki.execute("SELECT mod FROM col WHERE id = 1").fetchone()
                if r is not None:
                    local_mod_ms = int(r[0])
            finally:
                anki.close()
        except sqlite3.Error:
            pass

    return SyncStatus(
        last_sync_ms=last_sync_ms,
        last_action=last_action,
        last_error=last_error,
        local_mod_ms=local_mod_ms,
        enabled=enabled,
    )


def _record_sync_outcome(action: str, error: str | None) -> None:
    main = open_connection()
    try:
        main.execute(
            """UPDATE anki_sync_state
               SET last_sync_ms = ?, last_action = ?, last_error = ?
               WHERE id = 1""",
            (int(time.time() * 1000), action, error),
        )
    finally:
        main.close()


def _ankiweb_session() -> SyncSession:
    settings = get_settings()
    if not settings.anki.enabled or settings.anki.ankiweb is None:
        raise AnkiSyncError(503, "anki sync is disabled in config")
    cfg = settings.anki.ankiweb
    return host_key(cfg.username, cfg.password, cfg.base_url)


async def sync_upload() -> None:
    """Full upload to AnkiWeb. Bootstraps the collection if missing."""
    async with _sync_lock:
        await asyncio.to_thread(_sync_upload_blocking)


def _sync_upload_blocking() -> None:
    settings = get_settings()
    if not settings.anki.enabled:
        raise AnkiSyncError(503, "anki sync is disabled in config")

    if not is_collection_present():
        bootstrap_collection()

    try:
        session = _ankiweb_session()
        with anki_db_path().open("rb") as f:
            data = f.read()
        upload(session, data)
        # On success, clear the unsynced marker locally.
        _clear_usn_after_sync()
        _record_sync_outcome("upload", None)
        log.info("anki sync: upload OK (%d bytes)", len(data))
    except AnkiSyncError as exc:
        _record_sync_outcome("upload", str(exc))
        log.warning("anki sync: upload failed: %s", exc)
        raise
    except Exception as exc:
        _record_sync_outcome("upload", str(exc))
        log.exception("anki sync: upload failed")
        raise AnkiSyncError(500, f"unexpected error: {exc}") from exc


async def sync_download() -> None:
    """Full download from AnkiWeb, replacing the local collection."""
    async with _sync_lock:
        await asyncio.to_thread(_sync_download_blocking)


def _sync_download_blocking() -> None:
    settings = get_settings()
    if not settings.anki.enabled:
        raise AnkiSyncError(503, "anki sync is disabled in config")

    try:
        session = _ankiweb_session()
        new_bytes = download(session)
        anki_dir().mkdir(parents=True, exist_ok=True)
        target = anki_db_path()
        tmp = target.with_suffix(target.suffix + ".tmp")
        with tmp.open("wb") as f:
            f.write(new_bytes)

        # Sanity-validate the file by opening it and reading `col.id`.
        try:
            v = sqlite3.connect(tmp)
            try:
                row = v.execute("SELECT id FROM col WHERE id = 1").fetchone()
                if row is None:
                    raise AnkiSyncError(500, "download: file has no col row")
            finally:
                v.close()
        except sqlite3.DatabaseError as exc:
            tmp.unlink(missing_ok=True)
            raise AnkiSyncError(500, f"download: invalid SQLite: {exc}") from exc

        # Replace atomically. WAL/SHM siblings are stale after a swap;
        # remove them so SQLite re-creates them on the next open.
        os.replace(tmp, target)
        for ext in ("-wal", "-shm"):
            sibling = target.with_name(target.name + ext)
            sibling.unlink(missing_ok=True)

        _record_sync_outcome("download", None)
        log.info("anki sync: download OK (%d bytes)", len(new_bytes))
    except AnkiSyncError as exc:
        _record_sync_outcome("download", str(exc))
        log.warning("anki sync: download failed: %s", exc)
        raise
    except Exception as exc:
        _record_sync_outcome("download", str(exc))
        log.exception("anki sync: download failed")
        raise AnkiSyncError(500, f"unexpected error: {exc}") from exc


def is_collection_present() -> bool:
    if not anki_db_path().is_file():
        return False
    try:
        conn = sqlite3.connect(anki_db_path())
        try:
            return is_bootstrapped(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _clear_usn_after_sync() -> None:
    """After a successful upload the server has accepted our state, so
    flip USN markers from -1 (unsynced) to 0 (synced).

    We don't bump `col.usn` itself because the server is the authority;
    on next download it'll come back synced anyway. But for cards/notes
    we keep our own state coherent so subsequent uploads don't claim
    "still unsynced" when nothing has changed.
    """
    conn = sqlite3.connect(anki_db_path(), isolation_level=None)
    try:
        conn.execute("BEGIN")
        try:
            conn.execute("UPDATE notes SET usn = 0 WHERE usn = -1")
            conn.execute("UPDATE cards SET usn = 0 WHERE usn = -1")
            conn.execute("UPDATE revlog SET usn = 0 WHERE usn = -1")
            conn.execute("UPDATE graves SET usn = 0 WHERE usn = -1")
            conn.execute("UPDATE decks SET usn = 0 WHERE usn = -1")
            conn.execute("UPDATE col SET usn = 0, ls = ? WHERE id = 1", (int(time.time()),))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


__all__ = [
    "SyncStatus",
    "ensure_collection",
    "is_collection_present",
    "sync_download",
    "sync_status",
    "sync_upload",
]

"""Plain-SQL migration runner.

Migrations live in `backend/migrations/NNNN_<slug>.sql`. The runner records
applied versions in a `schema_migrations` table and applies any not-yet-seen
files in order, each within a single transaction (with the version-row insert
included so the unit is atomic).

The splitter strips `--` line comments, then splits on `;`. It does NOT
understand SQL string literals or `BEGIN ... END` blocks — if a migration needs
a semicolon inside a string or a trigger body, put it in its own .sql file
containing only that one statement.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(r[0]) for r in rows}


def _list_files() -> list[tuple[int, Path]]:
    if not MIGRATIONS_DIR.is_dir():
        return []
    out: list[tuple[int, Path]] = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        head = p.name.split("_", 1)[0]
        if not head.isdigit():
            raise RuntimeError(f"migration filename must start with digits: {p.name}")
        out.append((int(head), p))
    return out


def _strip_line_comments(sql: str) -> str:
    out: list[str] = []
    for line in sql.splitlines():
        idx = line.find("--")
        if idx >= 0:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def _split_statements(sql: str) -> list[str]:
    cleaned = _strip_line_comments(sql)
    return [s.strip() for s in cleaned.split(";") if s.strip()]


def run_migrations(conn: sqlite3.Connection) -> int:
    """Apply pending migrations. Returns the number applied."""
    _ensure_table(conn)
    applied = _applied_versions(conn)
    pending = [(v, p) for v, p in _list_files() if v not in applied]

    for version, path in pending:
        log.info("Applying migration %04d %s", version, path.name)
        statements = _split_statements(path.read_text(encoding="utf-8"))
        conn.execute("BEGIN")
        try:
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, _utcnow_iso()),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            log.exception("migration %04d failed", version)
            raise

    return len(pending)

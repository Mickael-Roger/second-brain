"""Row dataclasses.

Plain frozen dataclasses constructed from `sqlite3.Row`. They are read-only
snapshots of a row at query time — there is no identity map. Mutations go
through SQL directly.

Datetimes round-trip as ISO-8601 strings on the wire; the `from_row` helpers
parse them into aware UTC `datetime` objects.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(frozen=True, slots=True)
class Chat:
    id: str
    title: str
    path: str
    module_id: str | None
    model: str | None
    created_at: datetime
    updated_at: datetime
    archived: bool

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Chat":
        return cls(
            id=row["id"],
            title=row["title"],
            path=row["path"],
            module_id=row["module_id"],
            model=row["model"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            archived=bool(row["archived"]),
        )


@dataclass(frozen=True, slots=True)
class SessionRow:
    id: str
    created_at: datetime
    expires_at: datetime
    user_agent: str | None
    ip: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SessionRow":
        return cls(
            id=row["id"],
            created_at=_parse_dt(row["created_at"]),
            expires_at=_parse_dt(row["expires_at"]),
            user_agent=row["user_agent"],
            ip=row["ip"],
        )


@dataclass(frozen=True, slots=True)
class ModuleState:
    module_id: str
    key: str
    value: str | None
    updated_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ModuleState":
        return cls(
            module_id=row["module_id"],
            key=row["key"],
            value=row["value"],
            updated_at=_parse_dt(row["updated_at"]),
        )


@dataclass(frozen=True, slots=True)
class Setting:
    key: str
    value: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Setting":
        return cls(key=row["key"], value=row["value"])

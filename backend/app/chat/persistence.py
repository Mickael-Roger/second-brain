"""Chat persistence: SQLite index + markdown file on disk.

Storage layout:
  - SQLite `chats` table is just an index (id, title, path, module_id, ...).
  - The full transcript lives in a markdown file under `chats_dir`. The path
    is stable for the chat's lifetime.

All datetimes are stored as ISO-8601 UTC text.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from ulid import ULID

from app.config import get_settings
from app.db.models import Chat
from app.llm.types import Message

from .markdown import parse_markdown, render_markdown, utcnow


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(s: str, max_len: int = 40) -> str:
    s = s.lower().strip()
    s = _SLUG_RE.sub("-", s).strip("-")
    return (s or "chat")[:max_len]


def _chats_root() -> Path:
    root = get_settings().chats_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def _file_path_for(chat_id: str, title: str, created_at: datetime) -> Path:
    """Path relative to chats_root."""
    year = f"{created_at.year:04d}"
    date = created_at.strftime("%Y-%m-%d")
    name = f"{date}-{_slugify(title)}-{chat_id[:8]}.md"
    return Path(year) / name


def _load_one(conn: sqlite3.Connection, chat_id: str) -> Chat | None:
    row = conn.execute(
        "SELECT id, title, path, module_id, model, created_at, updated_at, archived "
        "FROM chats WHERE id = ?",
        (chat_id,),
    ).fetchone()
    return Chat.from_row(row) if row is not None else None


def create_chat(
    conn: sqlite3.Connection,
    *,
    title: str = "New chat",
    module_id: str | None = None,
    model: str | None = None,
) -> Chat:
    chat_id = str(ULID())
    now = utcnow()
    rel = _file_path_for(chat_id, title, now)
    conn.execute(
        "INSERT INTO chats (id, title, path, module_id, model, created_at, updated_at, archived) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
        (chat_id, title, str(rel), module_id, model, now.isoformat(), now.isoformat()),
    )
    chat = Chat(
        id=chat_id,
        title=title,
        path=str(rel),
        module_id=module_id,
        model=model,
        created_at=now,
        updated_at=now,
        archived=False,
    )
    write_messages(chat, [])
    return chat


def list_chats(
    conn: sqlite3.Connection,
    *,
    module_id: str | None = None,
    archived: bool = False,
    limit: int = 200,
) -> list[Chat]:
    if module_id is None:
        rows = conn.execute(
            "SELECT id, title, path, module_id, model, created_at, updated_at, archived "
            "FROM chats WHERE archived = ? ORDER BY updated_at DESC LIMIT ?",
            (1 if archived else 0, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, title, path, module_id, model, created_at, updated_at, archived "
            "FROM chats WHERE archived = ? AND module_id = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (1 if archived else 0, module_id, limit),
        ).fetchall()
    return [Chat.from_row(r) for r in rows]


def get_chat(conn: sqlite3.Connection, chat_id: str) -> Chat | None:
    return _load_one(conn, chat_id)


def rename_chat(conn: sqlite3.Connection, chat: Chat, new_title: str) -> Chat:
    now = utcnow()
    conn.execute(
        "UPDATE chats SET title = ?, updated_at = ? WHERE id = ?",
        (new_title, now.isoformat(), chat.id),
    )
    # We deliberately do not rename the file: the path is stable once created
    # so links from elsewhere (Obsidian backlinks) don't break.
    refreshed = _load_one(conn, chat.id)
    assert refreshed is not None
    return refreshed


def set_archived(conn: sqlite3.Connection, chat: Chat, archived: bool) -> Chat:
    conn.execute(
        "UPDATE chats SET archived = ?, updated_at = ? WHERE id = ?",
        (1 if archived else 0, utcnow().isoformat(), chat.id),
    )
    refreshed = _load_one(conn, chat.id)
    assert refreshed is not None
    return refreshed


def delete_chat(conn: sqlite3.Connection, chat: Chat, *, hard: bool = False) -> None:
    """Soft-delete by default (archive flag). Hard delete removes the file."""
    if hard:
        path = _chats_root() / chat.path
        path.unlink(missing_ok=True)
        conn.execute("DELETE FROM chats WHERE id = ?", (chat.id,))
    else:
        conn.execute(
            "UPDATE chats SET archived = 1, updated_at = ? WHERE id = ?",
            (utcnow().isoformat(), chat.id),
        )


def write_messages(chat: Chat, messages: list[Message]) -> None:
    """Re-render the chat markdown file from a full message list."""
    text = render_markdown(
        chat_id=chat.id,
        title=chat.title,
        module_id=chat.module_id,
        model=chat.model,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        messages=messages,
    )
    abs_path = _chats_root() / chat.path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(text, encoding="utf-8")


def read_messages(chat: Chat) -> list[Message]:
    abs_path = _chats_root() / chat.path
    if not abs_path.is_file():
        return []
    _fm, messages = parse_markdown(abs_path.read_text(encoding="utf-8"))
    return messages


def touch(conn: sqlite3.Connection, chat_id: str) -> None:
    conn.execute(
        "UPDATE chats SET updated_at = ? WHERE id = ?",
        (utcnow().isoformat(), chat_id),
    )

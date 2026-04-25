"""Daily journal path resolution.

Today's note lives at the flat path `Journal/YYYY-MM-DD.md`. The nightly
archival job moves prior days into `Journal/YYYY/MM/YYYY-MM-DD.md`. This
helper resolves either form so reads work transparently.
"""

from __future__ import annotations

from datetime import date as _date
from pathlib import Path

from app.config import get_settings

from .paths import vault_root


def _flat_path(d: _date) -> Path:
    folder = get_settings().obsidian.journal.folder
    return Path(folder) / f"{d.isoformat()}.md"


def _archived_path(d: _date) -> Path:
    s = get_settings().obsidian.journal
    rendered = s.archive_template.format(
        folder=s.folder,
        year=d.year,
        month=d.month,
        date=d.isoformat(),
    )
    return Path(rendered)


def daily_relpath(d: _date | None = None) -> str:
    """Return the vault-relative path for a given day's note.

    For today, always returns the flat path (the file may not exist yet —
    the caller is responsible for creating it via vault.append).
    For past days, returns the archived path if it exists, otherwise flat.
    """
    if d is None:
        d = _date.today()

    flat = _flat_path(d)
    if d == _date.today():
        return flat.as_posix()

    abs_flat = vault_root() / flat
    if abs_flat.is_file():
        return flat.as_posix()
    return _archived_path(d).as_posix()


def journal_files() -> list[Path]:
    """All journal notes in the vault (flat + archived). Used by the nightly
    archival job to find files that should be moved.
    """
    folder = get_settings().obsidian.journal.folder
    base = vault_root() / folder
    if not base.is_dir():
        return []
    return [p for p in base.rglob("*.md") if p.is_file()]

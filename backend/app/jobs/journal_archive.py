"""Nightly journal-archival job.

Walks `Journal/` and moves any flat `YYYY-MM-DD.md` whose date is *before*
today into the archived `Journal/YYYY/MM/YYYY-MM-DD.md` shape. All moves run
inside one git transaction so the entire night is one commit.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date as _date

from app.config import get_settings
from app.vault import journal_files, vault_root
from app.vault.guard import GitConflictError, get_guard

log = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    moved: int
    skipped: int
    paths: list[str]      # archived destination paths (vault-relative)
    errors: list[str]     # human-readable error lines


def _candidates() -> list[tuple[_date, "object"]]:
    """Find flat journal files older than today.

    Returns (date, src_path). Files already inside an archived folder
    structure (Journal/YYYY/MM/) are ignored — they're already done.
    """
    s = get_settings().obsidian.journal
    root = vault_root()
    folder_abs = root / s.folder
    today = _date.today()

    out: list[tuple[_date, object]] = []
    for p in journal_files():
        # Only consider files directly under Journal/, not Journal/YYYY/MM/.
        if p.parent != folder_abs:
            continue
        m = _DATE_RE.match(p.name)
        if not m:
            continue
        try:
            d = _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if d >= today:
            continue
        out.append((d, p))
    return out


def _archive_dest(d: _date) -> str:
    s = get_settings().obsidian.journal
    return s.archive_template.format(
        folder=s.folder, year=d.year, month=d.month, date=d.isoformat()
    )


async def run_journal_archive() -> ArchiveResult:
    """Move stale flat-path journal notes into the archived structure.

    Runs inside one git transaction (one commit per night, regardless of
    how many files were moved).
    """
    candidates = _candidates()
    if not candidates:
        return ArchiveResult(moved=0, skipped=0, paths=[], errors=[])

    root = vault_root()
    moved: list[str] = []
    errors: list[str] = []

    try:
        async with get_guard().transaction(
            f"nightly journal archive ({len(candidates)} note(s))"
        ):
            for d, src_path in candidates:
                dst_rel = _archive_dest(d)
                dst_abs = root / dst_rel
                if dst_abs.exists():
                    errors.append(f"{src_path.name}: destination already exists ({dst_rel})")
                    continue
                try:
                    dst_abs.parent.mkdir(parents=True, exist_ok=True)
                    src_path.rename(dst_abs)
                    moved.append(dst_rel)
                except OSError as exc:
                    errors.append(f"{src_path.name}: {exc}")
    except GitConflictError as exc:
        errors.append(f"git conflict during archive: {exc}")
        log.exception("journal archive aborted on git conflict")

    return ArchiveResult(
        moved=len(moved),
        skipped=len(errors),
        paths=moved,
        errors=errors,
    )

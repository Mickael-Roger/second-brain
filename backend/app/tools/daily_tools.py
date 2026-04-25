"""daily.* — sugar for the user's daily journal.

Today's note lives at the flat `Journal/YYYY-MM-DD.md`; older days are
rewritten into the archived `Journal/YYYY/MM/YYYY-MM-DD.md` by the nightly
job. These tools resolve the right path either way.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Any

from app.vault import append_note, daily_relpath, read_note
from app.vault.guard import GitConflictError
from app.vault.paths import VaultPathError

from .registry import ToolRegistry, text_result


def _parse_date(value: Any) -> _date | None:
    if value is None or value == "":
        return None
    if isinstance(value, _date):
        return value
    return _date.fromisoformat(str(value))


async def _append(args: dict[str, Any]):
    text = args["text"]
    try:
        d = _parse_date(args.get("date"))
    except ValueError as exc:
        return text_result(f"invalid date: {exc}", is_error=True)

    rel = daily_relpath(d)
    try:
        n = await append_note(rel, text, message=f"daily.append {rel}")
    except (VaultPathError, FileNotFoundError) as exc:
        return text_result(str(exc), is_error=True)
    except GitConflictError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(f"appended to {n.path}")


async def _read(args: dict[str, Any]):
    try:
        d = _parse_date(args.get("date"))
    except ValueError as exc:
        return text_result(f"invalid date: {exc}", is_error=True)
    rel = daily_relpath(d)
    try:
        n = read_note(rel)
    except FileNotFoundError:
        return text_result(f"no journal entry for {rel}")
    except VaultPathError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(f"# {n.path}\n\n{n.content}")


def register_all(reg: ToolRegistry) -> None:
    reg.register(
        "daily.append",
        "Append a line/paragraph to the user's daily journal note. Defaults to today.",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "date": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD). Default: today.",
                },
            },
            "required": ["text"],
        },
        _append,
    )
    reg.register(
        "daily.read",
        "Read the user's daily journal for a given date. Default: today.",
        {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "ISO date (YYYY-MM-DD). Default: today."},
            },
        },
        _read,
    )

"""Vault context files — INDEX.md, USER.md, PREFERENCES.md.

These three files at the root of the vault tell the brain how to think:
  - INDEX.md       — the structural map of the vault (folders, conventions).
  - USER.md        — who the user is (profile, role, languages, …).
  - PREFERENCES.md — how the brain should operate (tone, capture rules, …).

They are owned by the user. The brain reads them every chat session and
prepends them to the system prompt. The brain may update them in response
to user instructions ("remember that I prefer concise replies") via the
normal vault.* tools — no special handling.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import get_settings

from .paths import VaultPathError
from .vault import read_note

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ContextFile:
    label: str
    path: str
    content: str


_LABELS = {
    "index": "INDEX.md — vault structure (the map of the user's brain)",
    "user": "USER.md — facts about the user",
    "preferences": "PREFERENCES.md — how the brain should operate",
}


def read_context_files() -> list[ContextFile]:
    """Return the available context files in display order. Missing files
    are silently skipped — the brain still works without them."""
    s = get_settings()
    if s.obsidian.vault_path is None:
        return []

    out: list[ContextFile] = []
    for kind, filename in (
        ("index", s.obsidian.index_file),
        ("user", s.obsidian.user_file),
        ("preferences", s.obsidian.preferences_file),
    ):
        if not filename:
            continue
        try:
            n = read_note(filename)
        except (FileNotFoundError, VaultPathError):
            continue
        except RuntimeError as exc:
            log.debug("context file %s not loaded: %s", filename, exc)
            continue
        out.append(ContextFile(label=_LABELS[kind], path=n.path, content=n.content))
    return out

"""Backlink computation.

Walks the vault on demand looking for `[[Note]]` and `[[Note|alias]]` links
that point at a given note. Cheap enough at our scale (a few thousand notes)
to do without a persistent index. Caching can come later if it ever shows up
in profiles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .paths import vault_root


_LINK_RE = re.compile(r"\[\[([^\]\|#]+?)(?:#[^\]\|]*)?(?:\|[^\]]*)?\]\]")


@dataclass(frozen=True, slots=True)
class Backlink:
    path: str       # vault-relative
    snippet: str


def _target_matches(target_token: str, target_rel: str) -> bool:
    """A wikilink token can be:
       - basename ("My Note")
       - relative path with or without .md ("Tech/My Note", "Tech/My Note.md")
    Resolve flexibly to the target.
    """
    target_path = Path(target_rel)
    target_stem = target_path.stem
    target_no_ext = target_path.with_suffix("").as_posix()
    return (
        target_token == target_stem
        or target_token == target_no_ext
        or target_token == target_rel
    )


def find_backlinks(target_rel: str, *, max_results: int = 200) -> list[Backlink]:
    root = vault_root()
    out: list[Backlink] = []
    for p in root.rglob("*.md"):
        rel_parts = p.relative_to(root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        rel = p.relative_to(root).as_posix()
        if rel == target_rel:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _LINK_RE.finditer(text):
            token = match.group(1).strip()
            if _target_matches(token, target_rel):
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.end())
                if line_end == -1:
                    line_end = len(text)
                snippet = text[line_start:line_end].strip()
                out.append(Backlink(path=rel, snippet=snippet[:200]))
                break
        if len(out) >= max_results:
            break
    return out

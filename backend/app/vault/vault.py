"""Vault primitives — the only way the rest of the codebase touches Obsidian.

All write operations go through `ObsidianGitGuard.transaction()` so every
mutation is paired with a `pull → mutate → commit → push` round-trip when
git is enabled. Reads do not take the lock — they're consistent enough for
our purposes (we trust that an in-flight write completes between reads).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .guard import get_guard
from .paths import resolve_vault_path, to_relative, vault_root

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NoteRead:
    path: str          # vault-relative
    content: str       # raw markdown (frontmatter still embedded)


# ── reads ─────────────────────────────────────────────────────────────


def read_note(path: str) -> NoteRead:
    abs_path = resolve_vault_path(path)
    if not abs_path.is_file():
        raise FileNotFoundError(f"note not found: {path}")
    return NoteRead(path=to_relative(abs_path), content=abs_path.read_text(encoding="utf-8"))


def list_tree(folder: str = "", *, glob: str = "**/*") -> list[dict]:
    """Return a flat list of {path, type, depth} entries under the folder.

    Hidden entries (starting with `.`) are skipped to keep `.git`,
    `.obsidian`, etc. out of the wiki view.
    """
    base = resolve_vault_path(folder) if folder else vault_root()
    if not base.is_dir():
        raise FileNotFoundError(f"folder not found: {folder!r}")

    out: list[dict] = []
    root = vault_root()
    for p in sorted(base.glob(glob)):
        rel_parts = p.relative_to(root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        out.append(
            {
                "path": p.relative_to(root).as_posix(),
                "type": "folder" if p.is_dir() else "file",
                "depth": len(rel_parts) - 1,
            }
        )
    return out


def search_vault(query: str, *, in_folder: str = "", limit: int = 200) -> list[dict]:
    """Full-text search via ripgrep, falling back to grep -r if rg is absent.

    Returns [{path, line_number, snippet}, …] — vault-relative paths.
    """
    if not query.strip():
        return []
    base = resolve_vault_path(in_folder) if in_folder else vault_root()

    rg = shutil.which("rg")
    if rg:
        cmd = [
            rg,
            "--no-heading",
            "--line-number",
            "--smart-case",
            "--max-count",
            "5",
            "--max-columns",
            "200",
            "--glob",
            "!.git/**",
            "--glob",
            "!.obsidian/**",
            "--",
            query,
            str(base),
        ]
    else:
        cmd = [
            "grep",
            "-rIn",
            "--exclude-dir=.git",
            "--exclude-dir=.obsidian",
            "--",
            query,
            str(base),
        ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        log.warning("vault.search failed: %s", proc.stderr.strip())
        return []

    out: list[dict] = []
    root = vault_root()
    for line in proc.stdout.splitlines():
        # rg / grep output: <path>:<line>:<text>
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        path_str, lineno_str, snippet = parts
        try:
            rel = Path(path_str).resolve().relative_to(root.resolve())
        except ValueError:
            continue
        try:
            lineno = int(lineno_str)
        except ValueError:
            continue
        out.append(
            {"path": rel.as_posix(), "line_number": lineno, "snippet": snippet.rstrip()}
        )
        if len(out) >= limit:
            break
    return out


# ── writes (all through the guard) ────────────────────────────────────


async def write_note(path: str, content: str, *, message: str | None = None) -> NoteRead:
    abs_path = resolve_vault_path(path)
    msg = message or f"vault.write {to_relative(abs_path)}"

    async with get_guard().transaction(msg):
        await asyncio.to_thread(_write_text, abs_path, content)

    return NoteRead(path=to_relative(abs_path), content=content)


async def append_note(path: str, content: str, *, message: str | None = None) -> NoteRead:
    abs_path = resolve_vault_path(path)
    msg = message or f"vault.append {to_relative(abs_path)}"

    async with get_guard().transaction(msg):
        existing = abs_path.read_text(encoding="utf-8") if abs_path.is_file() else ""
        sep = "" if existing.endswith("\n") or not existing else "\n"
        new_content = existing + sep + content
        await asyncio.to_thread(_write_text, abs_path, new_content)

    return NoteRead(path=to_relative(abs_path), content=new_content)


async def create_note(
    folder: str,
    title: str,
    body: str,
    *,
    frontmatter: dict | None = None,
    message: str | None = None,
) -> NoteRead:
    """Create a note at <folder>/<title>.md. Fails if it already exists."""
    rel = (Path(folder) / f"{title}.md").as_posix() if folder else f"{title}.md"
    abs_path = resolve_vault_path(rel)
    if abs_path.exists():
        raise FileExistsError(f"note already exists: {rel}")

    parts: list[str] = []
    if frontmatter:
        import yaml as _yaml

        parts.append("---")
        parts.append(_yaml.safe_dump(frontmatter, sort_keys=False).strip())
        parts.append("---")
        parts.append("")
    parts.append(body)
    content = "\n".join(parts)

    msg = message or f"vault.create_note {rel}"
    async with get_guard().transaction(msg):
        await asyncio.to_thread(_write_text, abs_path, content)
    return NoteRead(path=rel, content=content)


async def move_note(src: str, dst: str, *, message: str | None = None) -> NoteRead:
    abs_src = resolve_vault_path(src)
    abs_dst = resolve_vault_path(dst)
    if not abs_src.is_file():
        raise FileNotFoundError(f"source not found: {src}")
    if abs_dst.exists():
        raise FileExistsError(f"destination already exists: {dst}")

    msg = message or f"vault.move {to_relative(abs_src)} → {to_relative(abs_dst)}"
    async with get_guard().transaction(msg):
        await asyncio.to_thread(_move_file, abs_src, abs_dst)
    return NoteRead(path=to_relative(abs_dst), content=abs_dst.read_text(encoding="utf-8"))


async def delete_note(path: str, *, message: str | None = None) -> None:
    abs_path = resolve_vault_path(path)
    if not abs_path.is_file():
        raise FileNotFoundError(f"note not found: {path}")
    msg = message or f"vault.delete {to_relative(abs_path)}"
    async with get_guard().transaction(msg):
        await asyncio.to_thread(abs_path.unlink)


# ── filesystem helpers ────────────────────────────────────────────────


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _move_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)

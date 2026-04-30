"""Vault primitives — the only way the rest of the codebase touches Obsidian.

All write operations go through `ObsidianGitGuard.transaction()` so every
mutation is paired with a `pull → mutate → commit → push` round-trip when
git is enabled. Reads do not take the lock — they're consistent enough for
our purposes (we trust that an in-flight write completes between reads).
"""

from __future__ import annotations

import asyncio
import fnmatch
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


def find_notes(pattern: str, *, in_folder: str = "", limit: int = 50) -> list[str]:
    """Match notes by NAME (not content). Returns vault-relative paths.

    `pattern` rules:
      - if it contains `*`, `?`, or `[…]`, treated as a case-insensitive glob;
      - otherwise, a case-insensitive substring against the file's basename
        AND its relative path (so 'S3NS' finds 'Tech/S3NS Cheatsheet.md').
    """
    if not pattern.strip():
        return []
    base = resolve_vault_path(in_folder) if in_folder else vault_root()
    if not base.is_dir():
        if base.is_file():
            return []
        raise FileNotFoundError(f"folder not found: {in_folder!r}")

    has_glob = any(c in pattern for c in "*?[")
    needle = pattern.lower()
    root = vault_root()
    out: list[str] = []
    for p in base.rglob("*.md"):
        rel_parts = p.relative_to(root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        rel = p.relative_to(root).as_posix()
        name = p.name
        if has_glob:
            matched = fnmatch.fnmatchcase(name.lower(), needle) or fnmatch.fnmatchcase(
                rel.lower(), needle
            )
        else:
            matched = needle in name.lower() or needle in rel.lower()
        if matched:
            out.append(rel)
            if len(out) >= limit:
                break
    return out


def search_vault(query: str, *, in_path: str = "", limit: int = 200) -> list[dict]:
    """Full-text CONTENT search via ripgrep (falls back to grep -r).

    `in_path` may be a folder (recursive) or a single `.md` file. Empty =
    whole vault. Returns [{path, line_number, snippet}, …] with
    vault-relative paths.
    """
    if not query.strip():
        return []
    base = resolve_vault_path(in_path) if in_path else vault_root()

    rg = shutil.which("rg")
    if rg:
        cmd = [
            rg,
            "--no-heading",
            "--line-number",
            "--with-filename",          # force prefix even when target is a single file
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
            "-rHIn",                    # -H: always print filename
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


async def replace_in_note(
    path: str,
    find: str,
    replace: str,
    *,
    replace_all: bool = False,
    message: str | None = None,
) -> NoteRead:
    """Surgical text replacement in an existing note.

    By default the operation fails when `find` matches zero times (the LLM
    would silently no-op) or more than once (ambiguous — the caller should
    include enough context to make the match unique). Pass `replace_all=True`
    to replace every occurrence intentionally.
    """
    abs_path = resolve_vault_path(path)
    if not abs_path.is_file():
        raise FileNotFoundError(f"note not found: {path}")

    content = abs_path.read_text(encoding="utf-8")
    occurrences = content.count(find)
    if occurrences == 0:
        raise ValueError(f"`find` string not present in {path}")
    if occurrences > 1 and not replace_all:
        raise ValueError(
            f"`find` matches {occurrences} times in {path}; pass replace_all=true "
            "to substitute every occurrence, or include more context to make the "
            "match unique"
        )
    new_content = content.replace(find, replace)
    if new_content == content:
        # find == replace shortcut: don't touch mtime.
        return NoteRead(path=to_relative(abs_path), content=content)

    msg = message or f"vault.replace_in_note {to_relative(abs_path)}"
    async with get_guard().transaction(msg):
        await asyncio.to_thread(_write_text, abs_path, new_content)
    return NoteRead(path=to_relative(abs_path), content=new_content)


def _apply_patch_ops(content: str, ops: list[dict]) -> str:
    """Apply a list of line-based ops on `content` and return the new text.

    Ops reference lines in the ORIGINAL content (1-indexed). To keep that
    contract, ops are applied highest-line-first so already-applied ops
    don't shift the indices of the remaining ones.
    """
    lines = content.splitlines(keepends=True)
    n = len(lines)

    occupied: list[tuple[int, int, int]] = []  # (lo, hi, idx) for destructive ops
    normalized: list[tuple[int, int, str, dict]] = []

    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            raise ValueError(f"ops[{i}]: must be an object")
        kind = op.get("op")
        if kind in ("delete", "replace"):
            lo, hi = op.get("from"), op.get("to")
            if not isinstance(lo, int) or not isinstance(hi, int):
                raise ValueError(f"ops[{i}] {kind!r}: 'from' and 'to' must be integers")
            if lo < 1 or hi < lo or hi > n:
                raise ValueError(
                    f"ops[{i}] {kind!r}: from={lo} to={hi} out of range "
                    f"(note has {n} lines)"
                )
            if kind == "replace" and not isinstance(op.get("content", ""), str):
                raise ValueError(f"ops[{i}] 'replace': 'content' must be a string")
            for a, b, j in occupied:
                if not (hi < a or lo > b):
                    raise ValueError(
                        f"ops[{i}] {kind!r} range {lo}-{hi} overlaps "
                        f"ops[{j}] range {a}-{b}"
                    )
            occupied.append((lo, hi, i))
            normalized.append((hi, i, kind, op))
        elif kind == "insert":
            after = op.get("after")
            if not isinstance(after, int):
                raise ValueError(f"ops[{i}] 'insert': 'after' must be an integer")
            if after < 0 or after > n:
                raise ValueError(
                    f"ops[{i}] 'insert': after={after} out of range "
                    f"(note has {n} lines)"
                )
            if not isinstance(op.get("content"), str):
                raise ValueError(f"ops[{i}] 'insert': 'content' must be a string")
            normalized.append((after, i, kind, op))
        else:
            raise ValueError(f"ops[{i}]: unknown op {kind!r}")

    # Highest line first; within ties, later-listed op runs first.
    normalized.sort(key=lambda t: (-t[0], -t[1]))

    def _block(text: str) -> list[str]:
        if not text:
            return []
        block = text.splitlines(keepends=True)
        if not block[-1].endswith("\n"):
            block[-1] += "\n"
        return block

    for _, _, kind, op in normalized:
        if kind == "delete":
            del lines[op["from"] - 1 : op["to"]]
        elif kind == "replace":
            lines[op["from"] - 1 : op["to"]] = _block(op.get("content", ""))
        elif kind == "insert":
            pos = op["after"]
            lines[pos:pos] = _block(op["content"])

    # A line lacking '\n' can only legally be the last one — anything else
    # would glue two lines together when joined. Patch them up.
    for k in range(len(lines) - 1):
        if not lines[k].endswith("\n"):
            lines[k] += "\n"

    return "".join(lines)


async def patch_note(
    path: str,
    ops: list[dict],
    *,
    message: str | None = None,
) -> NoteRead:
    """Line-based surgical edit of an existing note.

    Each op is one of:
      - {"op": "delete", "from": L1, "to": L2}
      - {"op": "replace", "from": L1, "to": L2, "content": "..."}
      - {"op": "insert", "after": L, "content": "..."}

    Line numbers are 1-indexed against the ORIGINAL note (before any op is
    applied). `after=0` inserts at the very top. Destructive ranges may not
    overlap. The whole batch is applied atomically through the git guard.
    """
    abs_path = resolve_vault_path(path)
    if not abs_path.is_file():
        raise FileNotFoundError(f"note not found: {path}")
    if not isinstance(ops, list) or not ops:
        raise ValueError("`ops` must be a non-empty list")

    content = abs_path.read_text(encoding="utf-8")
    new_content = _apply_patch_ops(content, ops)
    if new_content == content:
        return NoteRead(path=to_relative(abs_path), content=content)

    msg = message or f"vault.patch {to_relative(abs_path)}"
    async with get_guard().transaction(msg):
        await asyncio.to_thread(_write_text, abs_path, new_content)
    return NoteRead(path=to_relative(abs_path), content=new_content)


async def update_frontmatter(
    path: str,
    updates: dict,
    *,
    message: str | None = None,
) -> NoteRead:
    """Merge changes into a note's YAML frontmatter, leaving the body alone.

    Each key in `updates` is set on the frontmatter; passing ``None`` as a
    value removes that key. Notes with no frontmatter yet get one added.
    """
    import frontmatter as _fm

    abs_path = resolve_vault_path(path)
    if not abs_path.is_file():
        raise FileNotFoundError(f"note not found: {path}")

    raw = abs_path.read_text(encoding="utf-8")
    post = _fm.loads(raw)
    for key, value in updates.items():
        if value is None:
            post.metadata.pop(key, None)
        else:
            post[key] = value
    new_content = _fm.dumps(post)
    if new_content == raw:
        return NoteRead(path=to_relative(abs_path), content=raw)

    msg = message or f"vault.update_frontmatter {to_relative(abs_path)}"
    async with get_guard().transaction(msg):
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


async def create_folder(path: str, *, message: str | None = None) -> str:
    """Create a folder under the vault root.

    Git doesn't track empty directories, so a tiny `.gitkeep` file is
    placed inside newly-created empty folders to make them persist across
    pulls. If the folder already exists with content, this is a no-op.
    """
    abs_path = resolve_vault_path(path)
    if abs_path.exists() and not abs_path.is_dir():
        raise FileExistsError(f"path exists but is a file: {path}")

    rel = to_relative(abs_path) if abs_path.exists() else Path(path).as_posix()

    if abs_path.is_dir() and any(abs_path.iterdir()):
        # Already exists with content — nothing to do.
        return rel

    msg = message or f"vault.create_folder {rel}"
    async with get_guard().transaction(msg):
        abs_path.mkdir(parents=True, exist_ok=True)
        gitkeep = abs_path / ".gitkeep"
        if not any(p for p in abs_path.iterdir() if p.name != ".gitkeep"):
            gitkeep.write_text("", encoding="utf-8")
    return to_relative(abs_path)


# ── filesystem helpers ────────────────────────────────────────────────


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _move_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)

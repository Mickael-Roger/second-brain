"""vault.* tool implementations — the LLM's primary surface.

Each handler returns a ToolResultBlock the orchestrator threads back into
the LLM as a tool_result. Errors become `is_error=True` results so the LLM
can recover instead of the whole turn failing.
"""

from __future__ import annotations

import json
from typing import Any

from app.vault import (
    append_note,
    create_note,
    delete_note,
    list_tree,
    move_note,
    read_note,
    search_vault,
    write_note,
)
from app.vault.guard import GitConflictError
from app.vault.paths import VaultPathError

from .registry import ToolRegistry, text_result


# ── handlers ─────────────────────────────────────────────────────────


async def _read(args: dict[str, Any]):
    try:
        n = read_note(args["path"])
    except FileNotFoundError as exc:
        return text_result(str(exc), is_error=True)
    except VaultPathError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(f"# {n.path}\n\n{n.content}")


async def _list(args: dict[str, Any]):
    folder = args.get("folder", "")
    glob = args.get("glob", "**/*")
    try:
        entries = list_tree(folder, glob=glob)
    except FileNotFoundError as exc:
        return text_result(str(exc), is_error=True)
    except VaultPathError as exc:
        return text_result(str(exc), is_error=True)
    if not entries:
        return text_result("(empty)")
    lines = [f"{e['type']:6} {e['path']}" for e in entries]
    return text_result("\n".join(lines))


async def _search(args: dict[str, Any]):
    q = args["query"]
    folder = args.get("folder", "")
    limit = int(args.get("limit", 30))
    try:
        hits = search_vault(q, in_folder=folder, limit=limit)
    except VaultPathError as exc:
        return text_result(str(exc), is_error=True)
    if not hits:
        return text_result("(no matches)")
    lines = [f"{h['path']}:{h['line_number']}: {h['snippet']}" for h in hits]
    return text_result("\n".join(lines))


async def _write(args: dict[str, Any]):
    try:
        n = await write_note(args["path"], args["content"])
    except (VaultPathError, FileNotFoundError) as exc:
        return text_result(str(exc), is_error=True)
    except GitConflictError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(f"wrote {n.path} ({len(n.content)} bytes)")


async def _append(args: dict[str, Any]):
    try:
        n = await append_note(args["path"], args["content"])
    except (VaultPathError, FileNotFoundError) as exc:
        return text_result(str(exc), is_error=True)
    except GitConflictError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(f"appended to {n.path} (now {len(n.content)} bytes)")


async def _create_note(args: dict[str, Any]):
    folder = args.get("folder", "")
    title = args["title"]
    body = args.get("body", "")
    fm = args.get("frontmatter")
    try:
        n = await create_note(folder, title, body, frontmatter=fm)
    except FileExistsError as exc:
        return text_result(str(exc), is_error=True)
    except (VaultPathError, FileNotFoundError) as exc:
        return text_result(str(exc), is_error=True)
    except GitConflictError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(f"created {n.path}")


async def _move(args: dict[str, Any]):
    try:
        n = await move_note(args["src"], args["dst"])
    except (VaultPathError, FileNotFoundError, FileExistsError) as exc:
        return text_result(str(exc), is_error=True)
    except GitConflictError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(f"moved → {n.path}")


async def _delete(args: dict[str, Any]):
    try:
        await delete_note(args["path"])
    except (VaultPathError, FileNotFoundError) as exc:
        return text_result(str(exc), is_error=True)
    except GitConflictError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(f"deleted {args['path']}")


# ── schemas + registration ───────────────────────────────────────────


_PATH = {"type": "string", "description": "Vault-relative path (e.g. 'Tech/RAG.md')."}


def register_all(reg: ToolRegistry) -> None:
    reg.register(
        "vault.read",
        "Read a note from the Obsidian vault. Returns the raw markdown including frontmatter.",
        {
            "type": "object",
            "properties": {"path": _PATH},
            "required": ["path"],
        },
        _read,
    )
    reg.register(
        "vault.list",
        "List vault entries under a folder. Hidden folders (.git, .obsidian) are excluded.",
        {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Folder to list (default: vault root).", "default": ""},
                "glob": {"type": "string", "description": "Glob pattern, default '**/*'.", "default": "**/*"},
            },
        },
        _list,
    )
    reg.register(
        "vault.search",
        "Full-text search across the vault (ripgrep). Returns up to `limit` matches.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "folder": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 30, "minimum": 1, "maximum": 200},
            },
            "required": ["query"],
        },
        _search,
    )
    reg.register(
        "vault.write",
        "Overwrite a note with the given content. Creates parent folders. Goes through git: pull → write → commit → push.",
        {
            "type": "object",
            "properties": {"path": _PATH, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        _write,
    )
    reg.register(
        "vault.append",
        "Append content to the end of a note. Creates the file if it doesn't exist.",
        {
            "type": "object",
            "properties": {"path": _PATH, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        _append,
    )
    reg.register(
        "vault.create_note",
        "Create a brand-new note. Fails if it already exists.",
        {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Vault-relative folder, '' for root."},
                "title": {"type": "string", "description": "Note title — used as filename without extension."},
                "body": {"type": "string", "default": ""},
                "frontmatter": {"type": "object", "description": "Optional YAML frontmatter (object form)."},
            },
            "required": ["folder", "title"],
        },
        _create_note,
    )
    reg.register(
        "vault.move",
        "Move/rename a note within the vault.",
        {
            "type": "object",
            "properties": {"src": _PATH, "dst": _PATH},
            "required": ["src", "dst"],
        },
        _move,
    )
    reg.register(
        "vault.delete",
        "Delete a note. Use with care — deletions are committed to git like any other write.",
        {
            "type": "object",
            "properties": {"path": _PATH},
            "required": ["path"],
        },
        _delete,
    )

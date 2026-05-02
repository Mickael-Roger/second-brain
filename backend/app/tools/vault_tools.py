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
    create_folder,
    create_note,
    delete_note,
    find_notes,
    list_tree,
    move_note,
    patch_note,
    read_note,
    replace_in_note,
    search_vault,
    update_frontmatter,
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


async def _find(args: dict[str, Any]):
    pattern = args["pattern"]
    folder = args.get("folder", "")
    limit = int(args.get("limit", 50))
    try:
        paths = find_notes(pattern, in_folder=folder, limit=limit)
    except (FileNotFoundError, VaultPathError) as exc:
        return text_result(str(exc), is_error=True)
    if not paths:
        return text_result("(no matching notes)")
    return text_result("\n".join(paths))


async def _grep(args: dict[str, Any]):
    q = args["query"]
    path = args.get("path", "")
    limit = int(args.get("limit", 30))
    try:
        hits = search_vault(q, in_path=path, limit=limit)
    except VaultPathError as exc:
        return text_result(str(exc), is_error=True)
    if not hits:
        return text_result("(no matches)")
    lines = [f"{h['path']}:{h['line_number']}: {h['snippet']}" for h in hits]
    return text_result("\n".join(lines))


async def _edit_note(args: dict[str, Any]):
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


async def _create_folder(args: dict[str, Any]):
    try:
        rel = await create_folder(args["path"])
    except FileExistsError as exc:
        return text_result(str(exc), is_error=True)
    except VaultPathError as exc:
        return text_result(str(exc), is_error=True)
    except GitConflictError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(f"created folder {rel}/")


async def _move(args: dict[str, Any]):
    try:
        n = await move_note(args["src"], args["dst"])
    except (VaultPathError, FileNotFoundError, FileExistsError) as exc:
        return text_result(str(exc), is_error=True)
    except GitConflictError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(f"moved → {n.path}")


async def _replace_in_note(args: dict[str, Any]):
    try:
        n = await replace_in_note(
            args["path"],
            args["find"],
            args["replace"],
            replace_all=bool(args.get("replace_all", False)),
        )
    except (VaultPathError, FileNotFoundError, ValueError) as exc:
        return text_result(str(exc), is_error=True)
    except GitConflictError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(f"replaced in {n.path} ({len(n.content)} bytes)")


async def _patch(args: dict[str, Any]):
    ops = args.get("ops")
    if not isinstance(ops, list):
        return text_result("`ops` must be a list of operations", is_error=True)
    try:
        n = await patch_note(args["path"], ops)
    except (VaultPathError, FileNotFoundError, ValueError) as exc:
        return text_result(str(exc), is_error=True)
    except GitConflictError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(f"patched {n.path} ({len(n.content)} bytes)")


async def _update_frontmatter(args: dict[str, Any]):
    updates = args.get("updates")
    if not isinstance(updates, dict):
        return text_result("`updates` must be a JSON object", is_error=True)
    try:
        n = await update_frontmatter(args["path"], updates)
    except (VaultPathError, FileNotFoundError) as exc:
        return text_result(str(exc), is_error=True)
    except GitConflictError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(f"updated frontmatter on {n.path}")


async def _delete(args: dict[str, Any]):
    try:
        await delete_note(args["path"])
    except (VaultPathError, FileNotFoundError) as exc:
        return text_result(str(exc), is_error=True)
    except GitConflictError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(f"deleted {args['path']}")


async def _trash(args: dict[str, Any]):
    src = str(args["path"]).strip().lstrip("/")
    if not src:
        return text_result("`path` is required.", is_error=True)
    if src.startswith("Trash/") or src == "Trash":
        return text_result(f"{src} is already in Trash/", is_error=True)
    dst = f"Trash/{src}"
    try:
        n = await move_note(src, dst, message=f"vault.trash {src}")
    except (VaultPathError, FileNotFoundError, FileExistsError) as exc:
        return text_result(str(exc), is_error=True)
    except GitConflictError as exc:
        return text_result(str(exc), is_error=True)
    return text_result(f"trashed → {n.path}")


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
        "vault.find",
        (
            "Find notes by FILENAME (not content). USE THIS FIRST when the user "
            "mentions a topic — there is often a dedicated note named for it. "
            "`pattern` is matched against the basename AND the relative path, "
            "case-insensitively. Glob characters `*` `?` `[…]` are supported "
            "(e.g. `S3NS*`, `*cheatsheet*`); without globs, plain substring "
            "match. Examples that match `Tech/S3NS Cheatsheet.md`: "
            "`S3NS`, `Cheatsheet`, `S3NS*`, `Tech/S3NS`."
        ),
        {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Filename pattern — substring or glob.",
                },
                "folder": {
                    "type": "string",
                    "description": "Restrict to a folder. Empty = whole vault.",
                    "default": "",
                },
                "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500},
            },
            "required": ["pattern"],
        },
        _find,
    )
    reg.register(
        "vault.grep",
        (
            "Search inside note CONTENT (ripgrep). Use this AFTER `vault.find` "
            "when a topic is likely scattered across notes rather than being "
            "its own page. Keep `query` short — single keywords or 2–3 words. "
            "Long sentences almost never match. `path` may be empty (whole "
            "vault), a folder, or a single .md file."
        ),
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Short keyword phrase. Smart-case matching.",
                },
                "path": {
                    "type": "string",
                    "description": "Vault-relative folder OR file path. Empty = whole vault.",
                    "default": "",
                },
                "limit": {"type": "integer", "default": 30, "minimum": 1, "maximum": 200},
            },
            "required": ["query"],
        },
        _grep,
    )
    reg.register(
        "vault.create_note",
        (
            "Create a NEW note. Use this to WRITE a fresh note. Fails if a "
            "note already exists at folder/title.md, so it's safe — it never "
            "clobbers existing content. Use `vault.edit_note` instead to "
            "modify an existing note."
        ),
        {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "description": "Vault-relative folder, '' for root.",
                },
                "title": {
                    "type": "string",
                    "description": "Note title — used as filename without the .md extension.",
                },
                "body": {"type": "string", "default": ""},
                "frontmatter": {
                    "type": "object",
                    "description": "Optional YAML frontmatter (object form).",
                },
            },
            "required": ["folder", "title"],
        },
        _create_note,
    )
    reg.register(
        "vault.edit_note",
        (
            "EDIT an existing note by overwriting it with the given content. "
            "Pass the FULL new body (including frontmatter if any). Creates "
            "the note if it doesn't exist. Goes through git: pull → write → "
            "commit → push. Use `vault.append` for incremental additions, "
            "or `vault.create_note` if you specifically want to fail when "
            "the note already exists."
        ),
        {
            "type": "object",
            "properties": {"path": _PATH, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        _edit_note,
    )
    reg.register(
        "vault.append",
        (
            "Append content to the end of an existing note. Creates the file "
            "if it doesn't exist (parent folders included). Cheaper and safer "
            "than `vault.edit_note` when you only want to add a paragraph."
        ),
        {
            "type": "object",
            "properties": {"path": _PATH, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        _append,
    )
    reg.register(
        "vault.create_folder",
        (
            "Create a folder under the vault root. Use this when the user "
            "asks for a new section or topic area that doesn't fit any "
            "existing folder. A `.gitkeep` placeholder is added so git "
            "tracks the empty directory. No-op if the folder already exists."
        ),
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Vault-relative folder path, e.g. 'Notes/Cooking'.",
                }
            },
            "required": ["path"],
        },
        _create_folder,
    )
    reg.register(
        "vault.replace_in_note",
        (
            "SURGICAL update — replace an exact substring inside an existing "
            "note. Cheaper than `vault.edit_note` for small fixes (typo, link "
            "rename, change one date) because you don't have to send the full "
            "body. The match is exact, NOT regex. Fails if `find` is not "
            "present, or if it matches more than once unless `replace_all` is "
            "true (in that case make sure `find` is unique enough, or include "
            "surrounding context). Use `vault.edit_note` for larger rewrites."
        ),
        {
            "type": "object",
            "properties": {
                "path": _PATH,
                "find": {"type": "string", "description": "Exact substring to look for."},
                "replace": {"type": "string", "description": "Replacement text."},
                "replace_all": {
                    "type": "boolean",
                    "default": False,
                    "description": "Replace every occurrence; default fails on >1 matches.",
                },
            },
            "required": ["path", "find", "replace"],
        },
        _replace_in_note,
    )
    reg.register(
        "vault.patch",
        (
            "Line-based surgical edit. Apply one or more ops on an existing "
            "note: DELETE a span of lines, REPLACE a span, or INSERT a block "
            "after a given line. Line numbers are 1-indexed and refer to the "
            "ORIGINAL note (before any op is applied) — call `vault.read` "
            "first to count them. `after=0` inserts at the very top. "
            "Destructive ranges (delete/replace) MUST NOT overlap each other; "
            "ops are applied highest-line-first so smaller line numbers stay "
            "stable across the batch. Use this when you know exact line "
            "positions. Use `vault.replace_in_note` instead when you only "
            "know the text to change, not its line. Use `vault.edit_note` "
            "for full rewrites."
        ),
        {
            "type": "object",
            "properties": {
                "path": _PATH,
                "ops": {
                    "type": "array",
                    "minItems": 1,
                    "description": "List of patch operations applied as one batch.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": ["delete", "replace", "insert"],
                            },
                            "from": {
                                "type": "integer",
                                "minimum": 1,
                                "description": "delete/replace: first line, 1-indexed (inclusive).",
                            },
                            "to": {
                                "type": "integer",
                                "minimum": 1,
                                "description": "delete/replace: last line, 1-indexed (inclusive).",
                            },
                            "after": {
                                "type": "integer",
                                "minimum": 0,
                                "description": "insert: insert AFTER this line; 0 means at the top.",
                            },
                            "content": {
                                "type": "string",
                                "description": "replace/insert: new text. Lines separated by \\n.",
                            },
                        },
                        "required": ["op"],
                    },
                },
            },
            "required": ["path", "ops"],
        },
        _patch,
    )
    reg.register(
        "vault.update_frontmatter",
        (
            "Merge changes into a note's YAML frontmatter without touching "
            "the body. `updates` is an object — each key replaces (or adds) "
            "that frontmatter field; pass null as the value to REMOVE a "
            "field. Notes without frontmatter get one added. Use this for "
            "tag/metadata edits without rewriting the whole note."
        ),
        {
            "type": "object",
            "properties": {
                "path": _PATH,
                "updates": {
                    "type": "object",
                    "description": (
                        "Object of frontmatter keys to merge. Lists/strings/"
                        "numbers/bools as values; null = delete the key."
                    ),
                },
            },
            "required": ["path", "updates"],
        },
        _update_frontmatter,
    )
    reg.register(
        "vault.move",
        (
            "Move or rename a note. Use this to MOVE A NOTE INTO ANOTHER "
            "FOLDER (e.g. promote an Inbox capture into Tech/) or to rename "
            "a file. Fails if the destination already exists."
        ),
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
    reg.register(
        "vault.trash",
        (
            "Soft-delete a note by moving it under Trash/ while keeping its "
            "original folder layout (e.g. 'Tech/RAG.md' → 'Trash/Tech/RAG.md'). "
            "Prefer this to `vault.delete` so the user can recover later. "
            "Fails if the note is already inside Trash/ or if a file with "
            "the same trashed path already exists."
        ),
        {
            "type": "object",
            "properties": {"path": _PATH},
            "required": ["path"],
        },
        _trash,
    )

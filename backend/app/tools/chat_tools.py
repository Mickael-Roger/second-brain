"""chat.search — full-text search across past conversation transcripts.

The transcripts live under `<data_dir>/chats/`, *not* the Obsidian vault.
Conversation isn't permanent knowledge — only what the user explicitly asks
to keep ends up in the vault. This tool lets the LLM look back at what was
said in earlier sessions when the user references prior context.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from app.config import get_settings

from .registry import ToolRegistry, text_result


async def _search(args: dict[str, Any]):
    q = str(args["query"]).strip()
    if not q:
        return text_result("(empty query)")
    limit = int(args.get("limit", 20))

    chats_dir = get_settings().chats_dir
    if not chats_dir.is_dir():
        return text_result("(no chat history yet)")

    rg = shutil.which("rg")
    if rg:
        cmd = [
            rg, "--no-heading", "--line-number", "--smart-case",
            "--max-count", "3", "--max-columns", "200",
            "--", q, str(chats_dir),
        ]
    else:
        cmd = ["grep", "-rIn", "--", q, str(chats_dir)]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        return text_result(f"search failed: {proc.stderr.strip()}", is_error=True)

    lines = proc.stdout.splitlines()[:limit]
    if not lines:
        return text_result("(no matches in chat history)")
    # Trim absolute paths down to relative-to-chats_dir for readability.
    out: list[str] = []
    for ln in lines:
        if ln.startswith(str(chats_dir)):
            ln = ln[len(str(chats_dir)) + 1 :]
        out.append(ln)
    return text_result("\n".join(out))


def register_all(reg: ToolRegistry) -> None:
    reg.register(
        "chat.search",
        "Search past chat transcripts for matches to a query. "
        "Useful when the user references something said earlier.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
        },
        _search,
    )

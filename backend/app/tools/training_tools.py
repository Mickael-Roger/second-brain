"""training.* — tools used by the Training kickoff chat.

The kickoff conversation runs with a restricted toolset (only
``training.finalize_kickoff``) so the LLM can't drift off course or
write the fiche itself. The orchestrator gates this via
``ChatRequest.system_prompt_id``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.training import expand_concept
from app.vault.guard import batch_session, commit_and_push, get_guard
from app.vault.paths import resolve_vault_path

from .registry import ToolRegistry, text_result

log = logging.getLogger(__name__)


def _slug_theme(name: str) -> str:
    """Folder-friendly theme slug. Keeps Title-Case-ish display by
    stripping path-unsafe chars and collapsing whitespace to a single
    space — folder names with spaces are fine on every supported OS
    and look better in the Wiki tree than dashed slugs."""
    s = re.sub(r"[\\/:\*\?\"<>\|]", "", (name or "").strip())
    s = re.sub(r"\s+", " ", s).strip()
    return s[:80]


def _expectations_frontmatter(theme: str) -> str:
    return (
        "---\n"
        "type: training-expectations\n"
        f'theme: "{theme}"\n'
        f"created: {datetime.now(timezone.utc).date().isoformat()}\n"
        "---\n\n"
    )


async def _finalize_kickoff(args: dict[str, Any]):
    raw_name = str(args.get("theme_name", "")).strip()
    raw_md = str(args.get("expectations_md", "")).strip()
    if not raw_name:
        return text_result("theme_name is required", is_error=True)
    if not raw_md:
        return text_result("expectations_md is required", is_error=True)

    theme = _slug_theme(raw_name)
    if not theme:
        return text_result(
            "theme_name resolved to an empty slug — pick a non-empty, "
            "path-safe name",
            is_error=True,
        )

    settings = get_settings()
    if settings.obsidian.vault_path is None:
        return text_result("vault is not configured", is_error=True)

    folder_rel = f"{settings.obsidian.training_folder.strip('/')}/{theme}"
    expectations_rel = f"{folder_rel}/Expectations.md"
    index_rel = f"{folder_rel}/Index.md"

    # Don't clobber an existing theme — surface it back so the LLM can
    # pick a different name (or the user can be told).
    if Path(resolve_vault_path(index_rel)).exists():
        return text_result(
            f"a theme already exists at {folder_rel} — pick a different "
            "theme_name (suggest a variant)",
            is_error=True,
        )

    # Write Expectations.md under a batch session so the file lands on
    # disk before expand_concept runs (it'll start a fresh batch).
    await get_guard().pre_flight()
    async with batch_session():
        abs_target = resolve_vault_path(expectations_rel)
        abs_target.parent.mkdir(parents=True, exist_ok=True)
        abs_target.write_text(_expectations_frontmatter(theme) + raw_md, encoding="utf-8")
    commit_and_push(f"training: kickoff expectations for {theme}")

    # Trigger the overview generation, calibrated to the expectations.
    try:
        result = await expand_concept(
            target_concept=theme,
            parent_path=None,
            theme=theme,
            extra_context=raw_md,
        )
    except Exception as exc:  # surfaces back to the LLM, which will stop.
        log.exception("training kickoff: expand_concept failed")
        return text_result(
            f"expectations saved at {expectations_rel}, but Index.md "
            f"generation failed: {exc!s}",
            is_error=True,
        )

    payload = {
        "theme": theme,
        "expectations_path": expectations_rel,
        "index_path": result.path,
    }
    return text_result(json.dumps(payload, ensure_ascii=False))


def register_all(reg: ToolRegistry) -> None:
    reg.register(
        "training.finalize_kickoff",
        "Finalize the Training kickoff conversation. Writes "
        "Training/<theme_name>/Expectations.md from the supplied "
        "markdown, then triggers generation of the theme's Index.md "
        "(the 'vue d'avion' overview) calibrated to those "
        "expectations. Call this ONCE when you have a clear picture "
        "of what the user wants — it ends the kickoff. Returns "
        "{theme, expectations_path, index_path} on success; on error "
        "(e.g. theme already exists) the LLM should suggest a "
        "different theme_name and try again.",
        {
            "type": "object",
            "properties": {
                "theme_name": {
                    "type": "string",
                    "description": (
                        "Short clean folder name for the theme "
                        "(Title Case, ~1–4 words, no slashes, no "
                        ".md extension). Becomes the folder under "
                        "Training/."
                    ),
                },
                "expectations_md": {
                    "type": "string",
                    "description": (
                        "Full markdown body of Expectations.md "
                        "(without frontmatter — the backend adds it). "
                        "Capture scope, level, angle, what 'done' "
                        "looks like, constraints. Faithful to what "
                        "the user actually said."
                    ),
                },
            },
            "required": ["theme_name", "expectations_md"],
            "additionalProperties": False,
        },
        _finalize_kickoff,
    )

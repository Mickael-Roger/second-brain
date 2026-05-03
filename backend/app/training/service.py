"""Core training service: expand a concept into a Markdown fiche.

The endpoint at ``/api/training/expand`` calls into ``expand_concept``.
The flow:

  1. Resolve the breadcrumb (parent fiche + theme index when distinct).
  2. Pick the LLM provider/model + capability flags from
     ``llm.tasks.training``.
  3. Run a small agent loop with one tool: ``image.generate``. Web
     search is honored as a flag the system prompt is told about (the
     adapter-level wiring is a TODO — see comment below).
  4. The LLM's final assistant text is the fiche markdown. We strip
     any accidental ``` fences, derive a title + slug, and create the
     note via ``vault.create_note`` (single git commit).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.config import LLMTaskConfig, get_settings
from app.llm import (
    Message,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
    get_llm_router,
)
from app.vault import read_note
from app.vault.guard import batch_session, commit_and_push, get_guard
from app.vault.paths import VaultPathError, resolve_vault_path

from .image_gen import (
    ImageGenerationError,
    asset_relpath,
    generate_image_bytes,
    write_image_bytes,
)
from .prompts import build_system_prompt

log = logging.getLogger(__name__)


_MAX_AGENT_ROUNDS = 8  # plenty for a fiche + a few illustrations
_MAX_BREADCRUMB_CHARS = 8_000  # cap parent / theme content sent as context


class TrainingExpandError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TrainingExpandResult:
    path: str            # vault-relative path of the new fiche
    theme: str
    parent_path: str | None


# ── helpers ──────────────────────────────────────────────────────────


_FENCE_RE = re.compile(r"^```(?:markdown|md)?\s*\n(.*)\n```\s*$", re.DOTALL)
_TITLE_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def _slugify(text: str) -> str:
    """Filename-safe basename for a fiche.

    Spaces and casing are preserved on purpose: the parent fiche's
    ``[[Concept]]`` wikilinks resolve by exact basename match, so
    a fiche generated from ``[[Image Classification]]`` MUST land at
    ``Image Classification.md`` — collapsing spaces to dashes would
    leave the wikilink dead even after the file was created."""
    s = re.sub(r"[\\/:\*\?\"<>\|]", "", (text or "").strip())
    s = re.sub(r"\s+", " ", s).strip()
    return s[:80] or "fiche"


def _strip_outer_fence(md: str) -> str:
    """Some models wrap their entire reply in a ```markdown fence.
    Unwrap once when the WHOLE message is a single fenced block."""
    m = _FENCE_RE.match(md.strip())
    return m.group(1) if m else md


def _extract_title(md: str, fallback: str) -> str:
    m = _TITLE_RE.search(md)
    return m.group(1).strip() if m else fallback


def _theme_from_path(path: str) -> str:
    """Derive the theme name from a vault-relative path.

    ``Training/<Theme>/...`` → ``<Theme>``. The theme folder lives one
    level under the configured training folder.
    """
    s = get_settings()
    root = s.obsidian.training_folder.strip("/").strip()
    rel = path.lstrip("/")
    if not rel.startswith(f"{root}/"):
        raise TrainingExpandError(
            f"path {path!r} is not under the training folder {root!r}"
        )
    after = rel[len(root) + 1 :]
    parts = after.split("/")
    if not parts or not parts[0]:
        raise TrainingExpandError(f"cannot derive theme from path {path!r}")
    return parts[0]


def _theme_index_path(theme: str) -> str:
    s = get_settings()
    root = s.obsidian.training_folder.strip("/")
    return f"{root}/{theme}/Index.md"


def _safe_read(rel_path: str) -> str | None:
    try:
        return read_note(rel_path).content
    except (FileNotFoundError, VaultPathError):
        return None
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("training: read failed for %s: %s", rel_path, exc)
        return None


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n\n[…truncated]"


def _build_user_message(
    *,
    target_concept: str,
    theme: str,
    parent_rel: str | None,
    parent_content: str | None,
    theme_index_content: str | None,
    web_search_requested: bool,
    extra_context: str | None = None,
) -> str:
    parts: list[str] = []
    parts.append(
        f"Generate a Training fiche on the concept: **{target_concept}**.\n\n"
        f"Theme: **{theme}**."
    )
    if parent_rel:
        parts.append(
            f"Parent fiche (the user just clicked an `[[{target_concept}]]` "
            f"wikilink from): `{parent_rel}`."
        )
    if web_search_requested:
        parts.append(
            "The user asked you to use web search if your provider exposes it "
            "natively — anchor recent or precise facts to sources, and list "
            "them under the `sources:` frontmatter field."
        )
    parts.append(
        "When the fiche is ready, write it as your final assistant text. "
        "Stop calling tools after that."
    )
    if parent_content:
        parts.append(
            "## Parent fiche content\n\n"
            "```markdown\n" + _truncate(parent_content, _MAX_BREADCRUMB_CHARS) + "\n```"
        )
    if theme_index_content and theme_index_content != parent_content:
        parts.append(
            "## Theme index\n\n"
            "```markdown\n" + _truncate(theme_index_content, _MAX_BREADCRUMB_CHARS) + "\n```"
        )
    if extra_context:
        parts.append(
            "## Stated expectations\n\n"
            "The user already clarified the scope, depth and goals of this "
            "theme during a kickoff conversation. Calibrate the overview to "
            "those expectations — choose the angle, the level of abstraction "
            "and the `À explorer` sub-concepts so they MATCH what the user "
            "asked for, not a generic intro.\n\n"
            "```markdown\n" + _truncate(extra_context, _MAX_BREADCRUMB_CHARS) + "\n```"
        )
    return "\n\n".join(parts)


# ── tool: image.generate ─────────────────────────────────────────────


def _build_image_tool(theme: str, task: LLMTaskConfig) -> ToolDef:
    return ToolDef(
        name="image.generate",
        description=(
            "Generate ONE illustration for this fiche when a visual genuinely "
            "helps comprehension and Mermaid wouldn't work (concept metaphor, "
            "anatomy, art reference, visual subject). Returns the "
            "vault-relative path to embed: ![alt text](./<returned path>). "
            "Use sparingly — never for technical diagrams (Mermaid is "
            "better) or formulas (LaTeX is better)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "English prompt describing the illustration in detail.",
                },
                "alt": {
                    "type": "string",
                    "description": "Short alt-text describing what the image shows.",
                },
            },
            "required": ["prompt", "alt"],
            "additionalProperties": False,
        },
    )


async def _handle_image_tool(
    args: dict[str, Any],
    *,
    theme: str,
    task: LLMTaskConfig,
    written_assets: list[str],
) -> ToolResultBlock:
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return ToolResultBlock(
            tool_use_id="(unset)",
            content=[TextBlock(text="image.generate: empty prompt")],
            is_error=True,
        )
    if not task.image_provider:
        return ToolResultBlock(
            tool_use_id="(unset)",
            content=[TextBlock(text=(
                "image.generate is not configured for the training task "
                "(set llm.tasks.training.image_provider in config). "
                "Continue with Mermaid / LaTeX only."
            ))],
            is_error=True,
        )
    try:
        data = await generate_image_bytes(prompt, task=task)
    except ImageGenerationError as exc:
        return ToolResultBlock(
            tool_use_id="(unset)",
            content=[TextBlock(text=f"image.generate failed: {exc}")],
            is_error=True,
        )

    rel = asset_relpath(theme, prompt)
    write_image_bytes(rel, data)
    written_assets.append(rel)
    return ToolResultBlock(
        tool_use_id="(unset)",
        content=[TextBlock(text=rel)],
        is_error=False,
    )


# ── main entry ───────────────────────────────────────────────────────


async def expand_concept(
    *,
    target_concept: str,
    parent_path: str | None,
    theme: str | None = None,
    web_search: bool = False,
    language: str | None = None,
    extra_context: str | None = None,
) -> TrainingExpandResult:
    """Generate a fiche for ``target_concept`` and write it to the vault.

    ``parent_path`` is the vault-relative path of the fiche the user
    clicked from. When None, the fiche is treated as a theme root and
    ``theme`` MUST be supplied — the resulting Index.md lives under
    ``<training_folder>/<theme>/Index.md``.

    ``extra_context`` is free-form markdown injected into the user
    message under a ``## Stated expectations`` heading. Used by the
    kickoff flow to pass the clarified scope/goals into the overview
    fiche generation so the LLM calibrates the angle accordingly.
    """
    target_concept = (target_concept or "").strip()
    if not target_concept:
        raise TrainingExpandError("target_concept is required")

    settings = get_settings()
    if settings.obsidian.vault_path is None:
        raise TrainingExpandError("obsidian.vault_path is not configured")

    # Resolve theme (from explicit arg or from parent's path).
    if theme is None and parent_path:
        theme = _theme_from_path(parent_path)
    theme = (theme or "").strip()
    if not theme:
        raise TrainingExpandError("could not resolve theme — pass it explicitly or via parent_path")

    # Resolve the LLM provider/model + capabilities.
    provider_name, model, task = settings.llm.resolve_task("training")
    if web_search and not task.web_search:
        log.info(
            "training: web_search requested but llm.tasks.training.web_search is "
            "false — generation will fall back to model knowledge only"
        )
    # NOTE: native web-search wiring depends on the provider adapter
    # exposing a `web_search` extra-tool surface. For now the flag is
    # advertised in the user message so the model can mention sources
    # it's confident about, but no extra adapter capability is plumbed
    # through.

    router = get_llm_router()
    provider = router.get(provider_name)

    # Read breadcrumb for context (best-effort).
    parent_content = _safe_read(parent_path) if parent_path else None
    theme_index_rel = _theme_index_path(theme)
    theme_index_content = _safe_read(theme_index_rel)
    if parent_path == theme_index_rel:
        theme_index_content = None  # avoid duplicating the same content

    # Compose system + user.
    lang = language or settings.app.language
    system = build_system_prompt(lang)
    user_text = _build_user_message(
        target_concept=target_concept,
        theme=theme,
        parent_rel=parent_path,
        parent_content=parent_content,
        theme_index_content=theme_index_content,
        web_search_requested=web_search and bool(task.web_search),
        extra_context=extra_context,
    )

    history: list[Message] = [Message(role="user", content=[TextBlock(text=user_text)])]
    image_tool = _build_image_tool(theme, task)
    tools = [image_tool] if task.image_provider else []
    written_assets: list[str] = []

    # Pre-flight pulls + commits any external edits so the batch starts
    # from a clean baseline. Then the whole generation (image gen +
    # fiche write) runs under batch_session() so we end with ONE commit
    # bundling assets + folder + fiche.
    await get_guard().pre_flight()
    final_text: str | None = None
    note_rel: str | None = None

    async with batch_session():
        rounds_left = _MAX_AGENT_ROUNDS
        while True:
            rounds_left -= 1
            if rounds_left < 0:
                raise TrainingExpandError(
                    f"agent exceeded {_MAX_AGENT_ROUNDS} rounds without producing a fiche"
                )

            assistant_message: Message | None = None
            async for ev in provider.stream(
                messages=history,
                tools=tools,
                system=system,
                model=model,
            ):
                if ev.type == "error":
                    raise TrainingExpandError(ev.error or "LLM stream error")
                if ev.type == "message_done" and ev.message:
                    assistant_message = ev.message

            if assistant_message is None:
                raise TrainingExpandError("LLM produced no assistant message")
            history.append(assistant_message)

            pending = [b for b in assistant_message.content if isinstance(b, ToolUseBlock)]
            if not pending:
                text = "".join(
                    b.text for b in assistant_message.content if isinstance(b, TextBlock)
                ).strip()
                if not text:
                    raise TrainingExpandError("LLM produced an empty fiche")
                final_text = text
                break

            results: list[ToolResultBlock] = []
            for call in pending:
                if call.name == "image.generate":
                    res = await _handle_image_tool(
                        call.input, theme=theme, task=task, written_assets=written_assets,
                    )
                else:
                    res = ToolResultBlock(
                        tool_use_id="(unset)",
                        content=[TextBlock(text=f"unknown tool: {call.name}")],
                        is_error=True,
                    )
                res = ToolResultBlock(
                    tool_use_id=call.id, content=res.content, is_error=res.is_error
                )
                results.append(res)
            history.append(Message(role="user", content=list(results)))

        # Materialise the fiche on disk (still under batch_session so no
        # per-write commit fires).
        body = _strip_outer_fence(final_text or "").strip()
        folder_rel = f"{settings.obsidian.training_folder.strip('/')}/{theme}"
        # Theme-root call (no parent_path) produces the canonical
        # ``Index.md`` for the theme; child concepts are slugged.
        if parent_path is None:
            candidate = f"{folder_rel}/Index.md"
        else:
            slug = _slugify(target_concept)
            candidate = f"{folder_rel}/{slug}.md"

        # Avoid clobbering: if the file exists, append a date suffix.
        abs_path = resolve_vault_path(candidate)
        if abs_path.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            stem = candidate[:-3]  # strip .md
            candidate = f"{stem}-{stamp}.md"
        note_rel = candidate

        abs_target = resolve_vault_path(note_rel)
        abs_target.parent.mkdir(parents=True, exist_ok=True)
        abs_target.write_text(body, encoding="utf-8")

    # Single bulk commit covering folder + assets + fiche.
    commit_and_push(f"training: generate {note_rel}")

    log.info(
        "training: wrote %s (theme=%s, parent=%s, assets=%d)",
        note_rel, theme, parent_path, len(written_assets),
    )
    return TrainingExpandResult(path=note_rel, theme=theme, parent_path=parent_path)

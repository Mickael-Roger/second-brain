"""HTTP endpoint for the on-demand Training fiche generator.

The wiki view calls ``POST /api/training/expand`` whenever the user
clicks a dead wikilink under the configured ``training_folder``. The
endpoint resolves the parent fiche (when given), runs the LLM-driven
fiche generator, and returns the vault-relative path of the new note
so the wiki can navigate straight to it.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import current_user
from app.config import get_settings
from app.training import TrainingExpandError, expand_concept
from app.vault import read_note, vault_root
from app.vault.guard import GitConflictError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/training", tags=["training"])


class ExpandRequest(BaseModel):
    target_concept: str = Field(min_length=1, max_length=200)
    parent_path: str | None = None
    theme: str | None = None
    web_search: bool = False
    language: str | None = None


class ExpandResponse(BaseModel):
    path: str
    theme: str
    parent_path: str | None


class TrainingConfigResponse(BaseModel):
    training_folder: str  # vault-relative, no trailing slash
    image_generation_enabled: bool


class ThemeSummary(BaseModel):
    theme: str
    index_path: str
    overview: str
    fiche_count: int
    updated_at: str  # ISO-8601 UTC


class ThemeListResponse(BaseModel):
    training_folder: str
    themes: list[ThemeSummary]


@router.get("/config", response_model=TrainingConfigResponse)
def get_config(_user: str = Depends(current_user)) -> TrainingConfigResponse:
    """Public training config the SPA needs to know about (which folder
    counts as the training subtree, whether image generation is wired)."""
    s = get_settings()
    task = s.llm.tasks.get("training")
    return TrainingConfigResponse(
        training_folder=s.obsidian.training_folder.strip("/"),
        image_generation_enabled=bool(task and task.image_provider),
    )


_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


def _extract_overview(md: str, max_len: int = 280) -> str:
    """Pull a one-paragraph blurb out of an Index.md: skip frontmatter
    and the leading H1, then take the first non-heading paragraph."""
    body = _FRONTMATTER_RE.sub("", md, count=1).lstrip()
    lines = body.splitlines()
    i = 0
    # Skip leading blank lines + an optional H1.
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith("# "):
        i += 1
    paragraph: list[str] = []
    for line in lines[i:]:
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith("#"):
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    text = " ".join(paragraph).strip()
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


@router.get("/themes", response_model=ThemeListResponse)
def get_themes(_user: str = Depends(current_user)) -> ThemeListResponse:
    """List training themes — one per sub-folder of ``training_folder``
    that contains an ``Index.md``. Sorted by latest activity."""
    s = get_settings()
    if s.obsidian.vault_path is None:
        raise HTTPException(status_code=503, detail="vault not configured")
    folder = s.obsidian.training_folder.strip("/")
    base = vault_root() / folder
    if not base.is_dir():
        return ThemeListResponse(training_folder=folder, themes=[])

    items: list[ThemeSummary] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        index_rel = f"{folder}/{entry.name}/Index.md"
        try:
            index_content = read_note(index_rel).content
        except FileNotFoundError:
            continue  # only surface themes that have been seeded with an Index
        fiche_count = 0
        latest_mtime = entry.stat().st_mtime
        for p in entry.rglob("*"):
            rel_parts = p.relative_to(entry).parts
            if any(part.startswith(".") for part in rel_parts):
                continue
            if p.is_file():
                latest_mtime = max(latest_mtime, p.stat().st_mtime)
                if p.suffix == ".md" and p.name != "Index.md":
                    fiche_count += 1
        items.append(
            ThemeSummary(
                theme=entry.name,
                index_path=index_rel,
                overview=_extract_overview(index_content),
                fiche_count=fiche_count,
                updated_at=datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat(),
            )
        )
    items.sort(key=lambda t: t.updated_at, reverse=True)
    return ThemeListResponse(training_folder=folder, themes=items)


@router.post("/expand", response_model=ExpandResponse)
async def post_expand(
    payload: ExpandRequest,
    _user: str = Depends(current_user),
) -> ExpandResponse:
    try:
        result = await expand_concept(
            target_concept=payload.target_concept,
            parent_path=payload.parent_path,
            theme=payload.theme,
            web_search=payload.web_search,
            language=payload.language,
        )
    except TrainingExpandError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GitConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        # Vault not configured, image provider missing, etc.
        log.exception("training expand failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ExpandResponse(
        path=result.path,
        theme=result.theme,
        parent_path=result.parent_path,
    )

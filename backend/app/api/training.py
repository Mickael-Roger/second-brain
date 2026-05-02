"""HTTP endpoint for the on-demand Training fiche generator.

The wiki view calls ``POST /api/training/expand`` whenever the user
clicks a dead wikilink under the configured ``training_folder``. The
endpoint resolves the parent fiche (when given), runs the LLM-driven
fiche generator, and returns the vault-relative path of the new note
so the wiki can navigate straight to it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import current_user
from app.config import get_settings
from app.training import TrainingExpandError, expand_concept
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

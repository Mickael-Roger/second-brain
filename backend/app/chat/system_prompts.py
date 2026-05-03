"""Server-side registry of named system prompts.

The chat endpoint accepts a ``system_prompt_id`` whitelist key (never
free-form text), and we map it here to a builder function plus the
restricted tool surface that prompt declares it uses. Any unknown id
is rejected with HTTP 400 before the LLM is invoked.

Used by feature-specific chat flows (e.g. the Training kickoff) that
need a different persona + a tightly-scoped toolbox.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SystemPromptSpec:
    build: Callable[[str], str]   # (language) → full system prompt text
    allowed_tools: frozenset[str]  # tool names available; empty = no tools


def _build_training_kickoff(language: str) -> str:
    # Imported lazily so unrelated chat traffic doesn't pull the
    # training prompt module on every request.
    from app.training.prompts import build_kickoff_system_prompt

    return build_kickoff_system_prompt(language)


_REGISTRY: dict[str, SystemPromptSpec] = {
    "training-kickoff": SystemPromptSpec(
        build=_build_training_kickoff,
        allowed_tools=frozenset({"training.finalize_kickoff"}),
    ),
}


def get_spec(system_prompt_id: str) -> SystemPromptSpec | None:
    return _REGISTRY.get(system_prompt_id)

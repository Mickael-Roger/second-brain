"""Generate illustrations for Training fiches via the configured image
provider (OpenAI ``/v1/images/generations`` for ``image_provider:
openai``). Saves the PNG under ``<training_folder>/<theme>/_assets/`` so
the file lives next to the fiche that references it. The git guard
commits everything in one transaction with the surrounding fiche write.

The LLM calls this only when a *visual* illustration genuinely helps —
mermaid is preferred for flowcharts/architecture, LaTeX for formulas.
The system prompt instructs the LLM accordingly."""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from pathlib import Path

import httpx

from app.config import LLMTaskConfig, get_settings
from app.vault.paths import resolve_vault_path

log = logging.getLogger(__name__)


class ImageGenerationError(RuntimeError):
    pass


def _slugify(text: str, *, fallback: str = "img") -> str:
    s = re.sub(r"[^\w\s-]", "", (text or "").lower(), flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:48] or fallback


def _provider_creds(provider_name: str) -> tuple[str, str]:
    """Return (base_url, api_key) for an OpenAI-compatible image provider."""
    s = get_settings()
    cfg = s.llm.providers.get(provider_name)
    if cfg is None:
        raise ImageGenerationError(f"image provider '{provider_name}' not in llm.providers")
    if cfg.kind != "openai":
        raise ImageGenerationError(
            f"image provider '{provider_name}' has kind={cfg.kind!r}; "
            "only OpenAI-compatible providers expose /v1/images/generations"
        )
    if not cfg.base_url or not cfg.api_key:
        raise ImageGenerationError(f"image provider '{provider_name}' is missing base_url / api_key")
    return cfg.base_url.rstrip("/"), cfg.api_key


async def generate_image_bytes(
    prompt: str,
    *,
    task: LLMTaskConfig,
    size: str = "1024x1024",
    timeout: float = 120.0,
) -> bytes:
    """Call the image provider, return raw PNG bytes. Raises on any failure."""
    if not (task.image_provider and prompt.strip()):
        raise ImageGenerationError("image generation not configured or empty prompt")

    base_url, api_key = _provider_creds(task.image_provider)
    body = {
        "model": task.image_model,
        "prompt": prompt,
        "size": size,
        "n": 1,
        # gpt-image-1 / gpt-image-2 always return base64; older dall-e
        # models default to URLs. Asking for b64 keeps both branches the
        # same on our side.
        "response_format": "b64_json",
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/images/generations", json=body, headers=headers)
        if resp.status_code >= 400:
            raise ImageGenerationError(
                f"image provider returned {resp.status_code}: "
                f"{resp.text[:500]}"
            )
        payload = resp.json()

    data = (payload.get("data") or [{}])[0]
    b64 = data.get("b64_json")
    if not b64:
        url = data.get("url")
        if not url:
            raise ImageGenerationError("image provider returned no b64_json or url")
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content
    return base64.b64decode(b64)


def asset_relpath(theme: str, prompt: str) -> str:
    """Compute the vault-relative path for a generated illustration.

    Layout: ``<training_folder>/<theme>/_assets/<slug>-<hash>.png``.
    The 8-char hash on the prompt avoids collisions when the same fiche
    asks for multiple illustrations on related concepts.
    """
    s = get_settings()
    theme_slug = _slugify(theme, fallback="theme")
    prompt_slug = _slugify(prompt, fallback="img")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
    folder = s.obsidian.training_folder.strip("/")
    return f"{folder}/{theme_slug}/_assets/{prompt_slug}-{digest}.png"


def write_image_bytes(rel_path: str, data: bytes) -> Path:
    """Write the PNG to the vault. Caller must hold the git guard if a
    surrounding transaction is in flight."""
    abs_path = resolve_vault_path(rel_path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(data)
    return abs_path

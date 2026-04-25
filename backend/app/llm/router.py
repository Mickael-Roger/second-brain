"""Provider registry: build typed providers from the config and resolve by name."""

from __future__ import annotations

from functools import lru_cache

from app.config import LLMProviderConfig, get_settings

from .base import LLMProvider
from .openai_compat import OpenAICompatProvider


def _build(name: str, cfg: LLMProviderConfig) -> LLMProvider:
    if cfg.kind == "openai":
        return OpenAICompatProvider(
            name=name,
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            model=cfg.default_model,
        )
    if cfg.kind == "anthropic":
        # Phase 1 only ships the OpenAI-compat adapter. The Anthropic adapter
        # arrives in phase 4; until then we surface a clear runtime error.
        raise NotImplementedError(
            "Anthropic provider arrives in phase 4. Configure an OpenAI-compatible provider for now."
        )
    raise ValueError(f"unknown provider kind: {cfg.kind}")


class LLMRouter:
    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self._default: str = ""

    @classmethod
    def from_settings(cls) -> "LLMRouter":
        s = get_settings()
        r = cls()
        r._default = s.llm.default
        for name, cfg in s.llm.providers.items():
            try:
                r._providers[name] = _build(name, cfg)
            except NotImplementedError:
                # Skip unsupported providers but don't crash — they'll error on use.
                continue
        if r._default not in r._providers:
            # Fall back to the first available provider.
            if not r._providers:
                raise RuntimeError("No usable LLM provider in configuration.")
            r._default = next(iter(r._providers))
        return r

    def list_providers(self) -> list[dict]:
        s = get_settings()
        out: list[dict] = []
        for name, cfg in s.llm.providers.items():
            out.append(
                {
                    "name": name,
                    "kind": cfg.kind,
                    "models": list(cfg.models),
                    "default_model": cfg.default_model,
                }
            )
        return out

    def default_name(self) -> str:
        return self._default

    def default_model_for(self, provider_name: str) -> str:
        return get_settings().llm.providers[provider_name].default_model

    def has_model(self, provider_name: str, model: str) -> bool:
        cfg = get_settings().llm.providers.get(provider_name)
        return cfg is not None and model in cfg.models

    def get(self, name: str | None = None) -> LLMProvider:
        key = name or self._default
        if key not in self._providers:
            raise KeyError(f"LLM provider '{key}' is not configured or not yet supported")
        return self._providers[key]


@lru_cache(maxsize=1)
def get_llm_router() -> LLMRouter:
    return LLMRouter.from_settings()

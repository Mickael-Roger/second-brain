"""Configuration loading and validation.

Loads `config.yml` once at process start and exposes a typed `Settings` object
through `get_settings()`. The path is taken from the `CONFIG_PATH` env var or
defaults to `./config.yml`.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class AppSection(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    base_url: str = "http://localhost:8000"
    language: Literal["en", "fr"] = "fr"
    data_dir: Path = Path("/data")


class AuthSection(BaseModel):
    username: str
    password_hash: str
    session_secret: str = Field(min_length=16)
    session_lifetime_days: int = 30


class LLMProviderConfig(BaseModel):
    """One LLM provider entry.

    `kind` selects the wire format and authentication scheme:

    - ``openai``    — OpenAI-compatible chat/completions, API key auth.
                      Requires `base_url` and `api_key`.
    - ``anthropic`` — Anthropic /v1/messages, API key auth (phase 4).
                      Requires `base_url` and `api_key`.
    - ``chatgpt``   — ChatGPT Plus / Pro / Team subscription via OAuth, hits
                      the Codex Responses API. No `base_url` or `api_key`
                      needed; tokens come from the device-flow login (run
                      ``second-brain chatgpt-login <provider>``).
    """

    kind: Literal["openai", "anthropic", "chatgpt"]
    base_url: str | None = None
    api_key: str | None = None
    models: list[str] = Field(min_length=1)

    @field_validator("models")
    @classmethod
    def _no_dupes(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("models list contains duplicates")
        return v

    @model_validator(mode="after")
    def _check_required(self) -> "LLMProviderConfig":
        if self.kind in ("openai", "anthropic"):
            if not self.base_url:
                raise ValueError(f"{self.kind} provider requires `base_url`")
            if not self.api_key:
                raise ValueError(f"{self.kind} provider requires `api_key`")
        # `chatgpt` providers intentionally have no api_key / base_url —
        # auth comes from the OAuth token file.
        return self

    @property
    def default_model(self) -> str:
        return self.models[0]


class LLMSection(BaseModel):
    default: str
    max_tool_rounds: int = 10
    providers: dict[str, LLMProviderConfig]

    @field_validator("providers")
    @classmethod
    def _at_least_one(cls, v: dict[str, LLMProviderConfig]) -> dict[str, LLMProviderConfig]:
        if not v:
            raise ValueError("at least one LLM provider must be configured")
        return v

    def resolved_default(self) -> LLMProviderConfig:
        if self.default not in self.providers:
            raise ValueError(
                f"llm.default = '{self.default}' is not in llm.providers"
            )
        return self.providers[self.default]


class ObsidianGitSection(BaseModel):
    enabled: bool = False
    remote: str = "origin"
    branch: str = "main"
    ssh_key_path: Path | None = None
    author_name: str = "Second Brain"
    author_email: str = "second-brain@local"


class ObsidianJournalSection(BaseModel):
    folder: str = "Journal"
    # Path of an archived daily note relative to the vault, with placeholders
    # {folder}, {year}, {month}, {date}.
    archive_template: str = "{folder}/{year:04d}/{month:02d}/{date}.md"


class ObsidianSection(BaseModel):
    vault_path: Path | None = None
    # Vault-relative filenames for the three "context" files that get
    # auto-injected into the LLM's system prompt at every chat session.
    # Each file is optional — a missing file is silently skipped.
    index_file: str = "INDEX.md"             # the vault's structural map
    user_file: str = "USER.md"               # facts about the user
    preferences_file: str = "PREFERENCES.md" # how the brain should operate
    # System prompt for the nightly Organize task. Optional — missing file
    # falls back to the built-in default in app.jobs.organize.
    organize_prompt_file: str = "ORGANIZE.md"
    journal: ObsidianJournalSection = ObsidianJournalSection()
    git: ObsidianGitSection = ObsidianGitSection()


class OrganizeSection(BaseModel):
    enabled: bool = False
    schedule: str = "0 3 * * *"  # cron: nightly at 03:00
    mode: Literal["dry-run", "apply"] = "dry-run"
    modified_since: Literal["last_run", "always_full"] = "last_run"


class FreshRSSSourceConfig(BaseModel):
    """Fever-API-compatible FreshRSS endpoint.

    `api_key` is the pre-computed `md5(username:password)` token Fever
    expects in the POST body — pre-compute it once, paste it in. We do
    NOT take username/password here; storing the hash directly avoids
    hashing logic at startup and keeps the config self-describing.
    """

    base_url: str                       # e.g. https://freshrss.example.com/api/fever.php
    api_key: str                        # md5(username:password)
    max_items_per_run: int = 500
    # FreshRSS folder/group ids to skip on every fetch — articles whose
    # feed lives in these folders are never stored. The Fever group ids
    # are integers (stringified here) visible in FreshRSS's URL when
    # editing a category. Useful for muting noisy or off-topic folders
    # without unsubscribing from the feeds in FreshRSS itself.
    excluded_group_ids: list[str] = Field(default_factory=list)


class NewsSourcesSection(BaseModel):
    freshrss: FreshRSSSourceConfig | None = None


class NewsSection(BaseModel):
    enabled: bool = False
    fetch_schedule: str = "*/5 * * * *"    # cron, UTC — every 5 minutes by default
    sources: NewsSourcesSection = NewsSourcesSection()


class AnkiSection(BaseModel):
    """Anki integration via the AnkiConnect plugin.

    AnkiConnect runs inside the user's Anki desktop and exposes a
    JSON-RPC HTTP endpoint (default http://127.0.0.1:8765). The brain
    talks to it for every Anki operation; there is no local mirror.
    `api_key` is optional — set it only if AnkiConnect is configured
    with the `apiKey` field in its plugin settings.
    """

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    api_key: str | None = None
    timeout_seconds: float = 10.0

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


class SMTPSection(BaseModel):
    enabled: bool = False
    host: str | None = None
    port: int = 587
    # Connection security:
    #   none      — plain SMTP, no TLS.
    #   starttls  — plain SMTP then STARTTLS upgrade (port 587 typical).
    #   ssl       — SSL/TLS from the start (port 465 typical).
    # When unset, falls back to the legacy `starttls` boolean for backwards
    # compatibility (true → starttls, false → none).
    security: Literal["none", "starttls", "ssl"] | None = None
    starttls: bool = True  # deprecated; prefer `security`.
    username: str | None = None
    password: str | None = None
    from_address: str | None = None
    to_address: str | None = None
    format: Literal["text", "html"] = "text"

    @model_validator(mode="after")
    def _resolve_and_check(self) -> "SMTPSection":
        # Backfill `security` when not explicitly set:
        #   - port 465 is canonically implicit-TLS → ssl,
        #   - otherwise honor the legacy `starttls` boolean.
        if self.security is None:
            if self.port == 465:
                resolved = "ssl"
            else:
                resolved = "starttls" if self.starttls else "none"
            object.__setattr__(self, "security", resolved)
        if self.enabled:
            missing = [
                f for f in ("host", "from_address", "to_address") if not getattr(self, f)
            ]
            if missing:
                raise ValueError(
                    f"smtp.enabled = true but missing fields: {', '.join(missing)}"
                )
        return self


class LoggingSection(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["text", "json"] = "text"


class Settings(BaseModel):
    app: AppSection = AppSection()
    auth: AuthSection
    llm: LLMSection
    obsidian: ObsidianSection = ObsidianSection()
    organize: OrganizeSection = OrganizeSection()
    news: NewsSection = NewsSection()
    anki: AnkiSection = AnkiSection()
    smtp: SMTPSection = SMTPSection()
    logging: LoggingSection = LoggingSection()

    @property
    def database_url(self) -> str:
        db_path = self.app.data_dir / "second-brain.db"
        return f"sqlite:///{db_path}"

    @property
    def chats_dir(self) -> Path:
        """Where chat markdown files are written.

        Always under the data dir — chat transcripts are not part of the
        vault's permanent knowledge (the user explicitly keeps things via the
        LLM's vault tools instead).
        """
        return self.app.data_dir / "chats"


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(
            f"config file not found at {path}. Set CONFIG_PATH or create ./config.yml"
        )
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    path = Path(os.environ.get("CONFIG_PATH", "config.yml"))
    raw = _load_yaml(path)
    return Settings.model_validate(raw)


def reload_settings() -> Settings:
    """Force-reload of the config; only useful for tests."""
    get_settings.cache_clear()
    return get_settings()

# Second Brain — Project Specification

> Mono-user, self-hosted, Docker-deployed personal "second brain". A web/PWA + WhatsApp interface that unifies news (FreshRSS), notes (Obsidian), flashcards (Anki), tasks (WebDAV) and arbitrary user-defined modules behind a chatbot powered by configurable LLMs.

This document is the implementation spec for the coding agent. Decisions in here are intentional — challenge them in PRs, don't silently deviate.

---

## 1. Vision

The app is a **single entry point** to the user's personal knowledge tools. The LLM is the operator: the user reads, talks, and approves; the LLM does the busywork (summarizing news, creating Anki cards from articles, refactoring notes, organizing the Obsidian vault, ferrying items between tools).

Two interaction surfaces:

- **Web app (SPA + PWA)**: rich per-module UIs with a contextual chatbot, plus a global chat. Desktop and mobile.
- **WhatsApp bot**: a chat-only interface to the global chatbot, for on-the-go capture and queries.

The data lives in the user's existing tools — the app **does not become another silo**. Persistence in the app's own DB is limited to ephemeral technical state (last-sync timestamps, chat index, etc.). Anything semantically meaningful (chat transcripts, organized notes, todos, cards) is written back to the appropriate tool.

---

## 2. High-level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Docker container                         │
│                                                              │
│   ┌────────────────────────────────────────────────────┐    │
│   │ FastAPI backend (Python)                           │    │
│   │  ├── REST + SSE/WebSocket endpoints                │    │
│   │  ├── Auth (single user, password+session)          │    │
│   │  ├── Chat orchestrator (LLM ↔ tools loop)          │    │
│   │  ├── LLM provider router (OpenAI/Anthropic compat) │    │
│   │  ├── MCP client manager (stdio + HTTP)             │    │
│   │  ├── Built-in modules (News, Obsidian, Personal …) │    │
│   │  ├── WhatsApp Cloud API webhook                    │    │
│   │  └── Static file server (built SPA)                │    │
│   └────────────────────────────────────────────────────┘    │
│                                                              │
│   ┌──────────────────┐   ┌────────────────────────────┐    │
│   │ stdio MCP procs  │   │ SQLite (technical state)   │    │
│   │ (Anki, Tasks, …) │   │   data/second-brain.db     │    │
│   └──────────────────┘   └────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
        │                              │
        ▼                              ▼
  ┌──────────────┐            ┌──────────────────┐
  │ Obsidian     │            │ FreshRSS server  │
  │ vault (bind  │            │ (Fever API)      │
  │ mount + git) │            └──────────────────┘
  └──────────────┘            ┌──────────────────┐
                              │ External HTTP    │
                              │ MCP servers      │
                              └──────────────────┘
                              ┌──────────────────┐
                              │ Meta WhatsApp    │
                              │ Cloud API        │
                              └──────────────────┘
```

### 2.1 Tech stack

- **Backend**: Python 3.12, FastAPI, Uvicorn, `httpx`, `pydantic` v2, `pydantic-settings`, `SQLModel` (SQLite), `mcp` (official Python SDK), `pygit2` or `GitPython` for the Obsidian git workflow, `python-multipart` for image uploads.
- **Frontend**: React 18 + TypeScript + Vite, TanStack Query + TanStack Router, Tailwind CSS + shadcn/ui primitives, `i18next` (FR/EN), `vite-plugin-pwa` for PWA. State: lightweight (Zustand) for UI, server state via TanStack Query.
- **Streaming**: SSE for chat (simpler than WS, works through proxies). WebSockets only if a feature genuinely needs bidirectional push — none currently planned.
- **Build**: multi-stage Dockerfile (Node build → static assets copied into Python image). `uv` for Python dep management.
- **Tests**: `pytest` + `pytest-asyncio` (backend), `vitest` (frontend), Playwright for the few E2E flows that matter (login, send chat, organize).

### 2.2 Why no LiteLLM, no embeddings, no Postgres

User decisions, deliberate:

- **No LiteLLM**: thin in-house adapters give full control over tool-call translation and streaming semantics, with no surprise cost layer.
- **No embeddings / RAG**: the Obsidian vault itself is the index. Notes are organized into a wiki-style structure (categories with synthesis notes, wikilinks). LLM tools navigate the vault by category and link, not by vector search.
- **SQLite only**: mono-user, low write volume, easy to back up, fits in the same volume as the vault and config.

---

## 3. Module System

A **module** is a logical area of the second brain (News, Obsidian, Anki, Tasks, Personal Life, …). Each module has:

- A unique `id` (e.g. `news`, `obsidian`, `anki`, `tasks`, `personal_life`).
- A display name and icon (i18n).
- A set of **tools** the LLM can call (sourced from a built-in implementation or from an MCP server).
- An optional **UI view** rendered when the user clicks the left-menu entry.
- An optional dedicated **system prompt** (used when chatting "inside" the module).
- An optional **default LLM provider/model override**.

### 3.1 Built-in modules (first-class)

These ship with the app and have rich UIs. They are **not** MCP servers — they're Python code living in `backend/app/modules/`.

| Module        | UI                                                                                                                                                                                                       | Tools (exposed to LLM)                                                                                                                                                                                                                      |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| News          | List feeds + items with filters (unread, starred, category, search). Reader pane shows the LLM-synthesized description from FreshRSS. Click on an item → opens a contextual chat preloaded with the item. | `news_list`, `news_get`, `news_mark_read`, `news_star`, `news_search`                                                                                                                                                                       |
| Obsidian      | Tree view of the vault, markdown editor (read-mode + edit), backlinks pane, **Organize** button (see §6). Uses CodeMirror 6.                                                                              | `obsidian_list`, `obsidian_read`, `obsidian_write`, `obsidian_append`, `obsidian_move`, `obsidian_delete`, `obsidian_search`, `obsidian_link_suggest`, `obsidian_organize_start/iterate/commit`                                              |
| Chat (global) | Plain chat with access to **all** registered tools across modules.                                                                                                                                       | All tools                                                                                                                                                                                                                                   |
| Personal Life | Composite dashboard (see §3.4). Chat with a curated tool subset and a custom system prompt.                                                                                                              | News (filtered to `personal life` category), Obsidian (scoped to a configured folder), Tasks (configured list)                                                                                                                              |

### 3.2 MCP-driven modules

Anki and Tasks (and any future module) are declared in `config.yml` as MCP servers. The app:

1. At startup, spawns each declared `stdio` MCP server (managing its lifecycle) and connects to each `http` MCP server.
2. Calls `tools/list` to discover the module's tools.
3. Registers them under a namespace (e.g. `anki.add_card`, `tasks.create`).
4. Routes LLM tool calls back to the right MCP client.

For known modules with first-class UI components shipped in the SPA (Anki: deck/card list, Tasks: list view), the module config can opt into a `ui` preset that calls the MCP tools through the backend. Unknown user-added MCP modules fall back to a generic **chat-only** view.

```yaml
modules:
  anki:
    name: { en: "Anki", fr: "Anki" }
    icon: cards
    ui: anki  # built-in UI preset; if omitted, falls back to chat-only
    mcp:
      transport: stdio
      command: "uvx"
      args: ["mcp-anki-server"]
      env:
        ANKI_CONNECT_URL: "http://host.docker.internal:8765"
    system_prompt: |
      You manage the user's Anki decks. Default deck is "Daily".
    llm:
      provider: openai-cheap
      model: gpt-4o-mini
```

### 3.3 Module registry

`ModuleRegistry` is the single source of truth at runtime. It exposes:

- `list_modules()` — for the SPA left menu.
- `get_tools(module_id=None)` — returns tool descriptors for the LLM. `None` returns the union (used by global chat).
- `dispatch(tool_name, arguments)` — calls either a Python implementation or an MCP client.

Tool descriptors use a **unified internal type** (see §4.2) and are translated into provider-specific tool schemas at request time.

### 3.4 Personal Life

Composite by design. Configured in `config.yml`:

```yaml
personal_life:
  system_prompt: |
    You help the user with their personal life. Be concise, kind,
    and proactive about wellbeing.
  sources:
    news_categories: ["Personal life"]
    obsidian_folder: "Personal/"
    tasks_list: "personal"
```

The dashboard renders three vertical strips (recent personal news, recent personal notes, open personal tasks) and a chat panel preloaded with a system prompt that aggregates these sources.

---

## 4. LLM Layer

### 4.1 Multi-provider configuration

`config.yml` declares one or more **named** providers. Each provider is either OpenAI-compatible or Anthropic-compatible.

```yaml
llm:
  default: openai-fast
  providers:
    openai-fast:
      kind: openai
      base_url: https://api.openai.com/v1
      api_key: sk-...
      model: gpt-4o-mini
    anthropic-smart:
      kind: anthropic
      base_url: https://api.anthropic.com
      api_key: sk-ant-...
      model: claude-opus-4-5
    local-llama:
      kind: openai      # any OpenAI-compatible endpoint
      base_url: http://192.168.1.20:8080/v1
      api_key: none
      model: llama-3.1-70b
```

**Selection precedence** (most specific wins):

1. Per-message override (chat UI model picker, sent in the request body).
2. Per-module config (`modules.<id>.llm`).
3. Global default (`llm.default`).

### 4.2 Internal types

Provider-agnostic types defined in `backend/app/llm/types.py`:

```python
class TextBlock(BaseModel): type: Literal["text"]; text: str
class ImageBlock(BaseModel): type: Literal["image"]; mime: str; data: str  # base64
class ToolUseBlock(BaseModel): type: Literal["tool_use"]; id: str; name: str; input: dict
class ToolResultBlock(BaseModel): type: Literal["tool_result"]; tool_use_id: str; content: list[TextBlock | ImageBlock]; is_error: bool = False

class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: list[TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock]

class ToolDef(BaseModel):
    name: str
    description: str
    input_schema: dict  # JSON Schema

class StreamEvent(BaseModel):
    type: Literal["text_delta", "tool_use", "tool_result", "done", "error"]
    ...
```

### 4.3 Provider adapters

Two adapters with a common interface:

```python
class LLMProvider(Protocol):
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDef],
        model: str,
        **kwargs,
    ) -> AsyncIterator[StreamEvent]: ...
```

- `OpenAICompatProvider` uses `/v1/chat/completions` with `stream=True`. Translates `ToolUseBlock` ↔ `tool_calls`, images ↔ `image_url` content parts (data URI form).
- `AnthropicCompatProvider` uses `/v1/messages` with `stream=True`. Native support for `tool_use`/`tool_result`/`image`. System prompt goes in the top-level `system` field.

Tool name namespacing: the registry stores tools as `<module_id>.<tool>`; the OpenAI adapter must map `.` to a permitted character (e.g. `__`) since OpenAI rejects dots in tool names. Anthropic accepts dots — passthrough.

### 4.4 Chat orchestrator

The orchestrator (`backend/app/chat/orchestrator.py`) runs the canonical LLM-loop:

```
loop:
  events = provider.stream(messages, tools, model)
  for event in events:
      yield event to SSE
      if event is tool_use:
          collect into pending_calls
  if no pending_calls: break
  for call in pending_calls (parallel where safe):
      result = registry.dispatch(call.name, call.input)
      append tool_result to messages
  append assistant turn to messages
```

Limits: max 10 sequential tool-call rounds per user turn (configurable). Tool calls dispatched in parallel within one round when the LLM emits multiple, except for tools marked `serial: true` (e.g. anything that mutates Obsidian — see §6.2).

---

## 5. MCP Integration

The official `mcp` Python SDK provides both `stdio` and `streamable-http` clients. The app does not act as an MCP server (deferred per the user — §13).

### 5.1 Lifecycle

`MCPManager` (in `backend/app/mcp/manager.py`):

- On startup, iterates configured MCP modules, opens a client per server.
- For `stdio`: spawns the subprocess in a dedicated `asyncio` task; restarts on crash with exponential backoff up to 5 retries, then surfaces a UI banner.
- For `http`: opens the streamable HTTP transport.
- Exposes `tools()` (cached, refreshed on `notifications/tools/list_changed`) and `call(name, args)`.
- On shutdown, gracefully terminates stdio children.

### 5.2 Tool bridging

Each MCP `Tool` is translated to a `ToolDef` with `name = f"{module_id}.{tool.name}"`. The MCP `inputSchema` is forwarded as-is. `outputSchema` (if any) is ignored by the LLM but used for typed parsing in built-in UI views.

### 5.3 Resources & prompts

MCP servers can expose resources and prompts in addition to tools. Phase 1 wires only **tools**. Resources/prompts are tracked as a backlog item.

---

## 6. Obsidian Module

The most complex module. Detailed because the spec is opinionated.

### 6.1 Vault layout assumptions

The vault is a git repository, bind-mounted at `/vault` in the container. Path is configurable. The container has its own SSH key (mounted) for git push.

```
/vault/
├── .git/
├── .obsidian/
├── SecondBrain/
│   ├── Chats/                  # generated chat transcripts (§7)
│   └── Organize/               # working drafts during organize flow
├── Personal/                   # configurable as personal_life folder
└── …user folders…
```

### 6.2 Git workflow

**Every** Obsidian write goes through `ObsidianGitGuard`. A single async lock serializes all vault mutations across the app — no parallel writes.

Pre-write sequence:

1. `git status --porcelain`. If dirty (Obsidian Sync wrote files outside the app):
   - `git add -A`
   - `git commit -m "external changes [auto]"` (commits external work so the LLM never overwrites unsaved user edits).
2. `git pull --rebase`.
3. **Conflict handling**: on rebase conflict, abort the rebase (`git rebase --abort`), surface a structured error to the chat orchestrator. The orchestrator returns it as a `tool_result` with `is_error=true` and a human-readable explanation. The user resolves manually outside the app, then retries.
   - _Future_ (not phase 1): LLM-assisted three-way merge for `.md` files only.

Post-write sequence:

1. `git add <changed paths>`
2. `git commit -m "<descriptive>"`. The commit message is generated by the orchestrator: it knows what tool it just ran and on which paths.
3. `git push`. On push rejection (non-fast-forward — race), retry once: pull-rebase + push. Second failure surfaces an error.

The lock guarantees atomicity per high-level operation (e.g. an "organize" pass touching 20 files = one commit, not 20).

### 6.3 Vault primitives (`backend/app/obsidian/vault.py`)

- Read/write notes (text body + parsed frontmatter via `python-frontmatter`).
- Path safety: every write path must resolve under `/vault` (no traversal). Reject symlinks pointing outside.
- Wikilink helpers: parse `[[Note]]`, `[[Note|alias]]`, `[[Note#heading]]`. Resolve to absolute paths.
- Backlink index: in-memory dict, refreshed on file mtime change. Used by `obsidian_link_suggest` and the editor backlinks pane. **Not** persisted to SQLite — rebuilt on startup (vault scan), incrementally updated on writes.

### 6.4 Organize flow

Triggered by an "Organize" button in the Obsidian module UI. The flow:

1. **Scope**: collect all notes with `mtime > module_state.obsidian.last_organized_at` (or all "uncategorized" notes if the user picks that option). Default: since-last-organize, with a UI to override.
2. **Per-note pass** (one LLM call per note, parallelized in batches):
   - Refactor (grammar, spelling) preserving the user's voice and the meaning.
   - Suggest frontmatter tags + category.
   - Suggest wikilinks to existing notes (using the backlink index + a category index).
   - Suggest a target folder (per a configured taxonomy if present).
3. **Aggregate synthesis**: a final LLM call produces a markdown synthesis of what changed (per-note bullet list + a "what's new" section).
4. **Interactive review**: the SPA renders the synthesis. The user can:
   - Chat to ask for modifications ("don't tag note X as `work`", "merge notes Y and Z").
   - Approve individual notes (per-note checkboxes).
   - Click **Validate** to commit.
5. **Commit**: only on validation, the diffs are written to disk through `ObsidianGitGuard` as **one** commit, and `last_organized_at` is updated.

Backing tools (the LLM uses them, the UI also calls them via the API):

- `obsidian_organize_start(scope)` → returns a session id and the candidate note list.
- `obsidian_organize_iterate(session_id, instructions?)` → produces or updates the proposal (drafts kept in `/vault/SecondBrain/Organize/<session>/`, not committed).
- `obsidian_organize_commit(session_id, accepted_notes)` → applies, commits, pushes, deletes the working folder.
- `obsidian_organize_discard(session_id)` → deletes the working folder, no commit.

Drafts under `Organize/<session>/` are gitignored via `.gitignore` so transient state never touches history.

---

## 7. Chat Persistence

Chat history is stored as **markdown files inside the Obsidian vault**. SQLite stores only the index.

### 7.1 File format

Path: `/vault/SecondBrain/Chats/<YYYY>/<YYYY-MM-DD>-<slug>.md`.

```markdown
---
chat_id: 01HXYZ…
module_id: news
title: "Anki cards from morning news"
created_at: 2026-04-25T08:13:00Z
updated_at: 2026-04-25T08:21:00Z
model: openai-fast/gpt-4o-mini
---

## User
…

## Assistant
…

<details><summary>Tool call: news_get(id=42)</summary>

```json
{ "id": 42, … }
```
</details>

## Assistant
…
```

Tool calls and results are stored as collapsible `<details>` blocks so the markdown is human-readable in Obsidian without losing fidelity. The orchestrator can re-parse them to reconstruct the full message list when a chat is resumed.

### 7.2 SQLite `chats` table

```sql
CREATE TABLE chats (
    id          TEXT PRIMARY KEY,        -- ULID
    title       TEXT NOT NULL,
    path        TEXT NOT NULL,           -- relative to vault root
    module_id   TEXT,                    -- nullable for global chat
    created_at  TIMESTAMP NOT NULL,
    updated_at  TIMESTAMP NOT NULL,
    archived    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_chats_module ON chats(module_id, updated_at DESC);
```

### 7.3 UI affordances

- Chat sidebar with: search, filter by module, create new, load, rename, delete (delete = archive in DB + move file to `Chats/_archived/`, no hard delete by default).
- Title is generated by the LLM after the first user/assistant exchange.

---

## 8. WhatsApp Bot

### 8.1 Transport

Meta WhatsApp Cloud API. The container exposes a webhook `POST /webhook/whatsapp` that the user must publicly reach (reverse proxy / tunnel). Verification via `GET /webhook/whatsapp` per Meta's spec.

Config:

```yaml
whatsapp:
  enabled: true
  phone_number_id: "1234567890"
  verify_token: "user-chosen-string"
  access_token: "EAAG…"
  allowed_sender_phone: "+33612345678"   # only this number is accepted
```

### 8.2 Behavior

- Incoming text → fed into the **global chat** orchestrator. The conversation is persisted as a chat in `Chats/` with `module_id = whatsapp`, one chat per calendar day per default (configurable: `daily | per-session`).
- Incoming image → multimodal; same orchestrator path.
- Outgoing: text only in phase 1. (Voice notes deferred.)
- Streaming: Cloud API doesn't support streaming responses, so the orchestrator collects the full assistant turn before sending. Tool calls happen silently between user message and assistant reply.
- Long replies > 4096 chars are split.

### 8.3 Security

- Reject any message whose `from` ≠ `allowed_sender_phone`. Log + drop.
- Webhook signature verification (Meta sends `X-Hub-Signature-256`) using the app secret from config.

---

## 9. Configuration File

`config.yml` is loaded at startup. Hot-reload **not** supported in phase 1 (restart the container).

### 9.1 Schema (illustrative)

```yaml
app:
  host: 0.0.0.0
  port: 8000
  base_url: https://second-brain.example.com   # used for WhatsApp webhook + PWA
  language: fr            # default UI language; user can override in-session

auth:
  username: mickael
  # bcrypt hash; generated by `python -m app.cli hash-password`
  password_hash: "$2b$12$…"
  session_secret: "long-random-string"
  session_lifetime_days: 30

llm:
  default: openai-fast
  max_tool_rounds: 10
  providers:
    openai-fast:
      kind: openai
      base_url: https://api.openai.com/v1
      api_key: sk-…
      model: gpt-4o-mini
    anthropic-smart:
      kind: anthropic
      base_url: https://api.anthropic.com
      api_key: sk-ant-…
      model: claude-opus-4-5

obsidian:
  vault_path: /vault
  git:
    enabled: true
    remote: origin
    branch: main
    ssh_key_path: /run/secrets/obsidian_ssh   # mounted
    author_name: "Second Brain"
    author_email: "second-brain@local"
  organize:
    folder_taxonomy: /vault/.taxonomy.yml      # optional

freshrss:
  base_url: https://rss.example.com
  api: fever
  username: mickael
  api_password: "fever-password"

modules:
  news: { enabled: true }
  obsidian: { enabled: true }
  personal_life:
    enabled: true
    system_prompt: |
      You help the user with their personal life. Be concise, kind …
    sources:
      news_categories: ["Personal life"]
      obsidian_folder: "Personal/"
      tasks_list: "personal"
  anki:
    enabled: true
    name: { en: "Anki", fr: "Anki" }
    icon: cards
    ui: anki
    mcp:
      transport: stdio
      command: uvx
      args: ["mcp-anki-server"]
      env:
        ANKI_CONNECT_URL: "http://host.docker.internal:8765"
    system_prompt: "You manage the user's Anki decks. Default deck is Daily."
    llm: { provider: openai-fast, model: gpt-4o-mini }
  tasks:
    enabled: true
    name: { en: "Tasks", fr: "Tâches" }
    icon: check
    ui: tasks
    mcp:
      transport: http
      url: http://tasks-mcp:9000/mcp
      headers:
        Authorization: "Bearer …"

whatsapp:
  enabled: true
  phone_number_id: "…"
  verify_token: "…"
  access_token: "…"
  allowed_sender_phone: "+33…"
  app_secret: "…"

logging:
  level: INFO
  format: json
```

### 9.2 Validation

`pydantic-settings` model with strict validation. Startup fails fast on:

- Unreachable LLM provider (smoke `models` call).
- Vault path not a git repo when `git.enabled: true`.
- Unreachable FreshRSS server.
- MCP `stdio` command not on `PATH`.

---

## 10. SQLite Schema

`data/second-brain.db`. Migrations via Alembic (one revision file per change).

```sql
CREATE TABLE chats (… see §7.2 …);

CREATE TABLE module_state (
    module_id TEXT NOT NULL,
    key       TEXT NOT NULL,
    value     TEXT,                         -- JSON
    updated_at TIMESTAMP NOT NULL,
    PRIMARY KEY (module_id, key)
);

CREATE TABLE sessions (
    id          TEXT PRIMARY KEY,           -- session token (hashed)
    created_at  TIMESTAMP NOT NULL,
    expires_at  TIMESTAMP NOT NULL,
    user_agent  TEXT,
    ip          TEXT
);

CREATE TABLE settings (                     -- user-mutable runtime settings
    key   TEXT PRIMARY KEY,
    value TEXT
);
```

That's it. Anything else lives in the user's tools.

---

## 11. Auth

- Login form posts username + password. Server verifies bcrypt hash from config. Issues an HTTP-only, Secure, SameSite=Lax cookie holding a session id; the id is hashed in `sessions`.
- Session lifetime configurable; sliding refresh on activity.
- `/api/*` requires session except `/api/auth/login` and `/webhook/whatsapp`.
- WhatsApp webhook authenticated by Meta signature (§8.3) — independent of user session.
- Rate limit on `/api/auth/login`: 5 attempts / 15 min / IP.

---

## 12. Internationalization

- Backend: error messages and assistant prompts can include `{lang}` placeholder. The orchestrator passes the current UI language as a system prompt header (`The user's preferred language is FR.`).
- Frontend: `i18next` with `en` and `fr` resource bundles in `frontend/locales/`. Key-based.
- Module display names in config are `{ en: "...", fr: "..." }` objects.
- Date/time formatting: `Intl.DateTimeFormat` with the active locale.

---

## 13. Multimodal

- Chat box accepts image paste/drop. Images are resized client-side (max 2048px long edge, JPEG q=0.85) before upload to keep payloads small.
- Backend stores images **transiently** in `data/uploads/<chat_id>/<ulid>.jpg` and references them in the chat markdown as standard Obsidian image embeds (`![[…]]`) once the chat is persisted. The image file is moved into the vault under `SecondBrain/Chats/_attachments/<YYYY-MM>/`.
- Provider adapters translate to OpenAI `image_url` parts (data URI) or Anthropic `image` content blocks.

---

## 14. MCP Server Exposure (Deferred)

User explicitly deferred this feature. The codebase should be structured to accommodate it later (the tool registry already exposes a clean dispatch interface), but **no MCP server implementation in phase 1**. Tracked as a future epic.

---

## 15. Deployment

### 15.1 Single container

```dockerfile
# Stage 1: build SPA
FROM node:20-alpine AS spa
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build  # outputs /app/dist

# Stage 2: runtime
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      git openssh-client ca-certificates && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev
COPY backend/ ./backend/
COPY --from=spa /app/dist ./backend/app/static/
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 15.2 Volumes & ports

```
docker run -d \
  --name second-brain \
  -p 8000:8000 \
  -v /host/path/vault:/vault \
  -v /host/path/data:/data \
  -v /host/path/config.yml:/config/config.yml:ro \
  -v /host/path/ssh_key:/run/secrets/obsidian_ssh:ro \
  --add-host=host.docker.internal:host-gateway \    # for AnkiConnect
  -e CONFIG_PATH=/config/config.yml \
  second-brain:latest
```

### 15.3 Reverse proxy

A `docker-compose.yml` example with Caddy (TLS termination) is shipped, but the app itself is just one service. `host.docker.internal` is used for AnkiConnect on the host.

---

## 16. Project Layout

```
second-brain/
├── PROJECT.md                  ← this file
├── README.md
├── pyproject.toml
├── uv.lock
├── Dockerfile
├── docker-compose.example.yml
├── config.example.yml
├── alembic.ini
├── backend/
│   ├── alembic/
│   ├── app/
│   │   ├── main.py             # FastAPI app + static SPA mount
│   │   ├── cli.py              # `hash-password`, `migrate`, …
│   │   ├── config.py
│   │   ├── auth/
│   │   ├── db/
│   │   ├── llm/
│   │   │   ├── base.py
│   │   │   ├── openai_compat.py
│   │   │   ├── anthropic_compat.py
│   │   │   ├── router.py
│   │   │   └── types.py
│   │   ├── mcp/
│   │   │   ├── manager.py
│   │   │   └── bridge.py
│   │   ├── modules/
│   │   │   ├── base.py
│   │   │   ├── registry.py
│   │   │   ├── news.py
│   │   │   ├── obsidian.py
│   │   │   ├── personal_life.py
│   │   │   └── chat.py
│   │   ├── obsidian/
│   │   │   ├── git.py
│   │   │   ├── vault.py
│   │   │   ├── organize.py
│   │   │   └── backlinks.py
│   │   ├── chat/
│   │   │   ├── orchestrator.py
│   │   │   ├── persistence.py
│   │   │   └── markdown.py     # encode/decode chat ↔ md
│   │   ├── whatsapp/
│   │   │   ├── webhook.py
│   │   │   └── client.py
│   │   ├── freshrss/
│   │   │   └── fever.py
│   │   ├── api/
│   │   │   ├── auth.py
│   │   │   ├── chat.py
│   │   │   ├── modules.py
│   │   │   ├── obsidian.py
│   │   │   ├── news.py
│   │   │   └── whatsapp.py
│   │   ├── static/             # built SPA copied here at image build
│   │   └── i18n/
│   └── tests/
│       ├── unit/
│       └── integration/
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── public/
│   │   └── manifest.webmanifest
│   ├── locales/
│   │   ├── en/translation.json
│   │   └── fr/translation.json
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── pwa.ts
│       ├── routes/
│       │   ├── login.tsx
│       │   ├── chat.tsx
│       │   ├── module.$id.tsx
│       │   └── settings.tsx
│       ├── components/
│       │   ├── chat/           # Chat, MessageList, Composer, ToolCallBlock
│       │   ├── layout/         # Sidebar, Header, MobileNav
│       │   ├── modules/
│       │   │   ├── news/
│       │   │   ├── obsidian/   # editor, tree, organize wizard
│       │   │   ├── anki/
│       │   │   ├── tasks/
│       │   │   └── generic/    # chat-only fallback
│       │   └── ui/             # shadcn primitives
│       ├── lib/
│       │   ├── api.ts          # fetch wrapper
│       │   ├── sse.ts
│       │   ├── i18n.ts
│       │   └── auth.ts
│       └── stores/
└── scripts/
    ├── dev-backend.sh
    └── dev-frontend.sh
```

---

## 17. API Surface (selected)

```
POST   /api/auth/login                    { username, password } → set cookie
POST   /api/auth/logout
GET    /api/auth/me

GET    /api/modules                       → [{id, name, icon, ui, …}]

POST   /api/chat                          { module_id?, chat_id?, model?, message: { content[] } }
                                          → SSE stream of StreamEvent
GET    /api/chats                         ?module_id=&q=
GET    /api/chats/{id}
PATCH  /api/chats/{id}                    { title? archived? }
DELETE /api/chats/{id}

# News (built-in)
GET    /api/news/items                    ?unread=&category=&q=
POST   /api/news/items/{id}/read
POST   /api/news/items/{id}/star

# Obsidian (built-in)
GET    /api/obsidian/tree
GET    /api/obsidian/note?path=
PUT    /api/obsidian/note                 { path, content }   # uses ObsidianGitGuard

# Organize
POST   /api/obsidian/organize/start       { scope }
POST   /api/obsidian/organize/{sid}/iterate  { instructions? }
POST   /api/obsidian/organize/{sid}/commit   { accepted_notes }
POST   /api/obsidian/organize/{sid}/discard

# WhatsApp
GET    /webhook/whatsapp                  Meta verification handshake
POST   /webhook/whatsapp                  Inbound messages
```

---

## 18. Phased Roadmap

Each phase ends with a working app. No phase ships half-done features.

**Phase 1 — Skeleton (target: end-to-end auth + chat with one provider)**
- Repo scaffold, Dockerfile, config loader, SQLite + Alembic.
- Auth (login, sessions).
- LLM router + OpenAI-compat adapter only.
- Global chat (no tools yet), persistence to a hardcoded `Chats/` path (no git).
- Minimal SPA: login page, chat page, sidebar.

**Phase 2 — Obsidian + git**
- Vault primitives, `ObsidianGitGuard`, basic Obsidian module with read/write tools.
- SPA: tree view, markdown editor (read-only first, then edit), backlinks pane.
- Chat persistence wired to vault.

**Phase 3 — News (FreshRSS)**
- Fever API client, News module + UI, contextual chat.

**Phase 4 — Anthropic provider + per-module overrides + multimodal**
- Anthropic adapter, image upload pipeline, model picker in UI.

**Phase 5 — MCP + Anki + Tasks**
- `MCPManager`, tool bridging, `anki` and `tasks` modules with first-class UI presets.

**Phase 6 — Organize flow**
- The full interactive organize wizard.

**Phase 7 — Personal Life dashboard**
- Composite view + custom system prompt.

**Phase 8 — WhatsApp bot**
- Webhook, signature verification, allowed sender, daily-chat persistence.

**Phase 9 — PWA polish, i18n complete, hardening**
- Service worker, offline shell, manifest, icons. Full FR/EN parity. Error boundaries, retry/backoff, observability.

**Deferred / future**
- MCP server exposure (the app as MCP server).
- LLM-assisted git conflict resolution.
- Voice notes for WhatsApp.
- Resource/prompt support from MCP servers.

---

## 19. Non-Goals (explicit)

- Multi-user / RBAC.
- Federation between vaults / cross-device direct sync (Obsidian Sync handles that).
- Embedding-based search.
- LiteLLM or any all-in-one LLM gateway.
- A separate database server (Postgres/Redis).
- Hot-reload of `config.yml` (restart-only).

---

## 20. Open Questions

To revisit during implementation, none of which block phase 1:

- **Concurrent organize sessions**: do we allow more than one in flight? Default proposal: no (single active session per user, since mono-user; subsequent starts ask to discard the previous draft).
- **Anki MCP server**: is there a maintained `mcp-anki-server` package, or do we fork/write a thin wrapper around AnkiConnect? Decide before phase 5.
- **Tasks WebDAV format**: when the user picks the MCP, the contract for a "Tasks list" needs to be confirmed (CalDAV VTODO assumed).
- **WhatsApp persistence cadence**: per-day vs per-session. Default: per-day, configurable later.
- **Token-budget management for long chats**: phase 1 sends the full transcript; if a chat blows the context window, we'll add summarization. Not designed yet.

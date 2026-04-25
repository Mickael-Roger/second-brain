# Second Brain — Project Specification

> Mono-user, self-hosted personal "second brain". An always-available LLM assistant that operates the user's existing tools and treats Obsidian as the only persistent memory. The user converses with it (web today, WhatsApp later); it reads/writes Obsidian, manages tasks/cards/news/calendar through their dedicated services, and reorganizes the vault every night.

This document is the implementation spec. Decisions in here are intentional — challenge them in PRs, don't silently deviate.

---

## 1. Vision

The brain is the user's mind augmented. **Obsidian is the only memory** — the brain doesn't keep copies of anything else. Tasks live in WebDAV. Cards live in Anki. News lives in FreshRSS. Calendar in CalDAV. The brain *operates* those systems through their APIs as it converses, but doesn't mirror them.

The user talks to the brain all day. The brain helps select what's worth remembering, and at what level (a full note, a line in the daily journal, a flashcard, a task, …). At night the brain consolidates: it links new notes to existing ones, fixes grammar, refactors structure, archives the day's journal, and emails a report.

---

## 2. Architecture

```
                 ┌──────────────────────────────────────┐
                 │       OBSIDIAN VAULT (git)           │
                 │       = the brain's memory           │
                 │       = the user's content only      │
                 └──────────────────────────────────────┘
                                 ▲
                                 │   pull → mutate → commit → push
                                 │   (single async lock, every write)
                ┌────────────────┴────────────────────┐
                │            SECOND BRAIN              │
                │                                      │
                │   Conversation surfaces:             │
                │     • Web app chat        (Phase 1)  │
                │     • Wiki (read + edit)  (Phase 1)  │
                │     • WhatsApp            (Phase 3)  │
                │                                      │
                │   Tools the LLM uses (no copies):    │
                │     • vault.*             (Phase 1)  │
                │     • daily.*             (Phase 1)  │
                │     • chat.search         (Phase 1)  │
                │     • tasks.*  (WebDAV)   (Phase 2)  │
                │     • anki.*   (Anki)     (Phase 2)  │
                │     • news.*   (FreshRSS) (Phase 2)  │
                │     • calendar.* (CalDAV) (Phase 2)  │
                │                                      │
                │   Nightly job: Organize the vault    │
                │     → email report via SMTP          │
                └──────────────────────────────────────┘
```

### 2.1 Tech stack (kept from earlier work)

- **Backend**: Python 3.12, FastAPI, Uvicorn, `httpx`, `pydantic` v2, raw `sqlite3` + plain `.sql` migrations (no ORM).
- **Frontend**: React 18 + TypeScript + Vite, TanStack Query, Tailwind, `i18next` (FR/EN), `vite-plugin-pwa`. CodeMirror 6 for the wiki editor.
- **Streaming**: SSE.
- **Scheduling**: APScheduler (in-process; no Redis).
- **Build**: multi-stage Dockerfile (Node build → Python runtime), single container.
- **LLM**: in-house adapters for OpenAI-compatible / Anthropic / ChatGPT-OAuth (Codex Responses API). No LiteLLM.
- **Email out**: stdlib `smtplib` for the nightly report.

### 2.2 What is *not* in the brain

- No ORM (SQLModel / SQLAlchemy / Alembic) — raw SQL only.
- No vector DB / embeddings — vault structure + wikilinks + ripgrep search.
- No Postgres / Redis — SQLite + the filesystem.
- No `Inbox/` (the user already has one in their vault — we use it, don't impose it), no `_system/` / `Sources/` / `Tasks/` / `Anki/` / `News/` / `Reports/` system folders.
- No "module" abstraction in the SPA — only Chat and Wiki.
- No mirroring of external systems into the vault (tasks, cards, news, emails are NOT copied as notes).

---

## 3. Vault content

The vault is the user's, with a few stable conventions the brain relies on. The user maintains their own folder structure (`Personal/`, `Tech/`, `Reading/`, …) — the brain navigates by reading, searching, and following wikilinks.

Three conventions the brain depends on:

| Path | Purpose |
|---|---|
| `INDEX.md` (vault root) | The user's map of their own vault — folder purposes, naming conventions, hub notes. **Auto-injected into every chat's system prompt** so the LLM always has the layout in mind. The brain may update it on explicit request or as part of the nightly Organize. |
| `Journal/YYYY-MM-DD.md` | One file per day, the user's daily journal. Today's entry sits at the flat path; the nightly Organize archives prior days into `Journal/YYYY/MM/YYYY-MM-DD.md`. |
| `Sport/YYYY.md` | One file per year of training, bullet-list of sessions. (Other yearly logs follow the same shape if the user wants them.) |

`Inbox/` exists in the user's vault for unclassified captures. The brain treats it like any other folder; the nightly Organize may propose moves *out of* `Inbox/` into the right topic folder.

### 3.1 `INDEX.md`

A markdown file the user writes and refines over time. Skeleton (the user fills it in):

```markdown
---
updated: 2026-04-25
---

# Vault index

## Folders
- `Inbox/` — freshly captured notes, unclassified.
- `Journal/` — daily journal; today's at the flat path,
  archived nightly to `Journal/YYYY/MM/YYYY-MM-DD.md`.
- `Sport/YYYY.md` — training log, one file per year.
- `Personal/` — private life, family, health.
- `Tech/` — engineering notes. Hub: [[Tech/Index]].
- `Reading/` — book and article notes.

## Conventions
- Wikilinks `[[Like This]]` everywhere.
- Frontmatter `tags:` are flat lowercase-kebab.
- New unclassified notes default to `Inbox/`.

## Hub notes
- [[Tech/Index]]
- [[Reading/Index]]
```

The brain reads this file at the start of every chat session, prepends it to the LLM's system prompt as `Here is the user's vault structure and conventions:\n\n<contents>`, and trusts what it says.

### 3.2 What does *not* live in the vault

- **Chat transcripts**: stored under `<data_dir>/chats/YYYY/<file>.md` and indexed in SQLite. Conversation is not knowledge; only what the user explicitly asks to keep is. The `chat.search` tool searches these.
- **Nightly Organize reports**: generated in memory, emailed, discarded.
- **Tasks, Anki cards, news items, calendar events**: never in the vault. The brain operates the source-of-truth services directly.

---

## 4. The LLM's tool surface

The orchestrator from Phase-1 already runs the canonical tool loop with SSE streaming. The remaining work is implementing the tool families. The LLM composes them — the user is in the loop for every "keep" decision.

### Phase 1

```
# Obsidian
vault.read(path) → text
vault.list(folder?, glob?) → [paths]
vault.search(query, in?) → [{path, snippet}]      # ripgrep
vault.write(path, content)                         # full overwrite, via git guard
vault.append(path, content)                        # idem
vault.create_note(folder, title, body, frontmatter?)
vault.move(src, dst)
vault.delete(path)

# Daily-note sugar (vault.append underneath, with date-aware path resolution)
daily.append(text, date?)        # default today; resolves flat → archived
daily.read(date?)

# Conversation memory (data_dir, not vault)
chat.search(q) → [{date, snippet, chat_id}]
```

### Phase 2 (user-guided, one at a time)

```
tasks.list_lists() → [list_names]
tasks.list(list, status?) → [{id, text, due, done}]
tasks.create(list, text, due?)
tasks.complete(list, id)
tasks.reopen(list, id)
tasks.delete(list, id)

anki.list_decks() → [{name, due, total}]
anki.add_card(deck, front, back, type, tags?)

news.unread(category?, limit?) → [{id, feed, title, summary, url, date}]
news.get(item_id)
news.mark_read(item_id)
news.search(q)

calendar.list(from, to)
calendar.create(title, start, end, description?, location?)
calendar.update(event_id, ...)
calendar.delete(event_id)
```

### Tool name rules

Internal name: `family.verb` (with a `.`). The OpenAI-compat adapter rewrites `.` to `__` on the wire; the Anthropic adapter accepts `.` directly; the ChatGPT-Responses adapter accepts `.` directly.

---

## 5. Web app

Two surfaces. That's all.

| View | What it does |
|---|---|
| **Chat** | conversation with the brain (Phase 1 tool families; Phase 2 adds more). Conversation streams via SSE. Persisted to `data_dir`, not the vault. |
| **Wiki** | tree on the left, rendered markdown center, backlinks right. Edit button → CodeMirror, save through the git guard. Search bar (ripgrep). Mobile-responsive. |

No "modules" sidebar, no Anki / Tasks / News views. Those tools live in their dedicated apps; the brain operates them on the user's behalf during chat.

The Wiki edits use a simple file-level lock during an active edit session — single-user, low contention.

WhatsApp (Phase 3) is the same chat orchestrator behind a different transport — same tools, same persistence layout.

---

## 6. Git guard

Every Obsidian write goes through a single `ObsidianGitGuard` with one async lock across the process. No parallel writes.

Pre-write:

1. `git status --porcelain`. If dirty (Obsidian Sync wrote during a chat), `git add -A && git commit -m "external changes [auto]"` first.
2. `git pull --rebase`.
3. On conflict: `git rebase --abort`, surface a structured error to the chat orchestrator → tool result with `is_error=true`. The user resolves manually, then retries. (LLM-assisted merge is deferred.)

Post-write:

1. `git add <changed paths>`
2. `git commit -m "<descriptive>"` (orchestrator generates the message from the tool name + paths).
3. `git push`. On non-fast-forward, retry once: pull-rebase + push. Second failure surfaces an error.

The lock means a high-level operation (e.g. nightly Organize touching 30 notes) is one commit, not 30.

---

## 7. Nightly Organize

Cron job at 03:00 inside the same container, scheduled by APScheduler. Two responsibilities:

### 7.1 Journal archival

Move `Journal/YYYY-MM-DD.md` files older than today into `Journal/YYYY/MM/YYYY-MM-DD.md`. Done unconditionally, in one commit.

### 7.2 Organize pass

For each note modified since the last run (and every note in `Inbox/`):

- Refactor: grammar, spelling, structure, preserving the user's voice.
- Suggest frontmatter tags.
- Suggest wikilinks to existing notes (using a vault-wide concept index built at job start).
- Suggest a folder move if the note feels misplaced (per `INDEX.md`).
- For `Inbox/` items: propose promotion into the right folder.

Two modes (config flag):

- **dry-run** (default for the first month) — proposals only. The report is the diff. No writes.
- **apply** — writes are committed (one commit per note, descriptive message).

Output: a markdown report (in-memory) emailed via SMTP to the configured address. Not stored in the vault.

The user can also trigger Organize on demand via a `POST /api/organize/run` endpoint (still respects dry-run/apply mode).

---

## 8. Auth, persistence, deployment (kept from earlier work)

- **Auth**: single user, bcrypt hash in `config.yml`, `itsdangerous`-signed session cookies, sessions row in SQLite.
- **SQLite**: `<data_dir>/second-brain.db`. Tables: `chats`, `sessions`, `module_state`, `settings`, `schema_migrations`. Plain `.sql` migrations under `backend/migrations/`, applied by an in-house runner at startup.
- **Docker**: single container, `second-brain serve` reads `config.yml`, runs uvicorn on the configured host/port. Bind-mounts: vault, data dir, config file. Optional SSH key bind-mount for git push.
- **CLI**: `second-brain {serve, migrate, hash-password, chatgpt-login}` with top-level `--config` / `-c`.

---

## 9. Configuration

`config.yml`. Hot-reload not supported — restart the container.

```yaml
app:
  host: 0.0.0.0
  port: 8000
  base_url: http://localhost:8000
  language: fr
  data_dir: /data

auth:
  username: mickael
  password_hash: "$2b$12$…"
  session_secret: "long-random-string"
  session_lifetime_days: 30

llm:
  default: chatgpt-pro
  max_tool_rounds: 10
  providers:
    chatgpt-pro:
      kind: chatgpt
      models: [gpt-5-codex, gpt-5-codex-high]
    openai:
      kind: openai
      base_url: https://api.openai.com/v1
      api_key: sk-…
      models: [gpt-4o-mini, gpt-4o]

obsidian:
  vault_path: /vault
  index_file: INDEX.md
  journal:
    folder: Journal
    archive_template: "{folder}/{year:04d}/{month:02d}/{date}.md"
  git:
    enabled: true
    remote: origin
    branch: main
    ssh_key_path: /run/secrets/obsidian_ssh
    author_name: "Second Brain"
    author_email: "second-brain@local"

organize:
  schedule: "0 3 * * *"          # cron: 03:00 every night
  mode: dry-run                  # dry-run | apply
  modified_since: last_run       # last_run | always_full

smtp:
  enabled: true
  host: smtp.example.com
  port: 587
  starttls: true
  username: brain@example.com
  password: …
  from_address: "Second Brain <brain@example.com>"
  to_address:   "mickael@example.com"
  format: text                   # text | html

logging:
  level: INFO
  format: text
```

Validation at startup fails fast on:
- vault path not a git repo when `obsidian.git.enabled: true`,
- unreachable LLM provider (smoke `models` call),
- SMTP unreachable when `organize.mode: apply` and `smtp.enabled: true`.

---

## 10. Phase plan

Each step ships something usable.

### Phase 1 — Brain core (Obsidian + chat + nightly Organize)

| # | Step | Outcome |
|---|---|---|
| 1 | **Strip + new shell** | Delete the module rail, sub-menus, ComingSoon, InlineChatBox, placeholder modules from the SPA. New nav: Chat / Wiki. Wiki is an empty placeholder. Chat keeps working unchanged. |
| 2 | **Vault foundation + git guard** | `app/vault/` package: read/list/search/write/append/move/delete + the async-lock-protected git guard. |
| 3 | **Wiki read** | `/api/vault/tree`, `/api/vault/note?path=`, `/api/vault/search?q=`. SPA: tree, rendered markdown (wikilinks, embeds), backlinks panel, search bar. |
| 4 | **Wiki edit** | CodeMirror, save via git guard, frontmatter editor, edit-session lock. |
| 5 | **vault.\* + daily.\* + chat.search as LLM tools, INDEX auto-injection, chat persistence out of vault** | Plumb tools to the orchestrator. Read `INDEX.md` and prepend to system prompt. Move chat transcripts from `<vault>/SecondBrain/Chats/` to `<data_dir>/chats/`. Test: *"add 'try X' to Reading/Backlog.md"* works end-to-end. |
| 6 | **Nightly Organize: journal archival** | APScheduler job, journal-archive logic, single commit per night. SMTP send (a heartbeat report at first). |
| 7 | **Nightly Organize: organize pass (dry-run only)** | Per-note proposals, full markdown report, email via SMTP. No writes yet. |
| 8 | **Polish** | Wiki mobile layout, PWA install/offline shell, search UX, error boundaries. |
| 9 | **Organize: apply mode** | Flip the config flag once dry-run reports look clean. |

### Phase 2 — External integrations (user-guided)

| # | Step | Notes |
|---|---|---|
| 10 | `tasks.*` (WebDAV CalDAV) | The user picks the server (Nextcloud Tasks / Baïkal / Radicale / …) and confirms the auth scheme. |
| 11 | `anki.*` (AnkiConnect) | Anki desktop + AnkiConnect on host; container reaches via `host.docker.internal`. |
| 12 | `news.*` (FreshRSS Fever) | Base URL + Fever password from config. |
| 13 | `calendar.*` (CalDAV) | Same server family as tasks usually; user confirms. |

### Phase 3 — WhatsApp

| # | Step | Notes |
|---|---|---|
| 14 | WhatsApp Cloud API webhook + signature verification + allowed-sender check | Routes inbound through the same chat orchestrator; replies via Cloud API. |

---

## 11. Non-goals (explicit)

- Multi-user / RBAC.
- Mirroring tasks, cards, news, emails into the vault.
- Embedding-based search.
- LiteLLM or any all-in-one LLM gateway.
- A separate database server (Postgres / Redis).
- Hot-reload of `config.yml`.
- An SPA "modules" abstraction or per-module dashboards.
- The brain acting as an MCP server (deferred indefinitely).

---

## 12. API surface (Phase 1)

```
POST   /api/auth/login                    { username, password } → set cookie
POST   /api/auth/logout
GET    /api/auth/me

GET    /api/llm/providers                 → [{name, kind, models, default_model, is_default}]

# Chat (unchanged from current implementation)
POST   /api/chat                          SSE stream
GET    /api/chats                         ?q=
GET    /api/chats/{id}
PATCH  /api/chats/{id}                    { title? archived? }
DELETE /api/chats/{id}

# Vault
GET    /api/vault/tree                    → folder tree (paths + types)
GET    /api/vault/note?path=              → { content, frontmatter, backlinks }
PUT    /api/vault/note                    { path, content }     # via git guard
POST   /api/vault/edit/lock               { path }              → { token }
DELETE /api/vault/edit/lock               { path, token }
GET    /api/vault/search?q=               → [{path, snippet}]

# Organize
POST   /api/organize/run                  { mode?: "dry-run"|"apply" } → 202 + job_id
GET    /api/organize/runs                 → [{id, started_at, mode, status}]
GET    /api/organize/runs/{id}            → { ... report markdown }
```

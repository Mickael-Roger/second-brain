<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./assets/logo_black.png">
    <img alt="Second Brain" src="./assets/logo_white.png" width="220">
  </picture>
</p>

# Second Brain

Mono-user self-hosted personal "second brain". See [PROJECT.md](./PROJECT.md) for the full specification.

This repository currently implements **Phase 1** of the roadmap: skeleton with auth, LLM routing (OpenAI-compatible providers), global chat with SSE streaming, and chat persistence as markdown.

## Quick start (development)

```bash
# 1. Backend deps
uv sync

# 2. Generate a password hash and copy config
uv run second-brain hash-password
cp config.example.yml config.yml
# edit config.yml — paste the hash, set an LLM api_key, pick a session_secret

# 3. Initialize the database (runs SQL migrations from backend/migrations/)
mkdir -p data
uv run second-brain migrate

# 4. Run the backend (host/port come from config.yml; auto-applies migrations)
cd backend && uv run second-brain serve --reload

# 5. In another shell, run the frontend dev server
cd frontend
npm install
npm run dev
```

The frontend dev server (Vite) proxies API calls to the backend. Open the URL printed by Vite.

## Quick start (Docker)

```bash
cp config.example.yml config.yml
# edit config.yml as above
mkdir -p data vault
docker compose -f docker-compose.example.yml up --build
```

The container serves the built SPA on port 8000.

## CLI

```bash
uv run second-brain serve                               # start the HTTP server (host/port from config)
uv run second-brain serve --reload                      # dev mode with hot-reload
uv run second-brain hash-password                       # prompt for a password, print bcrypt hash
uv run second-brain migrate                             # apply pending SQL migrations
uv run second-brain chatgpt-login <provider-name>       # OAuth device-flow login for kind=chatgpt
uv run second-brain organize                            # run the nightly Organize job right now
uv run second-brain organize --mode apply --no-email    # apply mode + suppress the SMTP send
```

In Docker, hit the running container:

```bash
docker exec -it second-brain second-brain organize
docker exec -it second-brain second-brain organize --mode apply --no-email
```

All commands accept `--config <path>` (or `-c`) before the subcommand to point
at a config file other than `./config.yml`. The `CONFIG_PATH` env var is also
honored.

```bash
uv run second-brain --config /etc/second-brain/config.yml chatgpt-login chatgpt-pro
```

### ChatGPT Plus / Pro / Team subscription

Add a `kind: chatgpt` entry under `llm.providers` (no `api_key`, no `base_url` —
see [`config.example.yml`](./config.example.yml)). Then run the device-flow
login once. The tokens are persisted to `{data_dir}/chatgpt_oauth/<name>.json`
and refreshed automatically before every request.

The command prints a URL and a one-time user code; visit
<https://auth.openai.com/codex/device>, enter the code, and authorize.

**In Docker** — the data volume is shared, so tokens land at the right path
automatically:

```bash
# one-shot
docker compose -f docker-compose.example.yml run --rm second-brain \
  second-brain chatgpt-login chatgpt-pro

# or, against an already-running container
docker exec -it second-brain second-brain chatgpt-login chatgpt-pro
```

**On the host while the app runs in a container** — `app.data_dir` in
`config.yml` is the *container's* path (typically `/data`). When running the
login from the host, point `--data-dir` at the host directory that's
bind-mounted to the container's data dir:

```bash
# host layout: /data/second-brain/data ↔ container /data
uv run second-brain --config /data/second-brain/config.yml \
  chatgpt-login chatgpt-pro --data-dir /data/second-brain/data
```

The token file lands at `<data-dir>/chatgpt_oauth/chatgpt-pro.json`, which
the running container then sees at `/data/chatgpt_oauth/chatgpt-pro.json` and
refreshes automatically.

**Outside Docker entirely**:

```bash
uv run second-brain chatgpt-login chatgpt-pro
```

## Configuration

See [`config.example.yml`](./config.example.yml). The active config path is `config.yml` in the repo root, or whatever `CONFIG_PATH` points to.

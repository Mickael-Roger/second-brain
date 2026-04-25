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

# 4. Run the backend (also auto-applies pending migrations on startup)
uv run uvicorn app.main:app --reload --app-dir backend

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
uv run second-brain hash-password           # prompt for a password, print bcrypt hash
uv run second-brain migrate                 # apply pending SQL migrations
```

## Configuration

See [`config.example.yml`](./config.example.yml). The active config path is `config.yml` in the repo root, or whatever `CONFIG_PATH` points to.

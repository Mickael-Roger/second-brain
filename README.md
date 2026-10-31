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

## Terminal interface (TUI)

The terminal client is written in **Go** and builds into a standalone binary.
With Go 1.26 or later, build it from the repository root and pass your server URL:

```bash
go -C tui build -o second-brain-tui .
./tui/second-brain-tui --username mickael --images iterm2 https://brain.example.com
```

You can also run directly from source:

```bash
go -C tui run . https://brain.example.com --images iterm2
```

The binary uses the existing API and web credentials. It needs no Python runtime,
local `config.yml`, vault, or database. Reverse-proxy URL prefixes are supported;
passwords and session cookies stay in memory.

It includes Chat (streaming, models, chronological history and image attachments),
Training, News (filters, read/star state, labels, captures and feed/category
management), and Anki (deck statistics, due cards, reviews and note creation).
Use `:chat`, `:news`, `:training` or `:anki` to switch views, or press `0` Chat,
`1` Anki, `2` Training, `3` News. These shortcuts appear in the top-right keyboard dashboard;
numbers remain text while typing. The black/cyan interface uses fixed column
headings, blue row selection, and group counters. Lists start collapsed: Up / Down selects, Enter expands
groups or opens content, `/` runs live fuzzy search, and Escape returns to the
list. Commands and filters share the prompt below the header; Tab completes
commands, and `:news /text` switches section with a filter. In News, `u` toggles
all/unread-only (unread-only by default) and `m` toggles the selected article's read state; unread articles
are bright cyan and read articles gray. Within content views, use Tab / Shift+Tab
to move focus and Ctrl+G to send a message. Chat history is grouped by year, then
month, newest first. Press `c` in Chat to start a conversation or in Anki to open
the note creation popup. Login needs only username/password and Enter. To quit with
Ctrl+C, press it again within three seconds of the confirmation message.

For **WezTerm + tmux**, enable passthrough in `~/.tmux.conf` and use
`--images iterm2` (WezTerm supports the iTerm2 inline-image protocol):

```tmux
set -g allow-passthrough on
```

Images appear in a media pane beside the document, with Previous / Next controls.
Available modes: `auto`, `iterm2`, `sixel`, `halfcell`, `off`. See
[`tui/README.md`](./tui/README.md) for controls, image configuration and tests.

## Anki — web, PWA and terminal

The Anki view opens on decks with review, new, learning and total card counts.
Expand a deck to display due/new cards below it, then select a card to review in
a popup over the same list (Again, Hard, Good, Easy). Multiple decks can remain
expanded. New notes are created in a popup, with deck, front/back, tags and
normal/reversed mode. The web and installed PWA share this responsive view; `c`
opens note creation on desktop and in the TUI.

All clients call the authenticated Second Brain API. The backend forwards deck
statistics, card searches/rendering, reviews, note creation and media requests
to AnkiConnect. Anki remains the source of truth. See [ANKI.md](./ANKI.md) for
configuration, endpoint contracts, counter semantics and deployment checks.

## Nix

The repository ships a Nix flake and equivalent non-flake expressions. The
server package builds and embeds the frontend; the Go TUI is a separate,
standalone package. The frontend uses `package-lock.json` integrity hashes and
the TUI has its Go dependencies under `tui/vendor/`, so their package builds do
not download language dependencies.

### Flake

With Nix flakes enabled:

```bash
# Build both artifacts without replacing either result symlink.
nix build .#server --out-link result-server
nix build .#tui --out-link result-tui

# Run the server. Pass --config before the command as usual.
result-server/bin/second-brain --config /etc/second-brain/config.yml serve

# Or run the TUI against a server.
nix run .#tui -- --images iterm2 https://brain.example.com

# Development shell: Go, Node, Python and uv.
nix develop
```

The default package/app is the server. The flake also exports an overlay:

```nix
{
  inputs.second-brain.url = "github:YOUR-ORG/second-brain";

  outputs = { nixpkgs, second-brain, ... }: {
    nixpkgs.overlays = [ second-brain.overlays.default ];
  };
}
```

This makes `pkgs.second-brain` and `pkgs.second-brain-tui` available.

### Non-flake

For channels or a pinned `NIX_PATH`, the same packages are exposed from
`default.nix`:

```bash
nix-build -A server
nix-build -A tui

./result/bin/second-brain-tui https://brain.example.com
nix-shell
```

Pass another nixpkgs explicitly when needed:

```bash
nix-build -A server --arg pkgs 'import /path/to/nixpkgs {}'
```

### NixOS server

Import the module and point it to a **runtime** configuration file. `configFile`
is deliberately a string, not a Nix path: it can therefore reference a
root-owned secret managed by sops-nix, agenix, or another secret manager without
copying API keys or password hashes into `/nix/store`.

```nix
{ inputs, ... }:
{
  imports = [ inputs.second-brain.nixosModules.default ];

  services.second-brain = {
    enable = true;
    configFile = "/run/secrets/second-brain/config.yml";
    dataDir = "/var/lib/second-brain";
    vaultPath = "/srv/obsidian";
    openFirewall = true;
    port = 8000;
  };
}
```

Set `app.data_dir` and `obsidian.vault_path` in this config file to the same
values as `dataDir` and `vaultPath`. The module creates the data directory and
runs a hardened `second-brain` systemd service as the `second-brain` user. Make
the vault and runtime config readable/writable by that user as appropriate;
the module does not create or take ownership of the vault. Use
`services.second-brain.extraEnvironment` for non-secret environment overrides.

## ChatGPT Plus / Pro / Team subscription

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

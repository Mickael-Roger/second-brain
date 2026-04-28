"""FastAPI application entrypoint.

Wires routers, serves the built SPA from `app/static/` (with HTML5 history
fallback), and configures CORS for the dev Vite server.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import auth as auth_api
from app.api import chat as chat_api
from app.api import news as news_api
from app.api import vault as vault_api
from app.config import get_settings
from app.db.connection import open_connection
from app.db.migrations import run_migrations
from app.jobs import shutdown_scheduler, start_scheduler

# NOTE: bind host / port are NOT read here — uvicorn binds the socket before
# importing this module. Use `second-brain serve` (see app.cli) to start the
# server with the host/port from config.yml.


def _configure_logging() -> None:
    s = get_settings()
    level = getattr(logging, s.logging.level)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    _configure_logging()
    log = logging.getLogger(__name__)
    conn = open_connection()
    try:
        applied = run_migrations(conn)
        if applied:
            log.info("Applied %d migration(s)", applied)
    finally:
        conn.close()
    start_scheduler()

    # Bootstrap the Anki collection.anki2 if anki is enabled. Idempotent
    # (a no-op when the file already has Anki's `col` table).
    settings_for_anki = get_settings()
    if settings_for_anki.anki.enabled:
        try:
            from app.anki import ensure_collection

            ensure_collection()
        except Exception:
            log.exception("anki: ensure_collection failed at startup (non-fatal)")

    # Kick off an immediate news backfill in the background so the
    # app starts populating articles right away — including all
    # unread items, regardless of age — without waiting for the
    # first cron tick (5 minutes by default).
    settings = get_settings()
    if settings.news.enabled and settings.news.sources.freshrss is not None:
        import asyncio

        async def _initial_news_fetch() -> None:
            from app.news.service import fetch_all_sources, thirty_days_ago_ts

            try:
                log.info("startup news fetch: starting (range=30d + all unread)")
                await fetch_all_sources(from_ts=thirty_days_ago_ts())
                log.info("startup news fetch: done")
            except Exception:
                log.exception("startup news fetch failed (non-fatal)")

        asyncio.create_task(_initial_news_fetch())

    try:
        yield
    finally:
        shutdown_scheduler()


def create_app() -> FastAPI:
    # Eagerly load settings so a malformed config fails at process start
    # rather than on the first request.
    get_settings()
    app = FastAPI(title="Second Brain", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_api.router)
    app.include_router(chat_api.router)
    app.include_router(vault_api.router)
    app.include_router(news_api.router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "app": "second-brain"}

    # Serve the built SPA (mounted at the very end so /api/* wins).
    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir() and (static_dir / "index.html").is_file():
        app.mount(
            "/assets",
            StaticFiles(directory=str(static_dir / "assets")),
            name="assets",
        )

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> Response:
            # /api/* should never reach here, but be defensive.
            if full_path.startswith("api/"):
                raise StarletteHTTPException(status_code=404)
            file = static_dir / full_path
            if full_path and file.is_file():
                return FileResponse(file)
            return FileResponse(static_dir / "index.html")

    return app


app = create_app()

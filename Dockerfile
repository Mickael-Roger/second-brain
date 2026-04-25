# syntax=docker/dockerfile:1.7

# Stage 1 — build SPA
FROM node:20-alpine AS spa
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY frontend/ ./
RUN npm run build

# Stage 2 — runtime
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
        git openssh-client ca-certificates curl tini ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'

# Non-root runtime user. UID/GID 1000 is the conventional first-user id on
# Linux desktops; bind-mounting host directories owned by your user (vault,
# data dir, SSH key) means files written by the container come back owned
# by you, not root. Override at build time with `--build-arg APP_UID=…` if
# your host user has a different uid.
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --system --gid "${APP_GID}" brain && \
    useradd  --system --uid "${APP_UID}" --gid "${APP_GID}" \
             --home-dir /home/brain --create-home --shell /bin/bash brain

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml ./
COPY uv.lock* ./
RUN uv sync --no-dev

COPY backend/ ./backend/
COPY --from=spa /app/dist ./backend/app/static/

# /data is the bind-mount target for the SQLite DB + chat transcripts; pre-
# create it owned by the runtime user so the first boot works even when the
# host hasn't pre-created /data with the right ownership.
RUN mkdir -p /data && chown -R brain:brain /app /data

ENV CONFIG_PATH=/config/config.yml \
    DATA_DIR=/data \
    PYTHONPATH=/app/backend \
    PATH="/app/.venv/bin:${PATH}"

USER brain

EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
# Bind host/port come from config.yml (`app.host`, `app.port`); EXPOSE 8000 is
# only the documented default. Override at the orchestrator level if you
# change the configured port and need the published port to match.
CMD ["second-brain", "serve"]

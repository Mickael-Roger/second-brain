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
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml ./
COPY uv.lock* ./
RUN uv sync --no-dev

COPY backend/ ./backend/
COPY --from=spa /app/dist ./backend/app/static/

ENV CONFIG_PATH=/config/config.yml \
    DATA_DIR=/data \
    PYTHONPATH=/app/backend

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
# Bind host/port come from config.yml (`app.host`, `app.port`); EXPOSE 8000 is
# only the documented default. Override at the orchestrator level if you change
# the configured port and need the published port to match.
CMD ["second-brain", "serve"]

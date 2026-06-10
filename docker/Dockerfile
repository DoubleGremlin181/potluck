# syntax=docker/dockerfile:1

FROM node:22-slim AS web
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim AS app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock README.md LICENSE hatch_build.py ./
COPY src/ src/
RUN uv sync --frozen --no-dev --no-editable
COPY --from=web /build/dist web/dist

ENV PATH="/app/.venv/bin:$PATH" \
    POTLUCK_WEB_DIST=/app/web/dist \
    POTLUCK_DB_PATH=/data/potluck.db
VOLUME /data
EXPOSE 8765
# 0.0.0.0 inside the container; publish as 127.0.0.1:8765:8765 on the host —
# Potluck is localhost-only by design (no auth in v1).
CMD ["potluck", "serve", "--host", "0.0.0.0", "--no-browser"]

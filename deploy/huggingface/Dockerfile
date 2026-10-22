# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------------------
# Single-container DocPilot for a free Hugging Face Space (Docker SDK, CPU basic).
# One container runs Postgres + pgvector, Redis, the API with its in-process ingestion
# worker, and the UI on port 7860. Kafka and MinIO are replaced by the in-process event bus
# and local file storage, which the app supports for exactly this kind of deployment.
# The deploy workflow copies this file to the root of the Space repository.
# ---------------------------------------------------------------------------------------
ARG PYTHON_IMAGE=python:3.12-slim

FROM node:22-alpine AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca_bundle; fi; \
    npm ci --no-audit --no-fund
COPY ui ./
COPY eval/corpus_src /eval/corpus_src
RUN npm run build

FROM ${PYTHON_IMAGE} AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    pip install --no-cache-dir uv==0.11.32
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export SSL_CERT_FILE=/run/secrets/ca_bundle; fi; \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export SSL_CERT_FILE=/run/secrets/ca_bundle; fi; \
    uv sync --frozen --no-dev --no-editable
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then \
      export SSL_CERT_FILE=/run/secrets/ca_bundle REQUESTS_CA_BUNDLE=/run/secrets/ca_bundle; \
    fi; \
    /app/.venv/bin/python -m docpilot.infra.embeddings.model_download --cache-dir /models

FROM ${PYTHON_IMAGE}
# Postgres + pgvector and Redis from Debian; no default cluster (start.sh runs initdb).
RUN mkdir -p /etc/postgresql-common \
    && echo "create_main_cluster = false" > /etc/postgresql-common/createcluster.conf \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql redis-server \
    && apt-get install -y --no-install-recommends "postgresql-$(ls /usr/lib/postgresql)-pgvector" \
    && rm -rf /var/lib/apt/lists/*
# Spaces run the container as UID 1000.
RUN useradd --create-home --uid 1000 user
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}" \
    HF_HUB_OFFLINE=1 \
    EMBEDDING_MODEL_PATH=/models/sentence-transformers--all-MiniLM-L6-v2 \
    APP_ENV=production \
    PORT=7860 \
    EVENT_BUS=memory \
    STORAGE_BACKEND=local \
    LOCAL_STORAGE_PATH=/tmp/docpilot/blobs \
    REDIS_URL=redis://127.0.0.1:6379/0 \
    OTEL_ENABLED=false
WORKDIR /app
COPY --from=builder --chown=user:user /app/.venv /app/.venv
COPY --from=builder --chown=user:user /models /models
COPY --from=ui --chown=user:user /ui/dist ./ui/dist
COPY --chown=user:user alembic.ini ./
COPY --chown=user:user migrations ./migrations
COPY --chown=user:user start.sh ./
USER user
EXPOSE 7860
CMD ["/app/start.sh"]

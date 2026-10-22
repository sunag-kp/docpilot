# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------------------
# DocPilot API + worker image (same image, different command).
#   docker build -t docpilot .
# Behind a TLS-intercepting corporate proxy, pass its CA bundle as a build secret:
#   docker build --secret id=ca_bundle,src=/path/to/ca.pem -t docpilot .
# ---------------------------------------------------------------------------------------
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    pip install --no-cache-dir uv==0.11.32
COPY pyproject.toml uv.lock README.md ./
# Dependencies first: this layer is cached until the lockfile changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export SSL_CERT_FILE=/run/secrets/ca_bundle; fi; \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable
# Bake the local embedding model into the image: containers start with no network access.
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then \
      export SSL_CERT_FILE=/run/secrets/ca_bundle REQUESTS_CA_BUNDLE=/run/secrets/ca_bundle; \
    fi; \
    /app/.venv/bin/python -m docpilot.infra.embeddings.model_download --cache-dir /models

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}" \
    HF_HUB_OFFLINE=1 \
    EMBEDDING_MODEL_PATH=/models/sentence-transformers--all-MiniLM-L6-v2
RUN groupadd --system --gid 10001 docpilot \
    && useradd --system --uid 10001 --gid docpilot --home-dir /app --shell /usr/sbin/nologin docpilot
WORKDIR /app
COPY --from=builder --chown=docpilot:docpilot /app/.venv /app/.venv
COPY --from=builder --chown=docpilot:docpilot /models /models
COPY --chown=docpilot:docpilot alembic.ini ./
COPY --chown=docpilot:docpilot migrations ./migrations
USER docpilot
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]
CMD ["docpilot-api"]

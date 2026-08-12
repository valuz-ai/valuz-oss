# syntax=docker/dockerfile:1

# Self-contained Valuz OSS backend image. Build from the repository root:
#
#   docker build -f backend/Dockerfile -t valuz-oss-backend .

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:24-bookworm-slim

FROM ${NODE_IMAGE} AS node-runtime

FROM ${PYTHON_IMAGE} AS dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && pip install uv==0.7.12

WORKDIR /build
COPY backend/pyproject.toml backend/uv.lock ./

# Application source is supplied through PYTHONPATH in the runtime stage. This
# keeps dependency installation cached when only Valuz source files change.
RUN uv sync --frozen --no-dev --no-install-project

FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="valuz-oss-backend" \
      org.opencontainers.image.description="Valuz OSS FastAPI backend"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONPATH=/app/backend:/app/backend/kernel \
    HOME=/data/home \
    XDG_CONFIG_HOME=/data/home/.config \
    XDG_CACHE_HOME=/data/home/.cache \
    VALUZ_DATA_DIR=/data/valuz \
    VALUZ_LOG_FILE_PATH=/data/valuz/logs/backend.log \
    VALUZ_USER_PROJECT_ROOT=/workspace \
    VALUZ_USER_SKILLS_DIR=/data/agents/skills \
    VALUZ_BACKUP_ROOT=/data/backups \
    VALUZ_DISABLE_LOGIN_PATH=1 \
    VALUZ_NODE_PATH=/usr/local/bin/node \
    VALUZ_CDT_ENTRY=/app/backend/vendor/chrome-devtools-mcp/node_modules/chrome-devtools-mcp/build/src/bin/chrome-devtools.js

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        openssh-client \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 valuz \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /data/home valuz

# Node.js is used by stdio MCP connectors and the bundled browser engine. Copy
# it from the official Node image while keeping Python 3.12 as the image base.
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

COPY --from=dependencies /opt/venv /opt/venv

WORKDIR /app
COPY --chown=valuz:valuz backend/pyproject.toml backend/uv.lock ./backend/
COPY --chown=valuz:valuz backend/alembic ./backend/alembic
COPY --chown=valuz:valuz backend/kernel ./backend/kernel
COPY --chown=valuz:valuz backend/plugins ./backend/plugins
COPY --chown=valuz:valuz backend/valuz_agent ./backend/valuz_agent
COPY --chown=valuz:valuz backend/vendor ./backend/vendor
COPY --chown=valuz:valuz i18n ./i18n

# Keep the browser/MCP JavaScript dependency reproducible and available without
# downloading npm packages after the container starts.
RUN npm ci \
        --omit=dev \
        --no-audit \
        --no-fund \
        --loglevel=error \
        --prefix /app/backend/vendor/chrome-devtools-mcp \
    && npm cache clean --force \
    && mkdir -p /data/valuz /data/agents/skills /data/backups /workspace \
    && chown -R valuz:valuz /data /workspace

USER valuz

VOLUME ["/data", "/workspace"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/v1/system/status', timeout=3)" || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "valuz_agent", "--host", "0.0.0.0", "--port", "8000"]

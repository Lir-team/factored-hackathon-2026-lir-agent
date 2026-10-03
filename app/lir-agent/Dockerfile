# Lir agent HTTP API for Cloud Run.
#
# Build from app/ so the decision-layer path dependency is in the context:
#   docker build -f lir-agent/Dockerfile -t lir-agent app/

ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.12

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---- build: resolve the locked dependencies into a virtualenv ---------------------------
FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /src/lir-agent
COPY decision-layer /src/decision-layer
COPY lir-agent/pyproject.toml lir-agent/uv.lock ./
# Dependencies first: this layer is reused until uv.lock changes.
RUN uv sync --locked --no-dev --no-install-project --no-editable
COPY lir-agent/README.md ./
COPY lir-agent/src ./src
RUN uv sync --locked --no-dev --no-editable

# ---- runtime: only the virtualenv, as a non-root user -----------------------------------
FROM python:${PYTHON_VERSION}-slim
RUN useradd --create-home --uid 10001 lir
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    ENVIRONMENT=production \
    # Cloud Run mounts the data bucket here; the audit log goes to writable /tmp.
    DATA_DIR=/mnt/data \
    AUDIT_PATH=/tmp/lir/audit.jsonl
USER lir
WORKDIR /home/lir
EXPOSE 8080
CMD ["lir-agent-api"]

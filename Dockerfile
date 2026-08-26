# Kairos backend — FastAPI + the agent pipeline in one image.
#
# One container serves the API and executes runs; a run is triggered either
# by a person (the dashboard's button) or by EventBridge Scheduler calling
# the same endpoint. SQLite and the daily spend ledger live on a mounted
# volume (/data), which is why this deploys as a single-task ECS service —
# see infra/README.md before scaling anything.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_SYNC=1

# Dependencies first, so a code change does not re-resolve the lockfile.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# The pipeline, the API, and the data the API seeds from on startup.
COPY agent ./agent
COPY api ./api
COPY scripts ./scripts
COPY data ./data

# Runtime state goes to the volume, never the container filesystem.
ENV KAIROS_DB_URL=sqlite:////data/kairos.db \
    KAIROS_STATE_DIR=/data/state

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

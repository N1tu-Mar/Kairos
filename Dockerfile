# Kairos backend — FastAPI + the agent pipeline in one image.
#
# One container serves the API and executes runs; a run is triggered either
# by a person (the dashboard's button) or by EventBridge Scheduler calling
# the same endpoint. SQLite, the run leases and the spend ledger live on a
# mounted volume (/data), which is why this deploys as a single-task ECS
# service — see infra/README.md before scaling anything.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_SYNC=1 \
    PYTHONDONTWRITEBYTECODE=1

# Dependencies first, so a code change does not re-resolve the lockfile.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# The pipeline, the API, the migrations that own the schema, and the data
# the API seeds from on startup.
COPY agent ./agent
COPY api ./api
COPY scripts ./scripts
COPY data ./data
COPY migrations ./migrations
COPY alembic.ini ./

# Runtime state goes to the volume, never the container filesystem.
ENV KAIROS_DB_URL=sqlite:////data/kairos.db \
    KAIROS_STATE_DIR=/data/state

# Run as a real user, not root. UID 1000 matches the EFS access point in
# infra/main.tf — the volume is created owned by 1000:1000 with mode 700, so
# the container user and the file owner have to be the same number or the
# task starts and then cannot write its own database.
#
# The virtualenv and /app are handed over too: `uv run` resolves the
# environment at start and needs to read all of it.
RUN groupadd --gid 1000 kairos \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin kairos \
    && chown -R 1000:1000 /app
USER 1000:1000

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

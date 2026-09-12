"""Profile the HTTP path: POST /founders/{id}/runs -> job -> dashboard polling.

Dry-run stubs replace SubAgents.build so nothing reaches Bedrock. Counts SQL
per request and measures the executor's run on the API event loop.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.chdir(REPO)
tmp = tempfile.mkdtemp()
os.environ.update(
    BEDROCK_MODEL_REASONING="[DRY-RUN]no-model",
    BEDROCK_MODEL_CLASSIFY="[DRY-RUN]no-model",
    KAIROS_DAILY_USD_CAP="0",
    KAIROS_ENV="local",
    KAIROS_ALLOW_OPEN_API="1",
    KAIROS_DB_URL=f"sqlite:///{tmp}/api.db",
    KAIROS_STATE_DIR=f"{tmp}/state",
    KAIROS_MANUAL_RUNS_PER_HOUR="100",
)
for k in ("KAIROS_API_TOKEN", "KAIROS_CREDENTIALS_FILE", "KAIROS_AUTH_MODE", "KAIROS_SUPABASE_ISSUER"):
    os.environ[k] = ""

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

import api.jobs as jobs  # noqa: E402
from agent.dryrun import build_stub_agents  # noqa: E402
from agent.runtime import SubAgents  # noqa: E402

SubAgents.build = classmethod(lambda cls: None)
_orig_ctx = jobs.new_run_context


def _ctx(**kw):
    ctx = _orig_ctx(**kw)
    ctx.agents = build_stub_agents(ctx)
    return ctx


jobs.new_run_context = _ctx

from api.main import app  # noqa: E402

SQL = Counter()


def main():
    out = {}
    with TestClient(app) as client:
        engine = app.state.repo.engine

        @event.listens_for(engine, "after_cursor_execute")
        def _count(conn, cursor, statement, params, context, executemany):
            SQL[statement.split()[0].upper()] += 1

        def measure(label, fn, n=20):
            SQL.clear()
            t = time.perf_counter()
            for _ in range(n):
                r = fn()
            dt = (time.perf_counter() - t) / n
            out[label] = {"status": r.status_code, "ms_per_request": round(dt * 1000, 2),
                          "sql_per_request": round(sum(SQL.values()) / n, 1), "bytes": len(r.content)}
            return r

        SQL.clear()
        t = time.perf_counter()
        r = client.post("/founders/founder_demo/runs", json={"use_demo_catalog": True, "include_grants_gov": False, "idempotency_key": "bench-1"})
        out["POST runs"] = {"status": r.status_code, "ms": round((time.perf_counter() - t) * 1000, 2), "sql": sum(SQL.values())}
        job_id = r.json()["job_id"]
        polls = 0
        t = time.perf_counter()
        while True:
            polls += 1
            j = client.get(f"/founders/founder_demo/jobs/{job_id}").json()
            if j["job"]["status"] not in ("queued", "running"):
                break
            time.sleep(0.01)
        out["job_to_terminal_s"] = round(time.perf_counter() - t, 3)
        out["job_status"] = j["job"]["status"]
        out["polls_until_terminal"] = polls
        measure("GET job (poll)", lambda: client.get(f"/founders/founder_demo/jobs/{job_id}"))
        measure("GET inbox", lambda: client.get("/founders/founder_demo/inbox"))
        measure("GET runs", lambda: client.get("/founders/founder_demo/runs?limit=50"))
        measure("GET runs/latest/skips", lambda: client.get("/founders/founder_demo/runs/latest/skips"))
        measure("GET drafts", lambda: client.get("/founders/founder_demo/drafts"))
        measure("POST runs (idempotent replay)", lambda: client.post("/founders/founder_demo/runs", json={"use_demo_catalog": True, "include_grants_gov": False, "idempotency_key": "bench-1"}))
    print(json.dumps(out, indent=1))


main()

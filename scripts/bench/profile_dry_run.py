"""Offline profiler for the Kairos dry-run pipeline. No Bedrock, no network.

    uv run python scripts/bench/profile_dry_run.py --scenario demo|seed|seed_bigkb [--repeat N]

Prints one JSON object. Each scenario runs in its own process so peak RSS
is attributable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import statistics
import sys
import tempfile
import time
import tracemalloc
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

os.environ.update(
    BEDROCK_MODEL_REASONING="[DRY-RUN]no-model",
    BEDROCK_MODEL_CLASSIFY="[DRY-RUN]no-model",
    KAIROS_DAILY_USD_CAP="0",
    KAIROS_ENV="local",
    KAIROS_ENABLE_BROWSER="false",
)
for k in ("KAIROS_API_TOKEN", "KAIROS_CREDENTIALS_FILE", "KAIROS_MAX_RUN_TOKENS", "KAIROS_MAX_ASSESSMENTS"):
    os.environ[k] = ""

from pydantic import BaseModel  # noqa: E402
from sqlalchemy import event  # noqa: E402

# ── Pydantic serialisation counters ────────────────────────────────────────
PYD = {"dump_json": Counter(), "dump_json_bytes": Counter(), "validate_json": Counter(), "validate_json_bytes": Counter()}
_orig_dump = BaseModel.model_dump_json
_orig_validate = BaseModel.model_validate_json.__func__


def _dump(self, *a, **kw):
    out = _orig_dump(self, *a, **kw)
    name = type(self).__name__
    PYD["dump_json"][name] += 1
    PYD["dump_json_bytes"][name] += len(out)
    return out


def _validate(cls, data, *a, **kw):
    PYD["validate_json"][cls.__name__] += 1
    PYD["validate_json_bytes"][cls.__name__] += len(data)
    return _orig_validate(cls, data, *a, **kw)


BaseModel.model_dump_json = _dump
BaseModel.model_validate_json = classmethod(_validate)

from agent import scout  # noqa: E402
from agent.budget import DailyLedger, RunBudget  # noqa: E402
from agent.config import settings  # noqa: E402
from agent.dryrun import StubAgent, build_stub_agents  # noqa: E402
from agent.models import FounderProfile, KnowledgeChunk  # noqa: E402
from agent.scout import new_run_context, run_once  # noqa: E402
from agent.tools.discovery import SeedCatalog  # noqa: E402
from api.jobs import load_forms  # noqa: E402
from api.repository import SqliteRepository  # noqa: E402

PHASE = ["setup"]
IN_RUN = [False]
WINDOW = [0.0, 0.0]
STAGE_S: dict[str, float] = defaultdict(float)
SQL = {"count": Counter(), "time": defaultdict(float), "slow": []}


def instrument_engine(engine):
    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, params, context, executemany):
        conn.info.setdefault("t0", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, params, context, executemany):
        dt = time.perf_counter() - conn.info["t0"].pop()
        verb = statement.strip().split()[0].upper()
        table = next((w for w in statement.replace('"', " ").split() if w.islower() and "_" not in w[:1] and w in TABLES), "?")
        key = f"{PHASE[0]}:{verb}:{table}"
        SQL["count"][key] += 1
        SQL["time"][key] += dt
        SQL["slow"].append((dt, PHASE[0], statement[:90]))


TABLES = {"profiles", "runs", "inbox", "opportunities", "jobs", "drafts", "answers", "eligibility_questions", "founder_members"}


def timed_toolset(orig):
    def build(ctx, sources):
        tools = orig(ctx, sources)
        wrapped = []
        for t in tools:
            wrapped.append(_Wrap(t))
        return wrapped
    return build


class _Wrap:
    def __init__(self, tool):
        self.tool = tool
        self.tool_name = tool.tool_name

    def __call__(self, *a, **kw):
        PHASE[0] = self.tool_name
        t0 = time.perf_counter()
        out = self.tool(*a, **kw)
        if asyncio.iscoroutine(out) or hasattr(out, "__await__"):
            async def _await():
                try:
                    return await out
                finally:
                    STAGE_S[self.tool_name] += time.perf_counter() - t0
                    PHASE[0] = "orchestrator"
            return _await()
        STAGE_S[self.tool_name] += time.perf_counter() - t0
        PHASE[0] = "orchestrator"
        return out


scout.build_toolset = timed_toolset(scout.build_toolset)

import agent.eligibility_clarifications as ec  # noqa: E402

_orig_resolve = ec.resolve_founder_answers
_orig_persist_q = ec.persist_plausible_questions


async def _resolve(ctx, **kw):
    PHASE[0] = "resolve_founder_answers"
    t0 = time.perf_counter()
    try:
        return await _orig_resolve(ctx, **kw)
    finally:
        STAGE_S["resolve_founder_answers"] += time.perf_counter() - t0
        PHASE[0] = "orchestrator"


def _persist_q(ctx):
    PHASE[0] = "persist_questions"
    t0 = time.perf_counter()
    try:
        return _orig_persist_q(ctx)
    finally:
        STAGE_S["persist_questions"] += time.perf_counter() - t0
        PHASE[0] = "orchestrator"


ec.resolve_founder_answers = _resolve
ec.persist_plausible_questions = _persist_q


class GeneratingDrafter(StubAgent):
    """Answers every askable field as GENERATED citing the first chunk, so the
    Auditor prompt is exercised. Measurement only."""

    def respond(self, output_model, prompt):
        import re

        ids = re.findall(r"^- ([a-z0-9_]+): ", prompt, flags=re.M)
        chunk = re.search(r"^\[([^\]]+)\] \(from ", prompt, flags=re.M)
        cid = chunk.group(1) if chunk else "missing"
        return output_model(fields=[
            {"field_id": i, "status": "GENERATED", "answer": "We ran a pilot.", "provenance_chunk_ids": [cid]}
            for i in ids
        ])


TOPICS = [
    ("problem", "Students book lab microscopes on paper sign-up sheets and lose hours to double bookings."),
    ("solution", "LabQueue is a web scheduler that syncs equipment calendars and sends reminders."),
    ("traction", "A six-week pilot with 40 students stopped 12 double-bookings in one building."),
    ("team", "The founding team is two undergraduates studying computer science and materials science."),
    ("market", "Every research university runs shared instrument cores that need scheduling."),
    ("revenue", "Departments pay an annual license per building; pilots are free for one semester."),
    ("competition", "Existing tools are spreadsheets or enterprise LIMS systems that cost too much."),
    ("impact", "Less idle equipment time means more student research hours per dollar spent."),
    ("milestones", "Next milestone is expanding to three buildings and integrating card-swipe access."),
    ("budget", "Funds cover hosting, a part-time developer stipend and hardware badge readers."),
]


def big_kb_profile(base: FounderProfile, n: int) -> FounderProfile:
    chunks = []
    for i in range(n):
        topic, text = TOPICS[i % len(TOPICS)]
        filler = " ".join(f"note{i}_{j}" for j in range(40))
        chunks.append(KnowledgeChunk(chunk_id=f"kb_{topic}_{i}", text=f"[DEMO] {text} {filler}", source=f"notes.md#{topic}-{i}"))
    return base.model_copy(update={"knowledge_base": chunks})


async def one(scenario: str, db_dir: str):
    PHASE[0] = "setup"
    t_setup = time.perf_counter()
    repo = SqliteRepository(f"sqlite:///{db_dir}/kairos.db")
    instrument_engine(repo.engine)
    profile = FounderProfile.model_validate_json((REPO / "data/demo_founder.json").read_text())
    if scenario.endswith("bigkb"):
        profile = big_kb_profile(profile, 80)
    repo.save_profile(profile)
    ctx = new_run_context(
        profile=profile,
        repo=repo,
        budget=RunBudget(max_run_tokens=250_000, max_assessments=25, daily_usd_cap=0.0, ledger=DailyLedger(Path(db_dir))),
    )
    ctx.forms = load_forms()
    agents = build_stub_agents(ctx)
    if scenario.endswith("bigkb"):
        agents.drafter = GeneratingDrafter()
    ctx.agents = agents
    is_demo = scenario.startswith("demo")
    catalog = "opportunities.demo.json" if is_demo else "opportunities.seed.json"
    sources = [SeedCatalog(settings().data_dir / catalog, allow_unverified=is_demo)]
    STAGE_S["setup"] += time.perf_counter() - t_setup

    IN_RUN[0] = True
    t0 = time.perf_counter()
    WINDOW[0] = t0
    report = await run_once(ctx, sources)
    total = time.perf_counter() - t0
    WINDOW[1] = time.perf_counter()
    IN_RUN[0] = False
    STAGE_S["persist_and_orchestration"] = total - sum(v for k, v in STAGE_S.items() if k != "setup")

    def prompt_stats(agent):
        ps = getattr(agent, "prompts", [])
        b = [len(p.encode()) for p in ps]
        return {"calls": len(b), "bytes_total": sum(b), "bytes_max": max(b, default=0), "est_tokens_total": sum(b) // 4}

    return {
        "run_once_s": total,
        "report": {"scanned": report.scanned, "filtered_out": report.filtered_out, "judged": report.judged,
                   "surfaced": report.surfaced, "halted": report.halted_reason,
                   "drafts": {k: (d.status, d.gate_result.failed_check if d.gate_result else None) for k, d in ctx.drafts.items()}},
        "prompts": {n: prompt_stats(getattr(agents, n)) for n in ("assessor", "drafter", "auditor")},
        "kb_chunks": len(ctx.kb.chunks),
    }


async def lag_probe(scenario, db_dir):
    """Max event-loop stall while the run executes on the same loop."""
    spans = []
    stop = asyncio.Event()

    async def beat():
        while not stop.is_set():
            t = time.perf_counter()
            await asyncio.sleep(0.001)
            end = time.perf_counter()
            spans.append((t, end))

    task = asyncio.create_task(beat())
    await asyncio.sleep(0)
    res = await one(scenario, db_dir)
    stop.set()
    await task
    lags = [e - t - 0.001 for t, e in spans if e > WINDOW[0] and t < WINDOW[1]]
    lags.append(WINDOW[1] - WINDOW[0]) if not lags else None
    res["loop_max_stall_ms"] = round(max(lags, default=0) * 1000, 2)
    res["loop_stalls_over_10ms"] = sum(1 for x in lags if x > 0.010)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="demo")
    ap.add_argument("--repeat", type=int, default=5)
    args = ap.parse_args()
    tracemalloc.start()
    runs = []
    for i in range(args.repeat):
        STAGE_S.clear(); SQL["count"].clear(); SQL["time"].clear(); SQL["slow"].clear()
        for c in PYD.values():
            c.clear()
        with tempfile.TemporaryDirectory() as d:
            res = asyncio.run(lag_probe(args.scenario, d))
        res["stage_s"] = dict(STAGE_S)
        runs.append(res)
    last = runs[-1]
    out = {
        "scenario": args.scenario,
        "repeat": args.repeat,
        "run_once_s_median": statistics.median(r["run_once_s"] for r in runs),
        "run_once_s_all": [round(r["run_once_s"], 4) for r in runs],
        "stage_s_median": {k: round(statistics.median(r["stage_s"].get(k, 0) for r in runs), 4) for k in last["stage_s"]},
        "loop_max_stall_ms_median": statistics.median(r["loop_max_stall_ms"] for r in runs),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024), 1),
        "tracemalloc_peak_mb": round(tracemalloc.get_traced_memory()[1] / 1e6, 1),
        "report": last["report"],
        "kb_chunks": last["kb_chunks"],
        "prompts": last["prompts"],
        "pydantic_last_run": {
            "dump_json_calls": sum(PYD["dump_json"].values()),
            "dump_json_bytes": sum(PYD["dump_json_bytes"].values()),
            "validate_json_calls": sum(PYD["validate_json"].values()),
            "validate_json_bytes": sum(PYD["validate_json_bytes"].values()),
            "dump_by_model": dict(PYD["dump_json"].most_common(8)),
            "dump_bytes_by_model": dict(PYD["dump_json_bytes"].most_common(8)),
            "validate_by_model": dict(PYD["validate_json"].most_common(8)),
        },
        "sql_last_run": {
            "total_queries": sum(SQL["count"].values()),
            "total_ms": round(sum(SQL["time"].values()) * 1000, 2),
            "by_phase_verb_table": {k: [v, round(SQL["time"][k] * 1000, 2)] for k, v in SQL["count"].most_common(20)},
            "slowest": [[round(t * 1000, 2), p, s] for t, p, s in sorted(SQL["slow"], reverse=True)[:6]],
        },
    }
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()

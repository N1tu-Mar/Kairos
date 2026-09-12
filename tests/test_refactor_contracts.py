"""Characterization tests pinned before the API/jobs/CLI refactor.

They describe what the code does today, not what it should do. A refactor
that moves code around must leave every assertion here unchanged; a change
that intends to alter behavior has to update this file on purpose.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.tools.campus as campus_module
import api.jobs as api_jobs
from agent.models import RunJob
from agent.scraping.agent import GENERAL_LANE, UNIVERSITY_LANE
from agent.tools.campus import CampusDiscoverySource
from agent.tools.discovery import GrantsGovSource, SeedCatalog
from scripts import form_coverage, run_scout
from tests.factories import profile

CONTRACT = Path(__file__).parent / "fixtures" / "api_contract.json"


def _contract(app) -> dict:
    routes = sorted(
        [r.path, sorted(r.methods), getattr(r, "status_code", None), r.name]
        for r in app.routes
        if hasattr(r, "methods")
    )
    # Round-trip so tuples and lists compare the way the fixture stores them.
    return json.loads(json.dumps({"openapi": app.openapi(), "routes": routes}))


def test_http_contract_is_unchanged():
    """Paths, methods, parameters, bodies, response models and status codes.

    Regenerate the fixture only for an intended contract change.
    """
    from api.main import app

    app.openapi_schema = None
    assert _contract(app) == json.loads(CONTRACT.read_text())


# ── Form loading ─────────────────────────────────────────────────────────────

LOADERS = [
    pytest.param(lambda d, mp: _patched_repo_root(api_jobs, d, mp), id="api.jobs"),
    pytest.param(lambda d, mp: _patched_repo_root(run_scout, d, mp), id="run_scout"),
    pytest.param(lambda d, mp: form_coverage.load_forms(d / "data" / "forms"), id="form_coverage"),
]


def _patched_repo_root(module, root: Path, monkeypatch):
    monkeypatch.setattr(module, "REPO_ROOT", root, raising=False)
    return module.load_forms()


def _form(opportunity_id: str, name: str) -> dict:
    return {
        "opportunity_id": opportunity_id,
        "name": name,
        "source_url": "https://example.invalid/form",
        "fields": [{"field_id": "f1", "label": "Describe your project"}],
    }


@pytest.mark.parametrize("load", LOADERS)
def test_form_loader_keys_by_opportunity_and_last_file_wins(load, tmp_path, monkeypatch):
    forms = tmp_path / "data" / "forms"
    forms.mkdir(parents=True)
    (forms / "a.json").write_text(json.dumps(_form("opp_1", "first")))
    (forms / "b.json").write_text(json.dumps(_form("opp_1", "second")))
    (forms / "c.json").write_text(json.dumps(_form("opp_2", "other")))
    (forms / "notes.txt").write_text("ignored")

    loaded = load(tmp_path, monkeypatch)

    assert sorted(loaded) == ["opp_1", "opp_2"]
    assert loaded["opp_1"].name == "second"


@pytest.mark.parametrize("load", LOADERS)
def test_form_loader_missing_directory_is_empty(load, tmp_path, monkeypatch):
    assert load(tmp_path, monkeypatch) == {}


@pytest.mark.parametrize("load", LOADERS)
def test_form_loader_invalid_form_raises(load, tmp_path, monkeypatch):
    forms = tmp_path / "data" / "forms"
    forms.mkdir(parents=True)
    (forms / "bad.json").write_text(json.dumps({"opportunity_id": "x"}))
    with pytest.raises(Exception):
        load(tmp_path, monkeypatch)


def test_real_forms_load_identically_everywhere():
    assert api_jobs.load_forms().keys() == run_scout.load_forms().keys()
    assert api_jobs.load_forms().keys() == form_coverage.load_forms().keys()


# ── Source factories ─────────────────────────────────────────────────────────


@pytest.fixture
def source_env(monkeypatch, tmp_path):
    """Both factories read the same fake config and reviewed-lane files."""
    university = tmp_path / "university.json"
    general = tmp_path / "general.json"
    university.write_text("[]")
    general.write_text("[]")
    monkeypatch.setattr(
        campus_module,
        "REVIEWED_WEB_LANES",
        (
            replace(UNIVERSITY_LANE, output_path=university),
            replace(GENERAL_LANE, output_path=general),
        ),
    )

    def configure(**overrides):
        config = SimpleNamespace(
            data_dir=tmp_path,
            allow_unverified_seed=False,
            enable_browser=False,
            grants_gov_base_url="https://grants.example.invalid",
            http_timeout_s=3.0,
        )
        for key, value in overrides.items():
            setattr(config, key, value)
        monkeypatch.setattr(api_jobs, "settings", lambda: config)
        monkeypatch.setattr(run_scout, "settings", lambda: config)
        return config

    return configure


def describe(sources) -> list[tuple]:
    out = []
    for source in sources:
        if isinstance(source, SeedCatalog):
            out.append(("seed", source.path.name, source.allow_unverified))
        elif isinstance(source, GrantsGovSource):
            out.append(
                (
                    "grants_gov",
                    source.keywords,
                    source.client.base_url,
                    source.client.timeout_s,
                )
            )
        elif isinstance(source, CampusDiscoverySource):
            out.append(
                (
                    "campus",
                    source.id_prefix,
                    source.enabled,
                    source.allow_live_scrape,
                    source.max_verification_age_days,
                )
            )
        else:
            out.append((type(source).__name__,))
    return out


DEFAULT_KEYWORDS = ("student", "undergraduate", "entrepreneurship")
REVIEWED = [
    ("campus", "university_web", True, False, campus_module.REVIEWED_WEB_MAX_AGE_DAYS),
    ("campus", "general_web", True, False, campus_module.REVIEWED_WEB_MAX_AGE_DAYS),
]


@pytest.mark.parametrize("demo", [False, True])
@pytest.mark.parametrize("grants_gov", [False, True])
@pytest.mark.parametrize("allow_unverified_seed", [False, True])
@pytest.mark.parametrize("enable_browser", [False, True])
def test_api_job_sources(source_env, demo, grants_gov, allow_unverified_seed, enable_browser):
    source_env(allow_unverified_seed=allow_unverified_seed, enable_browser=enable_browser)
    job = RunJob(
        job_id="job_t",
        founder_id="founder_t",
        use_demo_catalog=demo,
        include_grants_gov=grants_gov,
    )

    expected = [
        (
            "seed",
            "opportunities.demo.json" if demo else "opportunities.seed.json",
            demo or allow_unverified_seed,
        )
    ]
    if grants_gov:
        expected.append(
            ("grants_gov", DEFAULT_KEYWORDS, "https://grants.example.invalid", 3.0)
        )
    expected.append(("campus", "campus", enable_browser, False, None))
    expected.extend(REVIEWED)

    assert describe(api_jobs.build_sources(job)) == expected


@pytest.mark.parametrize("demo", [False, True])
@pytest.mark.parametrize("grants_gov", [False, True])
@pytest.mark.parametrize("enable_browser", [False, True])
@pytest.mark.parametrize("live_campus_scrape", [False, True])
@pytest.mark.parametrize("with_profile", [False, True])
def test_cli_sources(source_env, demo, grants_gov, enable_browser, live_campus_scrape, with_profile):
    source_env(enable_browser=enable_browser)
    founder = profile() if with_profile else None
    from agent.tools.discovery import keywords_for_profile

    expected = [
        ("seed", "opportunities.demo.json" if demo else "opportunities.seed.json", demo)
    ]
    if grants_gov:
        keywords = keywords_for_profile(founder) if founder else DEFAULT_KEYWORDS
        expected.append(("grants_gov", keywords, "https://grants.example.invalid", 3.0))
    expected.append(
        ("campus", "campus", enable_browser, enable_browser and live_campus_scrape, None)
    )
    expected.extend(REVIEWED)

    sources = run_scout.build_sources(
        demo, grants_gov, profile=founder, live_campus_scrape=live_campus_scrape
    )
    assert describe(sources) == expected


def test_targeted_reassessment_uses_only_the_persisted_row(source_env):
    from tests.factories import opportunity

    source_env()
    row = opportunity(id="opp_target")
    repo = SimpleNamespace(get_opportunity=lambda oid: row if oid == "opp_target" else None)
    job = RunJob(job_id="j", founder_id="f", target_opportunity_id="opp_target")

    sources = api_jobs.build_sources(job, repo)
    assert describe(sources) == [("PersistedOpportunitySource",)]
    assert sources[0].fetch(None) == [row]
    with pytest.raises(RuntimeError):
        api_jobs.build_sources(job)
    with pytest.raises(RuntimeError):
        api_jobs.build_sources(
            RunJob(job_id="j", founder_id="f", target_opportunity_id="missing"), repo
        )

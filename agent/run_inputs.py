"""The two inputs every pipeline run loads: discovery sources and forms.

One implementation shared by the API job (`api/jobs.py`) and the CLI
(`scripts/run_scout.py`). The callers differ only in which flags they pass,
so a flag cannot mean one thing from a terminal and another from the
dashboard.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.config import REPO_ROOT
from agent.models import ApplicationForm
from agent.tools.campus import CampusDiscoverySource, reviewed_web_sources
from agent.tools.discovery import GrantsGovClient, GrantsGovSource, SeedCatalog


def load_forms(directory: Path | None = None) -> dict[str, ApplicationForm]:
    """Load every transcribed application form, keyed by opportunity id.

    Read fresh on each call rather than cached, so editing a form JSON takes
    effect on the next run without a restart. A form that fails validation
    raises — a run must not silently proceed with the form missing.

    Only one form per opportunity survives: later files with the same
    `opportunity_id` overwrite earlier ones in sorted filename order. A missing
    directory is an empty mapping.
    """
    directory = directory if directory is not None else REPO_ROOT / "data" / "forms"
    forms: dict[str, ApplicationForm] = {}
    for path in sorted(directory.glob("*.json")):
        form = ApplicationForm.model_validate(json.loads(path.read_text()))
        forms[form.opportunity_id] = form
    return forms


def build_sources(
    config,
    *,
    demo: bool,
    grants_gov: bool,
    keywords: tuple[str, ...] | None = None,
    live_campus_scrape: bool = False,
) -> list:
    """Assemble discovery sources in priority order.

    *   Seed catalog always. The demo catalog is synthetic and unverified by
        construction, so choosing it is what allows unverified rows.
    *   Grants.gov only when asked. `keywords=None` keeps the source's own
        defaults.
    *   Campus always, gated twice: `config.enable_browser` decides whether it
        yields anything, and a live sweep additionally needs
        `live_campus_scrape`. A scheduled job never passes it.
    *   Human-accepted reviewed web rows.
    """
    catalog = "opportunities.demo.json" if demo else "opportunities.seed.json"
    sources: list = [
        SeedCatalog(
            config.data_dir / catalog,
            allow_unverified=demo or config.allow_unverified_seed,
        )
    ]
    if grants_gov:
        client = GrantsGovClient(config.grants_gov_base_url, config.http_timeout_s)
        sources.append(
            GrantsGovSource(client, keywords=keywords)
            if keywords is not None
            else GrantsGovSource(client)
        )
    sources.append(
        CampusDiscoverySource(
            enabled=config.enable_browser,
            allow_live_scrape=config.enable_browser and live_campus_scrape,
        )
    )
    sources.extend(reviewed_web_sources())
    return sources

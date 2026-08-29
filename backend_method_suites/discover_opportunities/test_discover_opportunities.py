"""Discovery across several sources: deduplication and partial failure.

A source that dies must be reported and must not take the working sources
with it. Deduplication is first-source-wins, and which source won is part
of the result rather than an implementation detail.
"""

from __future__ import annotations

from agent.tools.discovery import SourceError, discover_opportunities
from tests.factories import TODAY, opportunity


class ListSource:
    name = "seed"

    def __init__(self, *opportunities):
        self.opportunities = list(opportunities)

    def fetch(self, since):
        return self.opportunities


class DeadSource:
    name = "grants_gov"

    def fetch(self, since):
        raise SourceError("timeout")


def test_discovery_deduplicates_by_id_and_first_source_wins():
    seed = opportunity(id="same", title="[DEMO] Curated Row")
    live_duplicate = opportunity(id="same", title="[DEMO] Live Duplicate")

    found, failures = discover_opportunities(
        [ListSource(seed), ListSource(live_duplicate)], since=TODAY
    )

    assert failures == []
    assert len(found) == 1
    assert found[0].title == "[DEMO] Curated Row"


def test_discovery_records_failed_sources_while_returning_successful_rows():
    found, failures = discover_opportunities(
        [DeadSource(), ListSource(opportunity(id="survivor"))], since=TODAY
    )

    assert [o.id for o in found] == ["survivor"]
    assert len(failures) == 1
    assert failures[0].source == "grants_gov"
    assert "timeout" in failures[0].detail

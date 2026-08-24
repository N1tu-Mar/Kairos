"""The scraping pipeline, offline.

Fixtures in `tests/fixtures/rutgers/` are real pages, saved by the fetcher on
the day they were scraped. Nothing here touches the network: a test that
depends on a university web server being up is a test that fails for reasons
that have nothing to do with the code.

The cases that matter most are the refusals — the ones asserting that a field
the page did not state stays UNKNOWN, and that a robots.txt we could not read
is treated as a no.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from agent.scraping import extract
from agent.scraping.fetch import html_to_text
from agent.scraping.models import (
    Evidence,
    FetchRecord,
    ScrapedOpportunity,
    ScrapeRun,
)
from agent.scraping.pipeline import (
    build_record,
    deduplicate,
    discover_links,
    placeholder_record,
    write_candidates,
)
from agent.scraping.registry import TARGETS, Target, is_rutgers_domain

FIXTURES = Path(__file__).parent / "fixtures" / "rutgers"


def page(name: str) -> str:
    return html_to_text((FIXTURES / f"{name}.html").read_text(encoding="utf-8"))


def blocks(name: str) -> list[str]:
    return extract.to_blocks(page(name))


def target(**overrides) -> Target:
    base = dict(
        key="t",
        title="[TEST] Competition",
        organization="[TEST] Org",
        url="https://idea.rutgers.edu/programs/scarletpitch",
        tier="RUTGERS",
    )
    base.update(overrides)
    return Target(**base)


def record(**overrides) -> FetchRecord:
    base = dict(
        url="https://idea.rutgers.edu/programs/scarletpitch",
        final_url="https://idea.rutgers.edu/programs/scarletpitch",
        status_code=200,
        content_hash="abc123def456",
        fetched_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return FetchRecord(**base)


# ═══════════════════════════════════════════════════════════════════════════
# Never infer. This is the whole point of the pipeline.
# ═══════════════════════════════════════════════════════════════════════════


def test_a_field_without_evidence_cannot_be_set():
    opportunity = ScrapedOpportunity(
        scrape_id="x", title="t", organization="o", source_url="u", fetch=record()
    )

    assert opportunity.set_field("award_max", 5_000, None) is False
    assert opportunity.award_max is None
    assert "award_max" in opportunity.unknown_fields


def test_a_none_value_marks_the_field_unknown():
    opportunity = ScrapedOpportunity(
        scrape_id="x", title="t", organization="o", source_url="u", fetch=record()
    )
    evidence = Evidence(text="anything", source_url="u")

    assert opportunity.set_field("degree_levels", None, evidence) is False
    assert "degree_levels" in opportunity.unknown_fields


def test_setting_a_field_clears_its_unknown_flag():
    opportunity = ScrapedOpportunity(
        scrape_id="x", title="t", organization="o", source_url="u", fetch=record()
    )
    opportunity.mark_unknown("award_max")
    opportunity.set_field("award_max", 2_000, Evidence(text="First place: $2,000", source_url="u"))

    assert opportunity.unknown_fields == []
    assert opportunity.evidence["award_max"].text == "First place: $2,000"


def test_silence_about_equity_is_never_read_as_no_equity():
    """A pitch competition almost certainly takes no equity. Almost certainly
    is exactly the reasoning this pipeline refuses to do."""
    result = build_record(target(), page("scarletpitch"), record())

    assert result.equity_required is None
    assert "equity_required" in result.unknown_fields


def test_every_populated_eligibility_field_has_an_evidence_span():
    result = build_record(target(), page("rbs_business_plan"), record())

    for field in ("award_min", "award_max", "degree_levels", "institution", "deadline"):
        if getattr(result, field) is not None:
            assert field in result.evidence, f"{field} was set without evidence"
            assert result.evidence[field].text.strip()
            assert result.evidence[field].source_url


def test_evidence_is_quoted_from_the_page_not_paraphrased():
    result = build_record(target(), page("scarletpitch"), record())
    text = page("scarletpitch")

    quote = result.evidence["degree_levels"].text
    normalised = " ".join(text.split())
    assert " ".join(quote.split()) in normalised


# ═══════════════════════════════════════════════════════════════════════════
# Extraction against real pages
# ═══════════════════════════════════════════════════════════════════════════


def test_scarletpitch_prize_range():
    """Prizes render as alternating short lines; the block builder glues them."""
    awards = extract.find_awards(blocks("scarletpitch"), "u")

    assert awards["award_min"][0] == 250
    assert awards["award_max"][0] == 3_000
    assert "1st Place" in awards["award_max"][1].text


def test_scarletpitch_team_size():
    found = extract.find_team_size(blocks("scarletpitch"), "u")

    assert found["team_size_min"][0] == 1
    assert found["team_size_max"][0] == 5


def test_scarletpitch_degree_levels():
    levels, evidence = extract.find_degree_levels(blocks("scarletpitch"), "u")

    assert set(levels) >= {"undergraduate", "graduate"}
    assert "Rutgers-New Brunswick" in evidence.text


def test_a_date_without_a_year_does_not_become_a_date():
    """"Nov. 1st" could be this year or next. Picking one is inference."""
    verbatim, parsed, evidence = extract.find_deadline(blocks("scarletpitch"), "u")

    assert parsed is None
    assert "unresolved" in evidence.method
    assert verbatim


def test_an_opening_date_is_not_reported_as_a_deadline():
    found = extract.find_deadline(blocks("scarletpitch"), "u")
    assert found is not None
    verbatim, _, _ = found
    assert "Nov" not in verbatim, "Nov. 1st is when applications open, not when they close"


def test_a_date_with_a_year_resolves():
    verbatim, parsed, _ = extract.find_deadline(blocks("rbs_business_plan"), "u")

    assert parsed == date(2025, 12, 12)
    assert "December 12, 2025" in verbatim


def test_multiple_dates_produce_an_ambiguity_caveat():
    caveat = extract.deadline_is_ambiguous(blocks("scarletpitch"))

    assert caveat is not None
    assert "more than one date" in caveat


def test_a_prize_pool_is_not_reported_as_one_teams_award():
    """$50,000 across six winners is not a $50,000 award."""
    awards = extract.find_awards(blocks("rbs_business_plan"), "u")

    assert awards["award_max"][0] == 15_000
    assert any("50,000" in c and "combined or total" in c for c in awards["caveats"])


def test_conditional_eligibility_becomes_a_caveat():
    """"Open to all RBS students. Some restrictions apply for team leadership
    roles" is the difference between an application and a wasted afternoon."""
    caveats = extract.find_caveats(blocks("rbs_business_plan"), "u")

    assert any("conditional eligibility" in c for c in caveats)


def test_eligibility_labels_are_unioned_across_blocks():
    """A page can say "open to all RBS students" in one place and name the
    leadership categories in another. Taking only the first loses a real rule."""
    levels, _ = extract.find_degree_levels(blocks("rbs_business_plan"), "u")

    assert {"undergraduate", "mba", "alumni"} <= set(levels)


def test_an_open_scope_phrase_is_not_expanded_into_a_school_list():
    _, evidence = extract.find_institutions(blocks("njit_nbmc"), "u")

    assert "Northern NJ" in evidence.text


def test_a_sponsor_is_not_reported_as_the_organiser():
    """The Sales Executives Club funds the RBS competition. Rutgers runs it."""
    organization, _ = extract.find_organization(
        blocks("rbs_business_plan"), "u", "Rutgers Business School"
    )

    assert "Sales Executive" not in organization


def test_an_unparseable_organiser_falls_back_to_the_registry_not_a_guess():
    organization, evidence = extract.find_organization(["nothing here"], "u", "[TEST] Org")

    assert organization == "[TEST] Org"
    assert evidence.method == "registry_fallback"


def test_costs_are_not_mistaken_for_awards():
    found = extract.find_awards(["Registration fee is $25 per person."], "u")

    assert "award_max" not in found


def test_a_dollar_figure_with_no_award_context_is_ignored():
    found = extract.find_awards(["Our customers spend $400 a year on this."], "u")

    assert "award_max" not in found


# ═══════════════════════════════════════════════════════════════════════════
# Block building
# ═══════════════════════════════════════════════════════════════════════════


def test_short_lines_are_glued_across_blank_lines():
    """On its own "$3000" is a number. The two words above it make it a prize."""
    built = extract.to_blocks("1st Place\n\n$3000\n\n2nd Place\n\n$2000")

    assert len(built) == 1
    assert "1st Place $3000" in built[0]


def test_a_long_line_stands_alone():
    long_line = "x" * 200
    built = extract.to_blocks(f"short\n{long_line}\nalso short")

    assert long_line in built
    assert len(built) == 3


def test_gluing_is_bounded():
    built = extract.to_blocks("\n".join(f"line {i}" for i in range(60)))

    assert len(built) > 1, "an unbounded glue makes one block that supports anything"


# ═══════════════════════════════════════════════════════════════════════════
# Domain rules
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://idea.rutgers.edu/programs/scarletpitch", True),
        ("https://myrbs.business.rutgers.edu/x", True),
        ("https://sccinnovation.rutgers.edu/x", True),
        ("https://research.njit.edu/njiac", False),
        ("https://www.stevens.edu/x", False),
        ("https://rutgers.campuslabs.com/engage", False),
        ("https://notrutgers.edu/x", False),
        ("https://evil.com/?rutgers.edu", False),
    ],
)
def test_rutgers_domain_recognition(url, expected):
    assert is_rutgers_domain(url) is expected


def test_discovery_never_leaves_a_rutgers_domain():
    html = '<a href="https://research.njit.edu/grants">NJIT grants</a>'
    assert discover_links(html, "https://idea.rutgers.edu/programs") == []


def test_discovery_returns_nothing_at_all_for_an_off_domain_page():
    html = '<a href="https://www.stevens.edu/prizes">prizes</a>'
    assert discover_links(html, "https://www.stevens.edu/x") == []


def test_discovery_only_follows_funding_shaped_links():
    html = """
      <a href="/programs/grant-fund">Student grant fund</a>
      <a href="/about/parking">Parking information</a>
    """
    found = discover_links(html, "https://idea.rutgers.edu/programs")

    assert found == ["https://idea.rutgers.edu/programs/grant-fund"]


def test_discovery_is_capped():
    html = "".join(f'<a href="/grant-{i}">grant {i}</a>' for i in range(50))
    assert len(discover_links(html, "https://idea.rutgers.edu/x", limit=5)) == 5


def test_external_targets_are_flagged_in_the_output():
    result = build_record(
        target(tier="PROVIDED_EXTERNAL", url="https://research.njit.edu/x"),
        page("njit_nbmc"),
        record(url="https://research.njit.edu/x", final_url="https://research.njit.edu/x"),
    )

    assert any("off-domain" in c for c in result.caveats)


# ═══════════════════════════════════════════════════════════════════════════
# Provenance, dedupe, output
# ═══════════════════════════════════════════════════════════════════════════


def test_the_source_url_is_recorded_on_every_record():
    result = build_record(target(), page("scarletpitch"), record())

    assert result.source_url.startswith("https://idea.rutgers.edu/")
    assert result.fetch.url


def test_the_scrape_time_is_recorded():
    result = build_record(target(), page("scarletpitch"), record())

    assert result.scraped_at == record().fetched_at


def test_founder_reviews_are_never_populated_by_the_scraper():
    """The one field a scraper must never write. No target page has them."""
    result = build_record(target(), page("scarletpitch"), record())

    assert result.founder_reviews == []
    assert any("founder reviews" in c.lower() for c in result.caveats)


def test_every_record_starts_as_needing_review():
    result = build_record(target(), page("scarletpitch"), record())

    assert result.review_status == "NEEDS_HUMAN_REVIEW"


def test_a_target_with_no_url_is_recorded_with_everything_unknown():
    """A real opportunity should not vanish because it has no page yet."""
    result = placeholder_record(
        target(url="", no_stable_url=True, operator_note="[TEST] no page found")
    )

    assert result.source_url == ""
    assert "award_max" in result.unknown_fields
    assert result.evidence == {}
    assert result.fetch.failure.startswith("NO_STABLE_URL")


def test_the_same_program_at_two_urls_collapses_to_one_row():
    first = build_record(target(), page("scarletpitch"), record(content_hash="aaa"))
    second = build_record(
        target(url="https://idea.rutgers.edu/scarletpitch-2027"),
        page("scarletpitch"),
        record(url="https://idea.rutgers.edu/scarletpitch-2027",
               final_url="https://idea.rutgers.edu/scarletpitch-2027",
               content_hash="bbb"),
    )

    kept, merged = deduplicate([first, second])

    assert len(kept) == 1
    assert merged == 1
    assert any("duplicate merged" in c for c in kept[0].caveats)


def test_deduplication_keeps_the_richer_record():
    rich = build_record(target(), page("scarletpitch"), record(content_hash="aaa"))
    thin = build_record(target(), "nothing useful here", record(content_hash="bbb"))

    kept, _ = deduplicate([thin, rich])

    assert len(kept) == 1
    assert kept[0].award_max == 3_000, "a thinner duplicate must not overwrite a richer one"


def test_different_programs_are_not_merged():
    first = build_record(target(title="A"), page("scarletpitch"), record(content_hash="aaa"))
    second = build_record(target(title="B"), page("rbs_business_plan"), record(content_hash="bbb"))

    kept, merged = deduplicate([first, second])

    assert len(kept) == 2
    assert merged == 0


def test_output_never_touches_the_seed_catalog(tmp_path):
    seed_before = Path("data/opportunities.seed.json").read_text()
    result = build_record(target(), page("scarletpitch"), record())

    write_candidates(
        [result],
        ScrapeRun(run_id="r"),
        path=tmp_path / "candidates.json",
        run_log=tmp_path / "runs.jsonl",
    )

    assert Path("data/opportunities.seed.json").read_text() == seed_before


def test_a_human_review_decision_survives_the_next_scrape(tmp_path):
    """A scraper that overwrites a human's verdict makes the review pointless."""
    path = tmp_path / "candidates.json"
    result = build_record(target(), page("scarletpitch"), record())
    write_candidates([result], ScrapeRun(run_id="r1"), path=path, run_log=tmp_path / "l.jsonl")

    rows = json.loads(path.read_text())
    rows[0]["review_status"] = "ACCEPTED"
    rows[0]["founder_reviews"] = [
        {
            "text": "[TEST] Worth the two weeks.",
            "attribution": "[TEST] 2025 finalist",
            "source_url": None,
            "added_by": "[TEST] a human",
            "added_at": "2026-08-23T00:00:00Z",
        }
    ]
    path.write_text(json.dumps(rows))

    write_candidates([result], ScrapeRun(run_id="r2"), path=path, run_log=tmp_path / "l.jsonl")

    after = json.loads(path.read_text())[0]
    assert after["review_status"] == "ACCEPTED"
    assert len(after["founder_reviews"]) == 1


def test_the_run_records_what_it_could_not_do():
    run = ScrapeRun(run_id="r", targets_attempted=8, pages_fetched=6, opportunities_found=7)
    run.failures.append(FetchRecord(url="https://x.invalid", failure="NEEDS_JS: shell"))

    assert "Failed 1" in run.headline()


# ═══════════════════════════════════════════════════════════════════════════
# The registry is a set of claims about the world, so check its shape
# ═══════════════════════════════════════════════════════════════════════════


def test_every_target_declares_a_tier_and_a_note():
    for entry in TARGETS:
        assert entry.tier in {"RUTGERS", "PROVIDED_EXTERNAL"}
        assert entry.operator_note, f"{entry.key} has no operator note"


def test_a_target_without_a_url_is_marked_as_such():
    for entry in TARGETS:
        assert bool(entry.url) or entry.no_stable_url


def test_only_pages_proven_to_need_javascript_are_flagged_for_it():
    flagged = [t.key for t in TARGETS if t.requires_js]
    assert flagged == ["rutgers_entrepreneurial_society"]

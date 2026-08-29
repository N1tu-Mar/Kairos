"""The search-driven scraper: URL normalisation, filtering, and target building.

No network. Everything here is about what the agent decides to fetch
*before* it fetches anything: which URLs are worth a request, which hosts
are skipped, and what provenance a candidate carries when it is written.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from agent.scraping.agent import (
    BraveSearchClient,
    GENERAL_LANE,
    GeneralWebScraperAgent,
    SearchResult,
    UNIVERSITY_LANE,
    UniversityWebScraperAgent,
    WebScraperAgent,
    WebScraperConfig,
    host_matches_any,
    is_university_search_result,
    normalize_candidate_url,
    target_from_result,
)
from agent.scraping.models import FetchRecord


class FakeSearch:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.queries: list[str] = []

    def search(self, query: str, *, count: int) -> list[SearchResult]:
        self.queries.append(query)
        return [
            SearchResult(
                title=r.title,
                url=r.url,
                snippet=r.snippet,
                query=query,
                rank=index,
                source=r.source,
            )
            for index, r in enumerate(self.results[:count], start=1)
        ]


class FakeFetcher:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.fetched: list[str] = []

    def fetch(self, url: str, *, allow_js: bool = False):
        self.fetched.append(url)
        text = self.pages.get(url, "")
        return (
            text,
            FetchRecord(
                url=url,
                final_url=url,
                status_code=200 if text else 404,
                content_hash=f"hash-{len(self.fetched)}",
                fetched_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
                failure=None if text else "HTTP_404",
            ),
        )


def test_normalize_candidate_url_keeps_page_and_drops_tracking():
    assert normalize_candidate_url(
        "https://Example.edu/grants/?utm_source=x&round=2026#apply"
    ) == "https://example.edu/grants/?round=2026"


def test_normalize_candidate_url_skips_non_pages():
    assert normalize_candidate_url("mailto:test@example.edu") is None
    assert normalize_candidate_url("https://example.edu/app.pdf") is None


def test_host_matches_allowlisted_domains():
    assert host_matches_any("https://funding.rutgers.edu/x", ("rutgers.edu",))
    assert not host_matches_any("https://notrutgers.edu/x", ("rutgers.edu",))


def test_search_results_become_unreviewed_web_targets():
    result = SearchResult(
        title="Student Venture Prize",
        url="https://example.edu/prize",
        snippet="Cash prize for student founders.",
        query="student founder prize",
        rank=2,
        source="brave",
    )

    target = target_from_result(result)

    assert target.tier == "GENERAL_WEB_SEARCH"
    assert target.url == "https://example.edu/prize"
    assert "student founder prize" in target.operator_note
    assert "general funding" in target.operator_note
    assert target.key.startswith("general_web_")


def test_university_result_detection_accepts_dot_edu_and_student_language():
    assert is_university_search_result(
        SearchResult(title="Startup Prize", url="https://example.edu/prize")
    )
    assert is_university_search_result(
        SearchResult(
            title="Campus Pitch Competition",
            url="https://events.example.com/pitch",
            snippet="Cash prizes for undergraduate student founders.",
        )
    )
    assert not is_university_search_result(
        SearchResult(
            title="Small Business Innovation Grant",
            url="https://publicfunding.example.org/grants",
            snippet="Non-dilutive funding for incorporated startups.",
        )
    )


def test_discovery_filters_duplicates_social_hosts_and_weak_results():
    good = SearchResult(
        title="Student Startup Grant",
        url="https://example.edu/grant?utm_source=test",
        snippet="Funding for student entrepreneurs.",
    )
    agent = WebScraperAgent(
        FakeSearch(
            [
                good,
                SearchResult(title="Student Startup Grant", url=good.url),
                SearchResult(title="Video", url="https://youtube.com/watch?v=1", snippet="grant"),
                SearchResult(title="Parking", url="https://example.edu/parking", snippet="maps"),
            ]
        ),
        config=WebScraperConfig(queries=("student grants",), max_results_per_query=10),
    )

    targets, notes = agent.discover_targets()

    assert notes == []
    assert [t.url for t in targets] == ["https://example.edu/grant"]


def test_domain_allowlist_limits_search_fetches():
    agent = WebScraperAgent(
        FakeSearch(
            [
                SearchResult(
                    title="Rutgers Innovation Grant",
                    url="https://idea.rutgers.edu/grant",
                    snippet="Funding for students.",
                ),
                SearchResult(
                    title="Other Innovation Grant",
                    url="https://example.edu/grant",
                    snippet="Funding for students.",
                ),
            ]
        ),
        config=WebScraperConfig(
            queries=("student grants",),
            domains=("rutgers.edu",),
            max_results_per_query=10,
        ),
    )

    targets, _ = agent.discover_targets()

    assert [t.url for t in targets] == ["https://idea.rutgers.edu/grant"]


def test_university_agent_filters_to_university_lane_targets():
    agent = UniversityWebScraperAgent(
        FakeSearch(
            [
                SearchResult(
                    title="Small Business Innovation Grant",
                    url="https://publicfunding.example.org/grants",
                    snippet="Grant funding for startups.",
                ),
                SearchResult(
                    title="Campus Venture Prize",
                    url="https://entrepreneurship.example.edu/prize",
                    snippet="Cash prize for undergraduate student entrepreneurs.",
                ),
            ]
        ),
        config=WebScraperConfig.for_lane(
            UNIVERSITY_LANE,
            queries=("college startup pitch competitions",),
            max_results_per_query=10,
        ),
    )

    targets, notes = agent.discover_targets()

    assert notes == []
    assert [t.tier for t in targets] == ["UNIVERSITY_WEB_SEARCH"]
    assert [t.url for t in targets] == ["https://entrepreneurship.example.edu/prize"]
    assert "university" in targets[0].tags


def test_general_and_university_agents_can_share_one_search_client():
    search = FakeSearch(
        [
            SearchResult(
                title="Founder Grant",
                url="https://publicfunding.example.org/grant",
                snippet="Non-dilutive grant funding for startup founders.",
            ),
            SearchResult(
                title="University Pitch Competition",
                url="https://innovation.example.edu/pitch",
                snippet="Prize funding for graduate student founders.",
            ),
        ]
    )
    general = GeneralWebScraperAgent(
        search,
        config=WebScraperConfig.for_lane(
            GENERAL_LANE,
            queries=("startup grants",),
            max_results_per_query=10,
        ),
    )
    university = UniversityWebScraperAgent(
        search,
        config=WebScraperConfig.for_lane(
            UNIVERSITY_LANE,
            queries=("student pitch competitions",),
            max_results_per_query=10,
        ),
    )

    general_targets, _ = general.discover_targets()
    university_targets, _ = university.discover_targets()

    assert search.queries == ["startup grants", "student pitch competitions"]
    assert {target.tier for target in general_targets} == {"GENERAL_WEB_SEARCH"}
    assert {target.tier for target in university_targets} == {"UNIVERSITY_WEB_SEARCH"}


def test_run_reuses_scraper_pipeline_and_marks_rows_for_review(tmp_path):
    url = "https://example.edu/startup-prize"
    page = """
    Student Startup Prize
    Hosted by the Example Innovation Center.
    First prize $5,000.
    Open to undergraduate students.
    Applications close March 1, 2027.
    """
    agent = WebScraperAgent(
        FakeSearch(
            [
                SearchResult(
                    title="Student Startup Prize",
                    url=url,
                    snippet="Cash prize for undergraduate student founders.",
                )
            ]
        ),
        config=WebScraperConfig(
            queries=("student startup prize",),
            max_results_per_query=5,
            raw_dir=tmp_path,
        ),
        fetcher=FakeFetcher({url: page}),
    )

    records, run = agent.run()

    assert run.targets_attempted == 1
    assert len(records) == 1
    assert records[0].award_max == 5_000
    assert records[0].review_status == "NEEDS_HUMAN_REVIEW"
    assert any("web search" in caveat for caveat in records[0].caveats)


def test_brave_search_client_maps_web_results():
    request = httpx.Request("GET", "https://api.search.brave.com/res/v1/web/search")
    response = httpx.Response(
        200,
        request=request,
        json={
            "web": {
                "results": [
                    {
                        "title": "<b>Student Grant</b>",
                        "url": "https://example.edu/grant",
                        "description": "Funding for student founders.",
                    }
                ]
            }
        },
    )

    class FakeHttp:
        def get(self, *args, **kwargs):
            return response

    client = BraveSearchClient("test-key", http_client=FakeHttp())

    results = client.search("student grant", count=5)

    assert results == [
        SearchResult(
            title="Student Grant",
            url="https://example.edu/grant",
            snippet="Funding for student founders.",
            query="student grant",
            rank=1,
            source="brave",
        )
    ]

"""The web-scraper CLI: lane selection and output routing.

No network. The search client and the fetcher are both fakes, so what is
under test is the wiring — which lanes run, and where each one writes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from agent.scraping.agent import SearchResult
from agent.scraping.models import FetchRecord
from scripts import run_web_scraper


class FakeSearch:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.queries: list[str] = []

    def search(self, query: str, *, count: int) -> list[SearchResult]:
        self.queries.append(query)
        return [
            SearchResult(
                title=result.title,
                url=result.url,
                snippet=result.snippet,
                query=query,
                rank=index,
                source="fake",
            )
            for index, result in enumerate(self.results[:count], start=1)
        ]


class FakeFetcher:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages

    def fetch(self, url: str, *, allow_js: bool = False):
        text = self.pages[url]
        return (
            text,
            FetchRecord(
                url=url,
                final_url=url,
                status_code=200,
                content_hash=f"hash-{abs(hash(url))}",
                fetched_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
            ),
        )


def test_both_lanes_share_search_client_and_write_separate_outputs(tmp_path):
    public_url = "https://publicfunding.example.org/grant"
    university_url = "https://innovation.example.edu/pitch"
    search = FakeSearch(
        [
            SearchResult(
                title="Founder Grant",
                url=public_url,
                snippet="Non-dilutive grant funding for startup founders.",
            ),
            SearchResult(
                title="University Pitch Competition",
                url=university_url,
                snippet="Prize funding for graduate student founders.",
            ),
        ]
    )
    fetcher = FakeFetcher(
        {
            public_url: """
            Founder Grant
            Public Startup Foundation.
            Awards up to $10,000.
            Applications close May 1, 2027.
            """,
            university_url: """
            University Pitch Competition
            Campus Innovation Center.
            First prize receives $2,500.
            Open to graduate and undergraduate student founders.
            Applications close April 15, 2027.
            """,
        }
    )

    exit_code = run_web_scraper.main(
        [
            "--lane",
            "both",
            "--max-results-per-query",
            "10",
            "--max-pages",
            "1",
            "--raw-dir",
            str(tmp_path / "raw"),
            "--out-dir",
            str(tmp_path / "out"),
        ],
        search_client=search,
        fetcher=fetcher,
    )

    general_path = tmp_path / "out" / "opportunities.web.candidates.json"
    university_path = tmp_path / "out" / "opportunities.university-web.candidates.json"

    assert exit_code == 0
    assert len(search.queries) == 2
    assert general_path.exists()
    assert university_path.exists()

    general = json.loads(general_path.read_text())
    university = json.loads(university_path.read_text())
    assert general[0]["source_url"] == public_url
    assert university[0]["source_url"] == university_url
    assert any("general web search" in note for note in general[0]["caveats"])
    assert any("university web search" in note for note in university[0]["caveats"])


def test_out_file_is_rejected_when_running_both_lanes(tmp_path, capsys):
    exit_code = run_web_scraper.main(
        ["--lane", "both", "--out", str(tmp_path / "candidates.json")],
        search_client=FakeSearch([]),
    )

    assert exit_code == 2
    assert "--out can only be used with one lane" in capsys.readouterr().err

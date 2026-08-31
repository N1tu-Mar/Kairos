"""The web-scraper CLI: lane selection and output routing.

No network. The search client and the fetcher are both fakes, so what is
under test is the wiring — which lanes run, and where each one writes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agent.scraping.agent import GENERAL_QUERIES, UNIVERSITY_QUERIES, SearchResult
from agent.scraping.firecrawl import FirecrawlResult
from agent.scraping.models import FetchRecord, content_hash
from scripts import run_web_scraper


class FakeSearch:
    """A search provider returning a fixed list. Shared across lanes so a test can assert both used the same client."""

    def __init__(self, results: list[SearchResult]) -> None:
        """`queries` records every query issued, across all lanes."""
        self.results = results
        self.queries: list[str] = []

    def search(self, query: str, *, count: int) -> list[SearchResult]:
        """Return the canned hits, recording the query."""
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
    """A fetcher over canned pages, so a lane can complete without a network."""

    def __init__(self, pages: dict[str, str]) -> None:
        """`pages` maps URL to body."""
        self.pages = pages

    def fetch(self, url: str, *, allow_js: bool = False):
        """Serve a canned page for this URL."""
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


class FakeFirecrawl:
    """Return complete rendered text and count paid page attempts."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def scrape(self, url: str, *, local_record: FetchRecord, fallback_reason: str):
        self.calls.append(url)
        text = (
            "Student Founder Grant\n"
            "Example University Innovation Center.\n"
            "Awards up to $5,000.\n"
            "Open to undergraduate students.\n"
            "Applications close May 1, 2027."
        )
        return FirecrawlResult(
            text=text,
            record=FetchRecord(
                url=url,
                final_url=url,
                status_code=200,
                robots_allowed=local_record.robots_allowed,
                robots_url=local_record.robots_url,
                renderer="firecrawl",
                content_format="markdown",
                fallback_reason=fallback_reason,
                content_hash=content_hash(text),
            ),
            attempts=1,
            retries=0,
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
    assert search.queries == [*UNIVERSITY_QUERIES, *GENERAL_QUERIES]
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


def test_firecrawl_requires_a_key_before_search_or_writes(monkeypatch, tmp_path, capsys):
    search = FakeSearch([])
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setattr(run_web_scraper, "load_dotenv", lambda *args, **kwargs: False)

    exit_code = run_web_scraper.main(
        ["--firecrawl", "--out", str(tmp_path / "candidates.json")],
        search_client=search,
    )

    assert exit_code == 2
    assert search.queries == []
    assert not (tmp_path / "candidates.json").exists()
    assert "FIRECRAWL_API_KEY is not set" in capsys.readouterr().err


def test_firecrawl_and_local_playwright_are_rejected_together(capsys):
    exit_code = run_web_scraper.main(
        ["--firecrawl", "--allow-js"],
        search_client=FakeSearch([]),
    )

    assert exit_code == 2
    assert "choose one" in capsys.readouterr().err


def test_firecrawl_cap_is_shared_across_both_lanes(tmp_path):
    url = "https://innovation.example.edu/grant"
    search = FakeSearch(
        [
            SearchResult(
                title="Student Founder Grant",
                url=url,
                snippet="University grant funding for student entrepreneurs.",
            )
        ]
    )
    local = FakeFetcher({url: "Short grant page with a $5,000 award."})
    firecrawl = FakeFirecrawl()

    exit_code = run_web_scraper.main(
        [
            "--lane",
            "both",
            "--query",
            "student grant",
            "--max-pages",
            "1",
            "--firecrawl",
            "--max-firecrawl-pages",
            "1",
            "--raw-dir",
            str(tmp_path / "raw"),
            "--out-dir",
            str(tmp_path / "out"),
        ],
        search_client=search,
        fetcher=local,
        firecrawl_client=firecrawl,
    )

    assert exit_code == 0
    assert firecrawl.calls == [url]
    general = json.loads(
        (tmp_path / "out" / "opportunities.web.candidates.json").read_text()
    )
    university = json.loads(
        (tmp_path / "out" / "opportunities.university-web.candidates.json").read_text()
    )
    assert university[0]["fetch"]["renderer"] == "firecrawl"
    assert general[0]["fetch"]["renderer"] == "httpx"
    run_lines = (tmp_path / "raw" / "scrape_runs.jsonl").read_text().splitlines()
    assert any("pages attempted 1" in line for line in run_lines)
    assert any("capped 1" in line for line in run_lines)


def test_firecrawl_run_notes_include_zero_totals_when_search_is_empty(tmp_path):
    exit_code = run_web_scraper.main(
        [
            "--query",
            "student grant",
            "--firecrawl",
            "--raw-dir",
            str(tmp_path / "raw"),
            "--out",
            str(tmp_path / "candidates.json"),
        ],
        search_client=FakeSearch([]),
        fetcher=FakeFetcher({}),
        firecrawl_client=FakeFirecrawl(),
    )

    assert exit_code == 0
    run_log = (tmp_path / "raw" / "scrape_runs.jsonl").read_text()
    assert "Firecrawl fallback invocation totals" in run_log
    assert "pages attempted 0" in run_log


def test_firecrawl_page_cap_must_be_positive():
    with pytest.raises(SystemExit) as exc:
        run_web_scraper.main(
            ["--firecrawl", "--max-firecrawl-pages", "0"],
            search_client=FakeSearch([]),
        )

    assert exc.value.code == 2

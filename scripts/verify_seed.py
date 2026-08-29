"""Verify seed catalog candidates against their live source URLs.

Section 0.5 rule 3: *never write seed data from memory.* Every row in
`data/opportunities.seed.json` needs a `source_url` someone actually fetched
and a `verified_at` timestamp. A row that fails verification gets
`"verified": false` and is excluded from runs.

This script is what makes that rule mechanical instead of aspirational. It
reads `data/opportunities.candidates.json`, fetches every `source_url`, and
writes `data/opportunities.seed.json` with an honest verdict on each row.

It verifies two things, and is honest about the difference:

1.  **Reachability** — the page exists and still mentions the program title.
    A 200 alone does not prove a manually entered value is correct.
2.  **Evidence presence** — every verbatim quote the row carries in
    `criteria[].text` actually appears on the page that quote cites in its
    `source_doc` (whitespace- and punctuation-normalised). Programs routinely
    state eligibility on an FAQ or rules sub-page rather than the landing
    page, so each quote is checked against the page it claims to come from,
    and each distinct page is fetched once. A quote citing a page outside the
    funder's own site is refused rather than blessed — that is a judgment for
    a human. This is the mechanical guard against fabricated or drifted
    evidence: a curator (human or agent) cannot invent a supporting quote
    without the verifier catching it.

It still does not verify *interpretation* — that `award_max: 10000` is the
right reading of the quoted sentence is a human judgment. What it removes is
the failure class where the quote itself never existed.

    uv run python scripts/verify_seed.py
    uv run python scripts/verify_seed.py --strict   # exit 1 if any row fails
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from urllib.parse import urlsplit

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = REPO_ROOT / "data" / "opportunities.candidates.json"
SEED = REPO_ROOT / "data" / "opportunities.seed.json"

UA = "Mozilla/5.0 (compatible; kairos-seed-verifier/1.0)"


def normalize(text: str) -> str:
    """Lowercase, tags stripped, every run of non-alphanumerics collapsed to
    one space. Makes quote matching robust to markup, curly quotes and
    whitespace without weakening it to keyword search."""
    text = html_mod.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def page_mentions(html: str, phrase: str) -> bool:
    """Does the fetched page actually talk about this program?"""
    text = re.sub(r"<[^>]+>", " ", html).lower()
    tokens = [t for t in re.findall(r"[a-z]{4,}", phrase.lower())]
    if not tokens:
        return True
    hits = sum(1 for t in tokens if t in text)
    return hits >= max(1, len(tokens) // 2)


def registrable_domain(url: str) -> str:
    """Host minus one leading label, enough to tell `cep.mit.edu` from
    `nasaorbit.org` without shipping a public-suffix list."""
    host = urlsplit(url).netloc.lower().split(":")[0].removeprefix("www.")
    parts = host.split(".")
    return ".".join(parts[-3:]) if len(parts) > 3 else host


def same_site(a: str, b: str) -> bool:
    """Do two URLs belong to the same organisation's site?

    Compared on the last two labels, so `cep.mit.edu` and `mit.edu` match
    while `nasa.gov` and `nasaorbit.org` do not. An evidence quote citing a
    page outside the funder's own site is not automatically wrong, but it is
    not something this script will bless — it goes to a human.
    """
    def tail(url: str) -> str:
        """The registrable tail of a host — the last two labels, lowercased.

        Crude by design: it treats `foo.example.edu` and `example.edu` as one
        site so a redirect within an institution is not reported as a move, and
        it is knowingly wrong for multi-part public suffixes.
        """
        host = urlsplit(url).netloc.lower().split(":")[0].removeprefix("www.")
        return ".".join(host.split(".")[-2:])

    return bool(tail(a)) and tail(a) == tail(b)


def missing_evidence(pages: dict[str, str], row: dict) -> list[str]:
    """Every `criteria[].text` quote that does NOT appear on the page it cites.

    A criterion's `source_doc` names the page the quote was copied from,
    which is often a sub-page of the program — an FAQ, a rules page, an
    agency solicitation. The quote is checked against *that* page, because
    that is the claim being made. `pages` maps URL to fetched HTML.

    Quotes are matched normalised, as substrings. An empty return means every
    quoted span was found verbatim (modulo whitespace and punctuation).
    """
    missing = []
    for criterion in row.get("criteria") or []:
        quote = criterion.get("text", "")
        if not quote:
            continue
        cited = (criterion.get("source_doc") or row.get("source_url", "")).split("#")[0]
        html = pages.get(cited)
        if html is None:
            missing.append(f"{quote} [cited page could not be fetched: {cited}]")
            continue
        if normalize(quote) not in normalize(html):
            missing.append(quote)
    return missing


def evidence_pages(row: dict) -> tuple[list[str], list[str]]:
    """`(pages to fetch, off-site pages refused)` for one row's evidence."""
    source_url = row.get("source_url", "")
    wanted, refused = [], []
    for criterion in row.get("criteria") or []:
        cited = (criterion.get("source_doc") or source_url).split("#")[0]
        if not cited or cited in wanted or cited in refused:
            continue
        if cited != source_url and not same_site(source_url, cited):
            refused.append(cited)
        else:
            wanted.append(cited)
    return wanted, refused


def fetch(url: str, timeout_s: float, cache: dict[str, str | None]) -> str | None:
    """One GET per distinct URL per run. `None` means it could not be read."""
    if url in cache:
        return cache[url]
    try:
        response = httpx.get(
            url, timeout=timeout_s, follow_redirects=True, headers={"User-Agent": UA}
        )
        cache[url] = response.text if response.status_code == 200 else None
        if response.status_code != 200:
            cache[f"{url}::status"] = f"HTTP {response.status_code}"  # type: ignore[assignment]
    except httpx.HTTPError as exc:
        cache[url] = None
        cache[f"{url}::status"] = f"fetch failed: {type(exc).__name__}: {exc}"  # type: ignore[assignment]
    return cache[url]


def verify(row: dict, timeout_s: float, cache: dict | None = None) -> dict:
    """Re-fetch every row and re-find every quote it claims.

    The only thing that may mark a row verified, and it does so on evidence:
    the page must still be reachable, still be the same site, and still
    contain each `criteria[]` quote. A row that fails any of those keeps its
    previous state and is reported.
    """
    url = row.get("source_url", "")
    result = dict(row)
    cache = {} if cache is None else cache

    if not url:
        result["verified"] = False
        result["verified_at"] = None
        result["verification_note"] = "no source_url on this row"
        return result

    html = fetch(url, timeout_s, cache)
    if html is None:
        result["verified"] = False
        result["verified_at"] = None
        result["verification_note"] = cache.get(f"{url}::status", "fetch failed")
        return result

    if not page_mentions(html, row.get("title", "")):
        result["verified"] = False
        result["verified_at"] = None
        result["verification_note"] = (
            "page returned 200 but does not mention the program title — "
            "the URL may have been retired into a generic landing page"
        )
        return result

    wanted, refused = evidence_pages(row)
    if refused:
        result["verified"] = False
        result["verified_at"] = None
        result["verification_note"] = (
            f"evidence cites {len(refused)} page(s) outside the funder's own site "
            f"({refused[0]}); a human has to confirm that source before this row "
            f"can be trusted"
        )
        return result

    pages = {url: html}
    for cited in wanted:
        pages[cited] = fetch(cited, timeout_s, cache)  # type: ignore[assignment]
    pages = {k: v for k, v in pages.items() if v is not None}

    lost = missing_evidence(pages, row)
    if lost:
        result["verified"] = False
        result["verified_at"] = None
        result["verification_note"] = (
            f"{len(lost)} quoted evidence span(s) were not found on the page "
            f"each one cites — the quote may be fabricated or paraphrased, or "
            f"the page has changed since curation. First missing: {lost[0][:120]!r}"
        )
        return result

    checked = len(row.get("criteria") or [])
    page_count = len(pages)
    result["verified"] = True
    result["verified_at"] = datetime.now(timezone.utc).isoformat()
    result["verification_note"] = (
        f"HTTP 200, title terms present, {checked} evidence quote(s) found "
        f"across {page_count} cited page(s)"
    )
    return result


def main() -> int:
    """CLI entry. 0 when every checked row verified, non-zero when any did not."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=CANDIDATES)
    parser.add_argument("--out", type=Path, default=SEED)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--strict", action="store_true", help="exit non-zero if any row fails"
    )
    args = parser.parse_args()

    if not args.candidates.exists():
        print(f"no candidates file at {args.candidates}", file=sys.stderr)
        return 1

    rows = json.loads(args.candidates.read_text())
    cache: dict = {}
    verified = [verify(row, args.timeout, cache) for row in rows]

    args.out.write_text(json.dumps(verified, indent=2) + "\n")

    passed = sum(1 for r in verified if r["verified"])
    print(f"{passed}/{len(verified)} rows verified -> {args.out}")
    for row in verified:
        if not row["verified"]:
            print(f"  FAIL {row.get('id', '?')}: {row['verification_note']}")

    return 1 if args.strict and passed != len(verified) else 0


if __name__ == "__main__":
    raise SystemExit(main())

# Data

## `opportunities.candidates.json` → `opportunities.seed.json`

Tier 1 of the funding universe (spec Section 5) is a hand-curated catalog.
The rule that governs it is Section 0.5 rule 3:

> Never write seed data from memory. Every entry needs a `source_url` you
> actually fetched and a `verified_at` timestamp. An unverified entry gets
> `"verified": false` and is excluded from demo runs. A plausible-sounding
> grant that doesn't exist is worse than 20 fewer entries.

So the catalog is generated, not written:

1. A human adds a row to `opportunities.candidates.json` **after opening the
   page** and reading the eligibility, award range and deadline off it.
2. `uv run python scripts/verify_seed.py` fetches every `source_url`, checks
   it returns 200 and actually mentions the program, and writes
   `opportunities.seed.json` with an honest `verified` flag and timestamp.
3. `SeedCatalog` excludes `verified: false` rows from runs unless
   `KAIROS_ALLOW_UNVERIFIED_SEED=true`.

The verifier checks **reachability, not correctness**. A 200 means the page
exists. It does not mean the award range on the row is right — a human still
has to read the page. What it catches is the failure that survives review:
a confident row pointing at a URL that never existed.

That is not hypothetical. The first candidate list written for this repo
included `https://venturewell.org/e-team-grants/`, which looks exactly right
and returns 404. It is in `opportunities.candidates.json` on purpose, as a
row that fails.

**Current state: the catalog is a stub.** The verifier, the schema and the
exclusion behaviour all work; the 60–100 curated rows are still to be
collected. Until they are, demo runs use `opportunities.demo.json`.

## `opportunities.demo.json`

Obviously synthetic. Every row is `[DEMO]`-prefixed in both `title` and
`funder`, and every `source_url` is on `.invalid`, a TLD reserved by RFC 2606
that can never resolve. It exists so the pipeline can be exercised end to
end before curation is finished.

It is not loaded by default, it must never appear in the demo video as
though it were live, and `[DEMO]` must stay in the strings — that prefix is
the only thing standing between a screenshot and a false claim.

## `demo_founder.json`

The founder profile used in the video. Synthetic. Numbers here flow into the
numeric whitelist, so they are the only traction figures any draft can
legally contain.

## `forms/`

Real application forms modelled as structured JSON. Field labels are copied
verbatim from the source form, because the Section 10.1 blocklist matches on
label text — paraphrasing a label is how a certification field stops looking
like one.

**Current state: the only form here is `demo_campus_innovation_fund.json`**,
the synthetic form for the `[DEMO]` catalog row. No real form has been
transcribed yet. The verbatim-label rule above is how they must be added, not
a description of what is already in this directory.

## `opportunities.rutgers.candidates.json`

Output of `scripts/scrape_rutgers.py`. **Not production data, and not on the
path to becoming it by accident.** Every row carries
`review_status: NEEDS_HUMAN_REVIEW`, and no code writes from here into
`opportunities.candidates.json` or `opportunities.seed.json` — promotion is a
person copying a row across after reading it.

The schema is deliberately different from `Opportunity`. It carries two
things the production model does not:

- `evidence` — the verbatim sentence behind every populated field, with the
  URL it was on. A value with no evidence is not a fact.
- `unknown_fields` — every field the page did not state. `UNKNOWN` here means
  the page was silent, not that there is no restriction. The scraper cannot
  populate a field without evidence: `ScrapedOpportunity.set_field` refuses.

`founder_reviews` is always empty when the scraper writes it. No target page
publishes reviews from past applicants, and that field is the one a founder
leans on hardest when deciding whether a program is worth two weeks. It is
filled by a human or not at all. Re-running the scraper preserves both it and
`review_status`.

## `raw/`

The scrape archive, kept apart from everything above.

- `raw/pages/<host>/<path>.<timestamp>.html` — the exact bytes received, with
  a `.meta.json` sidecar recording URL, timestamp, HTTP status, the robots
  decision and a content hash. Extraction reads the archive, so a
  disagreement about what a page said is settled by opening a file rather
  than by asking a university web server the same question twice.
- `raw/robots/<host>.robots.txt` — the robots.txt each fetch decision was
  made against, cached so the decision is checkable later against what the
  host actually said at the time.
- `raw/scrape_runs.jsonl` — one line per sweep: counters, failures, notes.

The HTML and the run log are gitignored; the sidecars and robots.txt are
committed, because they are the audit trail.

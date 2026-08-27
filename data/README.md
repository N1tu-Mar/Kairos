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

The verifier checks **reachability and evidence, not interpretation**. A 200
means the page exists. Since 2026-08-27 it also re-finds every quote in
`criteria[].text` on the page that quote cites in its `source_doc` — programs
put eligibility on FAQ and rules sub-pages, so each quote is checked where it
claims to come from, and each distinct page is fetched once. A quote citing a
page outside the funder's own site is refused rather than blessed.

That closes the failure mode where a curator — human or agent — writes a
supporting quote that the page does not contain. A paraphrase fails
identically to a fabrication, which is correct: both are text the page does
not have. What it still cannot check is *interpretation*. That `award_max:
10000` is the right reading of the sentence quoted beside it remains a human
judgment, and a person still has to read the page.

That is not hypothetical. The first candidate list written for this repo
included `https://venturewell.org/e-team-grants/`, which looks exactly right
and returns 404. It is in `opportunities.candidates.json` on purpose, as a
row that fails.

**Current state (2026-08-27): 49 candidate rows, 43 verified.**

The six failures are kept on purpose, because each is a different way a row
can be untrustworthy and all six are invisible to a reachability-only check:

| Row | Why it fails |
|---|---|
| `venturewell_eteam` | HTTP 404. The original worked example. |
| `masschallenge-…` , `emergent-ventures` | HTTP 403 — the host refuses automated clients. Unread is not verified. |
| `776-foundation-fellowship`, `neo-scholars` | JavaScript-rendered. The researcher read the quotes out of the page's own JS bundle; a static verifier cannot confirm them. |
| `nasa-orbit-challenge` | Its rules live on `nasaorbit.org`, off NASA's own site. A human has to bless that source. |

All six carry `verified: false` and are excluded from runs.

This is short of the promised 60–100. Three research sweeps — university
competitions, plus two follow-up batches — were killed by an API rate limit
before they finished. The honest smaller set shipped instead of padding the
count, which is the trade `data/README.md` has always described.

Rows are folded in from research batches with `scripts/merge_candidates.py`,
which validates every row against the real `Opportunity` model, deduplicates
on id and normalised URL, **refuses rows a researcher marked unreachable**
rather than writing them from secondhand description, and appends a stale
note to any row whose deadline has already passed.

Demo runs still use `opportunities.demo.json`.

## `reverification.report.json`

Written by `scripts/reverify.py`. A `verified_at` timestamp is a claim about
the past; this is the queue of rows whose claim needs re-checking. Each entry
is DEAD, REDIRECTED, TITLE_GONE, EVIDENCE_LOST, DEADLINE_PASSED or UNCHANGED,
with the specific change recorded.

**Nothing in it has been applied.** The script does not edit the catalog, does
not flip `verified`, and does not update a deadline. It reports; a person
decides and edits by hand. Latest run over all 49 rows: 32 unchanged, 9 with passed deadlines, 3 dead,
3 with lost evidence, 2 redirected.

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

**Current state (2026-08-27): three real forms plus the synthetic one.**

| File | Fields | Complete? |
|---|---|---|
| `njit_new_business_model_competition.json` | 4 | yes — the page states the whole initial submission and its two-page limit |
| `mit_climate_energy_prize.json` | 7 | **no** — the page lists the application's sections, not the questions inside them |
| `tcnj_mayo_business_plan_competition.json` | 14 | **no** — the schedule page states deliverables; the guidebook was not transcribed |
| `demo_campus_innovation_fund.json` | 14 | synthetic, `[DEMO]` |

A partial form carries `complete: false` and a `completeness_note` saying
exactly what is missing. A form transcribed from half a page and presented as
whole is worse than no form: the Drafter would treat the missing questions as
nonexistent rather than unanswered.

No login was used, no portal was opened, and no question was inferred. Where
a form lives behind an account, that is recorded as missing rather than
guessed at.

Fields the agent must never fill carry `protected: true` — MIT CEP's
disclosure forms and rules agreement, TCNJ's policy affirmation. That flag is
enforced, not documentation: the Drafter forces any protected field to
`NEEDS_FOUNDER` independently of the label blocklist, and
`tests/test_real_forms.py` runs the real drafting path against every real form
with a stub that tries to answer everything, then asserts those fields come
back unanswered.

Transcribing MIT CEP's form found a live gap: `blocklisted()` did not catch
"IP, Capital, and Revenue Disclosure Forms", because the word *disclosure*
alone matched nothing. The blocklist now covers it.

`scripts/form_coverage.py` prints which catalog rows have a form (2 of 49)
and which can be discovered and judged but not drafted.

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

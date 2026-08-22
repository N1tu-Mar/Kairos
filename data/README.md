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

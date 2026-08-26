At minimum:
1. Build the real curated catalog
   Research and verify the promised 60–100 campus, nonprofit, fellowship, and corporate programs.
2. Implement non-API web discovery
   Add the missing browser/crawler source for university and program websites.
3. Extract structured facts with provenance
   Extract deadlines, award ranges, eligibility rules, and application links while retaining the exact supporting page text.
4. Reverify programs periodically
   Funding pages expire, redirect, and change deadlines. A one-time verified_at value eventually becomes stale.
5. Improve Grants.gov coverage
   Add pagination, wider/profile-aware queries, date filters, and coverage measurement.
6. Model real application forms
   The current modeled form is synthetic. Finding a real fund is not sufficient to draft its application without knowing its actual questions.
7. Measure discovery recall
   Create a reference set of known relevant funds and ask:
   Of the funds the system should have found, what percentage did it retrieve?


   
# Incomplete work

## Scheduled pipeline invocation

~~The production mechanism that periodically invokes the Kairos pipeline is
not yet decided.~~ A position is now taken in `infra/` and its README
answers each question this section used to ask: the pipeline deploys with
the API in one Fargate task; EventBridge Scheduler invokes the existing
`POST /founders/{id}/runs` daily through an API destination; it
authenticates with the same `KAIROS_API_TOKEN` bearer token the dashboard's
proxy sends; retries are capped at 1 so overlap stays a non-issue at a
daily cadence; and API and worker do not separate, so SQLite stays
single-writer.

Still true and still enforced: the browser owns none of this — nothing in
`frontend/` schedules anything. Still open: the Terraform has not been
applied against a live account, there is no run-lease lock if the cadence
ever tightens, and failure reporting is CloudWatch logs, not an alarm.

## Backend gaps the frontend ran into

Found while building `frontend/`. Items 1–5 are now closed; the endpoints
exist and are covered by `tests/test_api.py`. Item 6 is open and is a
deliberate decision, not an oversight.

1. ~~**No opportunity read endpoint.**~~ Closed. Runs now persist every
   opportunity they retrieved (`agent/scout.py` step 7,
   `SqliteRepository.save_opportunity`), and `GET /opportunities/{id}` serves
   the row. Award range, deadline and the extracted eligibility rules are
   structured fields, so anything downstream can sort or filter on them
   instead of parsing the headline a run happened to compose. A rejected
   opportunity is resolvable too, which is what makes a `Rejection` traceable
   back to the row it was written about.
2. ~~**No `GET /runs/{run_id}`.**~~ Closed as
   `GET /founders/{founder_id}/runs/{run_id}` and
   `GET /founders/{founder_id}/runs/{run_id}/skips`. Scoped to the founder in
   the path so a mistyped id 404s rather than quietly returning another
   founder's run. `list_runs` is still capped; older runs now resolve through
   the primary key.
3. ~~**Inbox item state is write-only from the pipeline's side.**~~ Closed as
   `PATCH /inbox/{item_id}`, which sets `state` and nothing else. `kind`,
   `headline`, `summary` and `assessment` are what the run decided and stay
   immutable — an audit trail you can edit is not one.
4. ~~**Drafts are reachable only through the inbox.**~~ Closed as
   `GET /founders/{founder_id}/drafts`, optionally filtered by
   `opportunity_id`. Counts still come from `Draft.counts()` in Python.
5. ~~**Profiles are read-only.**~~ Closed as `PUT /founders/{founder_id}`,
   a whole-object replace rather than a patch. These fields are what the
   deterministic eligibility filter compares against, so a half-applied
   update is the one outcome worth ruling out entirely — `citizenship`
   changed without `degree_level` is how a founder gets told they are
   eligible for something they are not. The body's `founder_id` must match
   the path.
6. ~~**No authentication anywhere in the repository.**~~ Closed for the
   single-founder case: `KAIROS_API_TOKEN` gates every endpoint except
   `/health` (`api/main.py::require_api_token`, covered by
   `backend_method_suites/auth_required`). The frontend carries the token
   server-side only — the browser talks to the Route Handlers and never
   holds the credential. What this deliberately is **not**: per-founder
   identity. One token opens the whole API, so founder scoping in the paths
   remains the 404-on-mismatch convenience it always was. The day there are
   two founders, that becomes a real authorisation check or the second
   founder does not get an account.

### Still not exposed, on purpose

- **Nothing edits a recorded verdict.** There is no endpoint that mutates a
  `RunReport`, a `Rejection`, a `SkipRecord`, an `Assessment` or a `Draft`
  after the run wrote it. Corrections belong in a new run, not in a rewrite
  of the old one.
- ~~**The frontend does not consume the new endpoints yet.**~~ Closed. The
  inbox and briefing cards render award range, deadline and the funder's
  page link from `GET /opportunities/{id}` (falling back to the composed
  headline when a row cannot be resolved), run detail reads
  `GET /founders/{id}/runs/{run_id}` so links older than the list cap
  resolve, `/drafts` lists every draft whether or not an inbox item points
  at it, inbox items can be marked opened/dismissed/applied through
  `PATCH /inbox/{item_id}`, and the profile's structured facts are editable
  through `PUT /founders/{id}` — sent as a whole object, with traction and
  the knowledge base passed through untouched. This raises the stakes on the
  auth gap above: both writes are now one click away in a browser.

The "Run Kairos now" control in the frontend is a manual trigger against the
existing `POST /founders/{id}/runs`, and its copy says so. It is not a
scheduler and does not stand in for one — see *Scheduled pipeline invocation*
above, which is still the open decision.

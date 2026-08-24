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

The production mechanism that periodically invokes the Kairos pipeline is not yet decided. Do not implement scheduling as part of the Next.js frontend work.

Before choosing an approach, confirm:

- where the Python/FastAPI pipeline will be deployed;
- expected run duration and frequency;
- whether overlapping runs must be prevented;
- required retry and failure-reporting behavior;
- how the scheduled trigger will authenticate;
- whether the scheduler should invoke an HTTP endpoint, enqueue a job, or start a dedicated worker;
- database and locking requirements when the API and worker run separately.

The browser must not own this responsibility: Kairos must continue searching when no founder has the site open.

## Backend gaps the frontend ran into

Found while building `frontend/`. Each one is a missing read or write on the
FastAPI surface, not a frontend defect, and none of them were worked around by
inventing data in the browser.

1. **No opportunity read endpoint.** `Opportunity` is never returned by the API
   and is not persisted by `SqliteRepository`, so award range, deadline and
   effort reach the dashboard only as the pre-rendered text inside
   `InboxItem.headline` (composed in `agent/scout.py::_headline`). The inbox
   therefore cannot sort or filter by deadline, and cannot show a countdown
   that updates after the run that produced it. Fixing this means persisting
   the opportunity rows a run surfaced, or adding
   `GET /opportunities/{opportunity_id}`.
2. **No `GET /runs/{run_id}`.** The run-detail page finds its run inside
   `GET /founders/{id}/runs?limit=50`, because that response already carries
   the complete `RunReport` — rejections, skips, source failures and notes.
   A direct link to a run older than the 50 most recent will 404.
3. **Inbox item state is write-only from the pipeline's side.**
   `InboxItem.state` (`new` / `opened` / `dismissed` / `applied`) is stored and
   served, but no endpoint updates it, so the dashboard cannot let a founder
   mark something read, dismissed or applied. Every item renders as it was
   written.
4. **Drafts are reachable only through the inbox.** `GET /drafts/{draft_id}`
   exists, but nothing maps a founder or an opportunity to their drafts, so a
   draft whose inbox item was never created is unreachable.
5. **Profiles are read-only.** There is no profile write endpoint, so
   `/profile` presents the founder's structured facts and knowledge base as a
   summary and does not offer editing it would have to fake.
6. **No authentication anywhere in the repository.** The dashboard reads one
   founder, named by the server-side `KAIROS_FOUNDER_ID` environment variable.
   It is not multi-tenant and must not be deployed as if it were.

The "Run Kairos now" control in the frontend is a manual trigger against the
existing `POST /founders/{id}/runs`, and its copy says so. It is not a
scheduler and does not stand in for one — see *Scheduled pipeline invocation*
above, which is still the open decision.

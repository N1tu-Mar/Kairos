# Kairos — frontend

The founder-facing surface for Kairos. Next.js App Router, TypeScript,
Tailwind. It reads the FastAPI backend and re-implements none of it.

## What this app is, and is not

**It is** a read surface over decisions the Python pipeline already made, plus
three narrow writes: a button that starts a run, an inbox-state patch (what
*you* did with an item — opened, dismissed, applied — and nothing else), and a
whole-object profile replace. Nothing can edit a recorded verdict — the
backend exposes no such endpoint, so this app cannot either.

**It is not** a second copy of the system. In particular the browser never:

- runs the agent pipeline,
- holds model credentials or the backend address,
- crawls funding websites,
- does background work of any kind.

There is no scheduler, cron job, queue or worker here. Kairos is designed
around a run that happens while the founder is asleep, and **nothing in this
repository schedules one yet** — that decision is open and recorded in
[`../incomplete.md`](../incomplete.md). The "Run Kairos now" button is a manual
action and its copy says so.

## Architecture

```
browser ──▶ Next.js (server) ──▶ FastAPI ──▶ SQLite
             │
             └── Route Handlers (thin proxy: runs, inbox state, profile)
```

- **Server Components** fetch every page's initial data. `KAIROS_API_URL` is a
  server-only variable — deliberately *not* `NEXT_PUBLIC_*` — so the backend
  address never ships to the browser.
- **Client Components** exist only where there is interaction or browser state:
  the nav's active link, the manual run control, the inbox state control, and
  the profile editor.
- **Route Handlers** (`src/app/api/`) are a backend-for-frontend proxy and
  nothing more. They forward one request and translate an error into a status
  code. No logic, no state, no persistence. There are three:
  `POST /api/runs`, `PATCH /api/inbox/[itemId]` (the `state` field only), and
  `PUT /api/profile` (whole-object, founder id pinned to this dashboard's).
- **`src/lib/api.ts`** is the only module that talks to FastAPI. Everything
  else imports from it.
- **`src/lib/types.ts`** mirrors `agent/models.py` field for field. No field is
  declared that the API does not actually return.

There is no second database and no duplicated business logic. Eligibility,
assessment, drafting, auditing, gating and persistence all stay in Python.

## Setup

Requires Node 20+ and a running Kairos backend.

```bash
cd frontend
npm install
cp .env.example .env.local     # then edit if your backend is not on :8000
npm run dev                    # http://localhost:3000
```

Start the backend separately, from the repository root:

```bash
uv run uvicorn api.main:app --reload --port 8000
```

The backend seeds a demo founder from `data/demo_founder.json` on startup. To
put data on the dashboard without an AWS account, use the repository's dry-run
runner (see the root README) or the **Run Kairos now** button with *Use the
demo catalog* checked.

## Environment variables

All server-side. None of them are exposed to the browser.

| Variable | Default | Purpose |
|---|---|---|
| `KAIROS_API_URL` | `http://127.0.0.1:8000` | Base URL of the FastAPI backend. |
| `KAIROS_FOUNDER_ID` | `founder_demo` | Which founder this dashboard reads. There is no auth in this repository; the dashboard is single-founder by design. |
| `KAIROS_API_TOKEN` | *(empty)* | Bearer token forwarded to the backend when it has `KAIROS_API_TOKEN` set. Server-only; empty means the backend is running open (localhost demo). |
| `KAIROS_API_TIMEOUT_MS` | `10000` | Timeout for ordinary reads. |
| `KAIROS_RUN_TIMEOUT_MS` | `180000` | Timeout for the manual run trigger. A real run does discovery, assessment and drafting, so reads are not a guide. |

**Never prefix any of these with `NEXT_PUBLIC_`.** That prefix makes a value
client-visible, and the backend address is not something the browser needs.

## Scripts

```bash
npm run dev         # development server
npm run build       # production build
npm run start       # serve the production build
npm run lint        # eslint, --max-warnings 0
npm run typecheck   # tsc --noEmit
npm run test        # vitest
```

## Screens

| Route | What it shows |
|---|---|
| `/` | The briefing: latest run counters, duration, tokens, spend, halted reason, source failures, and what surfaced. |
| `/inbox` | Surfaced opportunities, split into active recommendations and the passive "also found" list. Cards show the structured award range, deadline and the funder's page link from `GET /opportunities/{id}`, and let you mark an item opened, dismissed or applied. |
| `/runs` | Every recorded run. |
| `/runs/[runId]` | Run transparency: deterministic rejections grouped by the check that fired, skips grouped by the stage that made the call, source failures, notes. Reads `GET /founders/{id}/runs/{run_id}`, so a link to a run older than the list cap still resolves. |
| `/drafts` | Every draft, including one whose inbox item was dismissed or never created. |
| `/drafts/[draftId]` | Draft review: READY/BLOCKED, the gate check that failed, KNOWN/REUSED/GENERATED/NEEDS_FOUNDER counts, every question and answer with its provenance and audit verdict. |
| `/profile` | The founder's structured facts and knowledge base. The structured facts are editable; saving replaces the whole profile (`PUT /founders/{id}`) because a half-applied update is the failure the backend refuses to allow. Traction and the knowledge base stay read-only here — they are evidence. |

## Conventions worth keeping

- **"Nothing surfaced" is a result, not an error.** A run that scanned 214
  opportunities and surfaced zero is a successful run and renders as a quiet
  one. It never shares styling with a failure.
- **A partial run says so.** Source failures and halted runs are shown, never
  smoothed over.
- **`[DEMO]` stays visible.** Synthetic records are marked at the source and
  every view that renders one keeps the marker and adds a badge.
- **Kairos prepares; it never submits.** No screen offers, implies or animates
  submitting an application. The final action is always review.
- **No invented data.** Facts render from the structured fields the API
  serves — award range and deadline from the persisted `Opportunity` row, not
  parsed back out of a composed headline. When a row cannot be resolved, the
  card falls back to the headline the backend wrote, verbatim. Where the API
  still exposes nothing, the UI says so rather than faking it — see
  [`../incomplete.md`](../incomplete.md).
- **Every data-backed view has loading, empty and error states**, and "nothing
  recorded yet" is visually distinct from "the backend is down".

## Tests

`npm run test` runs Vitest with Testing Library against the states that are
easy to get wrong: quiet results, halted runs, missing gate results, the
`[DEMO]` marker surviving into every view, buttons refusing a second click
while a request is in flight, the card falling back to the composed headline
when an opportunity row cannot be resolved, a passed deadline being flagged,
and the profile editor sending the whole object with traction and the
knowledge base untouched.

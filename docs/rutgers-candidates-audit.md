# Rutgers candidate audit — 2026-08-26

Every row in `data/opportunities.rutgers.candidates.json`, re-checked against
its authoritative source page on 2026-08-26. Method: live re-fetch of each
`source_url`, plus a check of the archived raw HTML under `data/raw/pages/`
where a stored value disagreed with the live page. No field was changed
without a verbatim quote from the primary page supporting the change.

**Who reviewed:** this audit was performed by an automated agent under
explicit operator direction, not by an anonymous scraper run. Review-status
changes below are recorded in the candidate file with an
`[audit 2026-08-26 ...]` caveat naming the method. A human can re-open any
verdict by editing `review_status`.

## Verdicts

| # | Candidate | Verdict | Why |
|---|---|---|---|
| 1 | ScarletPitch | **PASS — promoted** | Every stored field matched the live page: prizes $250–$3,000, team 1–5, Rutgers–New Brunswick undergrad + grad, equity unstated. Live page now carries an apply link (`https://go.rutgers.edu/ApplyScarletPitch`) and labels the timeline "2027 Competition". Deadline dates still carry no year, so `deadline` stays null in the promoted row. |
| 2 | RBS Business Plan Competition | **NEEDS FOLLOW-UP** | Two extraction defects fixed with archived-page evidence: `award_min` 15000 → 5000 (the winners table lists $10,000 and $5,000 prizes; $50,000 is the combined pool), and team size 1–5 was on the page but missed. The 2025/2026 cycle is complete (Pitch Day April 3, 2026) — not promoted until the next cycle posts. |
| 3 | UPitchNJ | **FAIL — rejected** | Confirmed indirect-only: a Rutgers undergraduate reaches it by winning ScarletPitch, not by applying. The live page has also moved past the scraped snapshot (now announcing May 1, 2026 at Rowan). $500 was a past year's figure. |
| 4 | Rutgers TechStart Innovation Challenge | **NEEDS FOLLOW-UP** | Still no authoritative page. A 2026-08-26 search of rutgers.edu / business.rutgers.edu found related CTEC programs but no TechStart application page. Every field stays UNKNOWN. |
| 5 | NJIT New Business Model Competition | **NEEDS FOLLOW-UP** | All stored fields matched the live page ($3,000 fellowship, Northern-NJ student eligibility, equity unstated). Deadline November 4, 2025 is stale — the page shows last cycle. Genuinely open to a Rutgers founder; recheck when the fall 2026 cycle posts. |
| 6 | Rutgers MTC Code for Impact Hackathon | **FAIL — rejected** | Live page confirms "4 non-cash prizes"; event date April 4, 2026 has passed. Not a funding opportunity. |
| 7 | Ansary Entrepreneurship Competition (Stevens) | **FAIL — rejected** | Extraction defect fixed: `award_max` 17500 → 10000 ($17,500 is the pool total; First Prize is $10,000). Entry runs through Stevens senior-design courses, so a Rutgers founder cannot apply — the hard negative the registry predicted. |

**Tally: 1 pass (promoted), 3 needs-follow-up, 3 fail.**

## Field-level check detail

Stored values were compared against live-page quotes for: program name,
organization, award, deadline, applicant type, degree level, institution
restriction, team size, equity, and application URL. Deviations found:

- `rbs_business_plan.award_min` — stored 15000; page (archived and live)
  lists "$10,000 BUGS Nicholas Khan $10,000 DetX Santu and Sahil Ghosh
  $5,000 DUPRify Louis Deabreau $5,000". Corrected to 5000.
- `rbs_business_plan.team_size_*` — page states "Individuals or teams of up
  to five students/alumni can enter the competition." Was UNKNOWN; now 1–5.
- `stevens_ansary.award_max` — stored 17500; page states "First Prize
  ($10,000)" and "Ansary Prizes for Entrepreneurship, totaling $17,500".
  Corrected to 10000.

Everything else matched, or was already UNKNOWN and stays UNKNOWN. Silent
fields were not filled: no page states equity terms, so `equity_required`
remains UNKNOWN on every row.

## Stale / ambiguous deadlines

- RBS: 2025-12-12 — stale, previous cycle.
- NJIT: 2025-11-04 — stale, previous cycle.
- ScarletPitch: "Dec. 21st" / "Jan. 29th" with no year — page labels the
  timeline "2027 Competition" but prints no year on the dates; left
  unresolved rather than guessed.

## Founder reviews

Still empty on every row, by construction. No target page publishes them and
this audit did not invent any.

## What was promoted

`rutgers_scarletpitch` → `data/opportunities.candidates.json`, then through
`scripts/verify_seed.py`. It is the only row satisfying all promotion rules:
evidence verified against the live primary page, eligibility explicit, award
current-cycle, and directly applicable to a student founder.

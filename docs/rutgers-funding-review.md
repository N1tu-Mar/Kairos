# Rutgers student funding — candidate opportunities for review

**Nothing in this document is production data.** Every row below was read off
a public web page by a deterministic scraper, and every row is waiting on a
human. None of it has been written to `data/opportunities.seed.json`, and
nothing will be until someone reads the evidence and says so.

Attempted 8. Fetched 6. Extracted 7. Merged 0. Failed 1.

Run `scrape_c428896eda6d`, finished 2026-08-24T01:39:37.907670+00:00.

---

## How to read this

Three things are worth knowing before you trust a single number here.

1. **`UNKNOWN` means the page did not say it.** It does not mean "no
   restriction", it does not mean "probably fine", and it was not filled in
   from anywhere else. If a program's page never mentions equity, the equity
   row says UNKNOWN — not "no equity" — even where that would almost
   certainly be right. Almost certainly right is how a wrong fact ends up on
   a real application.
2. **Every value carries the sentence it came from.** The evidence table at
   the end of each entry is the actual page text. Where a number looks wrong,
   read the quote: the parse is a convenience and the quote is the source.
3. **Founder reviews are empty everywhere, and that is not a finding.** No
   page in this target set publishes them. The scraper has no code path that
   writes that field. If you want reviews in here, they have to come from
   people you talk to.

## What was not collected, and why

- **Nothing behind a login.** No target was authenticated against, no form
  was submitted, and no CAPTCHA was touched. There is no code path for any of
  it.
- **Nothing robots.txt disallowed.** Each host's robots.txt was fetched
  before its pages were, archived under `data/raw/robots/`, and an
  unreachable robots.txt was treated as a refusal rather than as permission.
- **No speculative browser rendering.** Pages were fetched as static HTML.
  A headless browser is used only where a static fetch already proved the
  page returns a JavaScript shell, and only when explicitly asked for.

### Targets that produced nothing this run

- `https://rutgers.campuslabs.com/engage/organization/RES` — NEEDS_JS: page renders its content with JavaScript

### Run notes

- rutgers_techstart: no URL to fetch — recorded as all-UNKNOWN

---

## 1. Ansary Entrepreneurship Competition

**Run by:** Stevens Institute of Technology
**Source:** <https://www.stevens.edu/ansary-entrepreneurship-competition>
**Scraped:** 2026-08-24T01:39:37.767111+00:00
**Review status:** `NEEDS_HUMAN_REVIEW`

### What kind of application is it

competition prize

### What the money looks like

| | |
|---|---|
| Award range | $2,500 – $17,500 |
| Deadline as written | **UNKNOWN** — the page does not state this. Not inferred. |
| Deadline as a date | **UNKNOWN** — the page does not state this. Not inferred. |
| Equity taken | **UNKNOWN** — the page does not state this. Not inferred. |

### What it requires

| | |
|---|---|
| Institutions | **UNKNOWN** — the page does not state this. Not inferred. |
| Degree levels | **UNKNOWN** — the page does not state this. Not inferred. |
| Applicant type | **UNKNOWN** — the page does not state this. Not inferred. |
| Team size | **UNKNOWN** — the page does not state this. Not inferred. |

### What past student founders said

- **None available.** No page in this target set publishes reviews from past student applicants, and the scraper does not write this field. Anything that appears here was typed in by a person. Treat the absence as missing information, not as a bad sign about the program.

### Read this before applying

- [conditional eligibility] "Before founding XtremeLabs, Abbas held senior leadership roles across the technology and IT services industry. He was Vice President of Global Services at Digital Intelligence Systems (DISYS), where he drove new service offerings and global delivery strategy. He also served as Senior Vice President "
- [indirect access route] "A live quarter-final that takes place within each participating academic department. Course instructors choose top teams, which then advance to the next round."
- [host-institution scope] "features student teams in a high-stakes contest to persuade prospective investors to help them turn their Senior Design Projects into effective businesses. Students deliver elevator pitches as they compete for the"
- [operator note] The hard negative. Real money, but the competition is built around Stevens senior design teams. Kept in the review file precisely so the reasoning for rejecting it is visible rather than assumed.
- [off-domain] This page is not on a Rutgers-owned domain. It was fetched because it was named explicitly in the target list, at exactly that URL, and it was not crawled.
- [founder reviews] None. No target page publishes reviews from past student applicants, so this field is empty by construction rather than by omission. Anything here must be typed in by a human.

### What we do not know

`deadline`, `degree_levels`, `institution`, `applicant_type`, `equity_required`, `team_size_min`, `team_size_max`

### Evidence — every field above, traced to the page

| Field | Quoted from the page | Found by |
|---|---|---|
| `organization` | Not stated in a parseable form on the page; taken from the target registry: Stevens Institute of Technology | `registry_fallback` |
| `award_min` | Third Prize ($2,500) | `regex:award_block` |
| `award_max` | Ansary Prizes for Entrepreneurship , totaling $17,500. Ansary Foundation Prizes for the competition are provided by the Cy and Jan Ansary Foundation | `regex:award_block` |
| `award_type` | Ansary Prizes for Entrepreneurship , totaling $17,500. Ansary Foundation Prizes for the competition are provided by the Cy and Jan Ansary Foundation | `regex:award_type:competition prize` |

---

## 2. New Business Model Competition

**Run by:** New Jersey Innovation Acceleration Center (NJIT)
**Source:** <https://research.njit.edu/njiac/new-business-model-competition>
**Scraped:** 2026-08-24T01:39:36.998670+00:00
**Review status:** `NEEDS_HUMAN_REVIEW`

### What kind of application is it

fellowship

### What the money looks like

| | |
|---|---|
| Award range | $3,000 |
| Deadline as written | November 4, 2025 |
| Deadline as a date | 2025-11-04 |
| Equity taken | **UNKNOWN** — the page does not state this. Not inferred. |

### What it requires

| | |
|---|---|
| Institutions | New Jersey Institute of Technology |
| Degree levels | **UNKNOWN** — the page does not state this. Not inferred. |
| Applicant type | student team, student |
| Team size | **UNKNOWN** — the page does not state this. Not inferred. |

### What past student founders said

- **None available.** No page in this target set publishes reviews from past student applicants, and the scraper does not write this field. Anything that appears here was typed in by a person. Treat the absence as missing information, not as a bad sign about the program.

### Read this before applying

- The page lists more than one date in a deadline context (November 4, 2025, November 18, 2025, November 4, November 18). The one recorded above is the earliest date the page labels as a closing date; confirm which applies to you.
- [indirect access route] "The contest is open to (1) any current student of a Northern NJ area college or university and (2) any Northern NJ regional community member who is proposing to start a new business in NJ. Those submitting as community members cannot be full-time students. NJIT student teams will automatically be co"
- [operator note] Off-domain but genuinely open to a Rutgers founder: the competition takes current students at Northern NJ colleges and universities, not only NJIT students. Verify the current year's scope in the evidence spans.
- [off-domain] This page is not on a Rutgers-owned domain. It was fetched because it was named explicitly in the target list, at exactly that URL, and it was not crawled.
- [founder reviews] None. No target page publishes reviews from past student applicants, so this field is empty by construction rather than by omission. Anything here must be typed in by a human.

### What we do not know

`degree_levels`, `equity_required`, `team_size_min`, `team_size_max`

### Evidence — every field above, traced to the page

| Field | Quoted from the page | Found by |
|---|---|---|
| `organization` | Not stated in a parseable form on the page; taken from the target registry: New Jersey Innovation Acceleration Center (NJIT) | `registry_fallback` |
| `award_min` | Hosted annually during the fall semester, the Center runs the New Business Model Competition. Many entrepreneurs submit practical innovative ideas for review by a panel of judges for a chance to win a $3,000 summer fellowship to pay them to work on their idea. Although submission is free, each submission must follow the guidelines of the NAVC program. This year's finals on December 2nd will open w… | `regex:award_block` |
| `award_max` | Hosted annually during the fall semester, the Center runs the New Business Model Competition. Many entrepreneurs submit practical innovative ideas for review by a panel of judges for a chance to win a $3,000 summer fellowship to pay them to work on their idea. Although submission is free, each submission must follow the guidelines of the NAVC program. This year's finals on December 2nd will open w… | `regex:award_block` |
| `award_type` | Hosted annually during the fall semester, the Center runs the New Business Model Competition. Many entrepreneurs submit practical innovative ideas for review by a panel of judges for a chance to win a $3,000 summer fellowship to pay them to work on their idea. Although submission is free, each submission must follow the guidelines of the NAVC program. This year's finals on December 2nd will open w… | `regex:award_type:fellowship` |
| `deadline` | Deadline for Submissions: Tuesday, November 4, 2025 Date for Notification of Finalists: Tuesday, November 18, 2025 Presentations by Finalists: | `regex:deadline_with_year:closing` |
| `institution` | The contest is open to (1) any current student of a Northern NJ area college or university and (2) any Northern NJ regional community member who is proposing to start a new business in NJ. Those submitting as community members cannot be full-time students. NJIT student teams will automatically be competing to represent NJIT at Hult Prize National Competition. | `regex:eligibility_block` |
| `applicant_type` | The contest is open to (1) any current student of a Northern NJ area college or university and (2) any Northern NJ regional community member who is proposing to start a new business in NJ. Those submitting as community members cannot be full-time students. NJIT student teams will automatically be competing to represent NJIT at Hult Prize National Competition. | `regex:eligibility_block` |

---

## 3. RBS Business Plan Competition

**Run by:** Rutgers Business School
**Source:** <https://myrbs.business.rutgers.edu/case-competitions/business-plan>
**Scraped:** 2026-08-24T01:39:36.233139+00:00
**Review status:** `NEEDS_HUMAN_REVIEW`

### What kind of application is it

cash prize

### What the money looks like

| | |
|---|---|
| Award range | $15,000 |
| Deadline as written | December 12, 2025 |
| Deadline as a date | 2025-12-12 |
| Equity taken | **UNKNOWN** — the page does not state this. Not inferred. |

### What it requires

| | |
|---|---|
| Institutions | Rutgers University |
| Degree levels | alumni, undergraduate, mba |
| Applicant type | student team, student, alumni, student founder |
| Team size | **UNKNOWN** — the page does not state this. Not inferred. |

### What past student founders said

- **None available.** No page in this target set publishes reviews from past student applicants, and the scraper does not write this field. Anything that appears here was typed in by a person. Treat the absence as missing information, not as a bad sign about the program.

### Read this before applying

- The page also states $50,000 as a combined or total figure, which is larger than the largest individual award found ($15,000). Confirm which number applies to one team.
- The page lists more than one date in a deadline context (December 12, 2025, January 15 2026, December 12, January 15, February 15, 2026, March 1, 2026, April 3, 2026, February 15). The one recorded above is the earliest date the page labels as a closing date; confirm which applies to you.
- [conditional eligibility] "Eligibility Open to all RBS students. Some restrictions apply for team leadership roles. Location Newark Campus, 1 Washington Park Participation 50 to 100 students and alumni Sponsored by Sales Executive Club of Northern New Jersey Foundation Contact Professor Doug Brownstone"
- [operator note] The conditional-eligibility case. Open to Rutgers students generally, but the venture's leadership roles carry a separate RBS senior / MBA / recent alumni requirement. Read the caveats before deciding this is applicable.
- [founder reviews] None. No target page publishes reviews from past student applicants, so this field is empty by construction rather than by omission. Anything here must be typed in by a human.

### What we do not know

`equity_required`, `team_size_min`, `team_size_max`

### Evidence — every field above, traced to the page

| Field | Quoted from the page | Found by |
|---|---|---|
| `organization` | Not stated in a parseable form on the page; taken from the target registry: Rutgers Business School | `registry_fallback` |
| `award_min` | : Company Name Business Owner Award Amount Let’s Move US Hannah Aura Shoval $15,000 DeLintt Frontier Robert DeLintt $15,000 CJPS (Central Jersey Process Service) Dana Chernin and Max Hayden | `regex:award_block` |
| `award_max` | : Company Name Business Owner Award Amount Let’s Move US Hannah Aura Shoval $15,000 DeLintt Frontier Robert DeLintt $15,000 CJPS (Central Jersey Process Service) Dana Chernin and Max Hayden | `regex:award_block` |
| `award_type` | The competition is supported by the Sales Executives Club of Northern New Jersey Foundation, which will provide total cash prizes of $50,000. Judges will be selected from Rutgers Business School MBA Alumni who have succeeded in entrepreneurial endeavors. | `regex:award_type:cash prize` |
| `deadline` | Who is Eligible? Competition Deliverables Judging & Prizes Past Winners Important dates Milestone Due Date Executive Summary (1 page) of Business Plan due December 12, 2025 Top 10 Summaries are selected by Judges January 15 2026 | `regex:deadline_with_year:closing` |
| `degree_levels` | The objective of the annual Business Plan Competition is to encourage student entrepreneurs and support the growth of jobs in New Jersey. In order to compete for the business competition prizes, there must be a serious intent to launch the proposed business. While the competition is open to current Rutgers University students, the leadership roles in the business venture must be filled by one of t… | `regex:eligibility_block` |
| `institution` | The objective of the annual Business Plan Competition is to encourage student entrepreneurs and support the growth of jobs in New Jersey. In order to compete for the business competition prizes, there must be a serious intent to launch the proposed business. While the competition is open to current Rutgers University students, the leadership roles in the business venture must be filled by one of t… | `regex:eligibility_block` |
| `applicant_type` | Eligibility Open to all RBS students. Some restrictions apply for team leadership roles. Location Newark Campus, 1 Washington Park Participation 50 to 100 students and alumni Sponsored by Sales Executive Club of Northern New Jersey Foundation Contact Professor Doug Brownstone | `regex:eligibility_block` |

---

## 4. Rutgers MTC Code for Impact Hackathon

**Run by:** Rutgers Muslim Tech Collaborative
**Source:** <https://mtc-code-for-impact-hackathon.devpost.com/>
**Scraped:** 2026-08-24T01:39:37.268063+00:00
**Review status:** `NEEDS_HUMAN_REVIEW`

### What kind of application is it

non-cash prize

### What the money looks like

| | |
|---|---|
| Award range | **UNKNOWN** — the page does not state this. Not inferred. |
| Deadline as written | **UNKNOWN** — the page does not state this. Not inferred. |
| Deadline as a date | **UNKNOWN** — the page does not state this. Not inferred. |
| Equity taken | **UNKNOWN** — the page does not state this. Not inferred. |

### What it requires

| | |
|---|---|
| Institutions | Rutgers University |
| Degree levels | **UNKNOWN** — the page does not state this. Not inferred. |
| Applicant type | student, student team |
| Team size | **UNKNOWN** — the page does not state this. Not inferred. |

### What past student founders said

- **None available.** No page in this target set publishes reviews from past student applicants, and the scraper does not write this field. Anything that appears here was typed in by a person. Treat the absence as missing information, not as a bad sign about the program.

### Read this before applying

- [non-cash award] "Public 4 non-cash prizes 34 participants Muslim Tech Collaborative Health Machine Learning/AI Social Good Code for Impact is a mission-driven hackathon hosted by Rutgers Muslim Tech Collaborative"
- [operator note] A Rutgers-hosted event on a third-party host. Included as the case where discovery should find something and funding triage should probably reject it: the listed prizes were non-cash. Confirm against the award evidence.
- [off-domain] This page is not on a Rutgers-owned domain. It was fetched because it was named explicitly in the target list, at exactly that URL, and it was not crawled.
- [founder reviews] None. No target page publishes reviews from past student applicants, so this field is empty by construction rather than by omission. Anything here must be typed in by a human.

### What we do not know

`award_min`, `award_max`, `deadline`, `degree_levels`, `equity_required`, `team_size_min`, `team_size_max`

### Evidence — every field above, traced to the page

| Field | Quoted from the page | Found by |
|---|---|---|
| `organization` | Public 4 non-cash prizes 34 participants Muslim Tech Collaborative Health Machine Learning/AI Social Good Code for Impact is a mission-driven hackathon hosted by Rutgers Muslim Tech Collaborative | `regex:hosted_by` |
| `award_type` | Public 4 non-cash prizes 34 participants Muslim Tech Collaborative Health Machine Learning/AI Social Good Code for Impact is a mission-driven hackathon hosted by Rutgers Muslim Tech Collaborative | `regex:award_type:non-cash prize` |
| `institution` | Public 4 non-cash prizes 34 participants Muslim Tech Collaborative Health Machine Learning/AI Social Good Code for Impact is a mission-driven hackathon hosted by Rutgers Muslim Tech Collaborative | `regex:eligibility_block` |
| `applicant_type` | Start late project Find more hackathons View the winners Who can participate Above legal age of majority in country of residence College students only All countries/territories, excluding standard exceptions View full rules View schedule Apr 4, 2026 College Ave Gym | `regex:eligibility_block` |

---

## 5. Rutgers TechStart Innovation Challenge

**Run by:** Rutgers Business School
**Source:** <_no source URL — see caveats_>
**Scraped:** 2026-08-24T01:39:36.838424+00:00
**Review status:** `NEEDS_HUMAN_REVIEW`

### What kind of application is it

**UNKNOWN** — the page does not state this. Not inferred.

### What the money looks like

| | |
|---|---|
| Award range | **UNKNOWN** — the page does not state this. Not inferred. |
| Deadline as written | **UNKNOWN** — the page does not state this. Not inferred. |
| Deadline as a date | **UNKNOWN** — the page does not state this. Not inferred. |
| Equity taken | **UNKNOWN** — the page does not state this. Not inferred. |

### What it requires

| | |
|---|---|
| Institutions | **UNKNOWN** — the page does not state this. Not inferred. |
| Degree levels | **UNKNOWN** — the page does not state this. Not inferred. |
| Applicant type | **UNKNOWN** — the page does not state this. Not inferred. |
| Team size | **UNKNOWN** — the page does not state this. Not inferred. |

### What past student founders said

- **None available.** No page in this target set publishes reviews from past student applicants, and the scraper does not write this field. Anything that appears here was typed in by a person. Treat the absence as missing information, not as a bad sign about the program.

### Read this before applying

- [operator note] Documented by Rutgers Business School as a real initiative, but with no stable standalone application page found at the time of writing. Recorded here with every field UNKNOWN rather than populated from secondhand descriptions. A human must find and add the application URL.
- [no source url] Nothing on this row was extracted from a page. Every field is UNKNOWN until someone finds the application URL and re-runs the scraper.

### What we do not know

`award_type`, `award_min`, `award_max`, `institution`, `degree_levels`, `applicant_type`, `equity_required`, `team_size_min`, `team_size_max`, `deadline`

### Evidence — every field above, traced to the page

_No evidence spans were captured for this row._

---

## 6. ScarletPitch

**Run by:** Innovation, Design, and Entrepreneurship Academy
**Source:** <https://idea.rutgers.edu/programs/scarletpitch>
**Scraped:** 2026-08-24T01:39:36.015366+00:00
**Review status:** `NEEDS_HUMAN_REVIEW`

### What kind of application is it

cash prize

### What the money looks like

| | |
|---|---|
| Award range | $250 – $3,000 |
| Deadline as written | Dec. 21st |
| Deadline as a date | **UNRESOLVED** — the page gives this date without a year, so no calendar date was derived. Guessing the year is exactly the inference this pipeline does not make. |
| Equity taken | **UNKNOWN** — the page does not state this. Not inferred. |

### What it requires

| | |
|---|---|
| Institutions | Rutgers University |
| Degree levels | undergraduate, graduate |
| Applicant type | student |
| Team size | 1 – 5 members |

### What past student founders said

- **None available.** No page in this target set publishes reviews from past student applicants, and the scraper does not write this field. Anything that appears here was typed in by a person. Treat the absence as missing information, not as a bad sign about the program.

### Read this before applying

- The page lists more than one date in a deadline context (Nov. 1st, Dec. 21st, Jan. 29th, Feb. 9th, Feb. 25th). The one recorded above is the earliest date the page labels as a closing date; confirm which applies to you.
- [indirect access route] "Hosted by the Innovation, Design, and Entrepreneurship Academy (IDEA), ScarletPitch is a campus-wide pitch competition that celebrates student entrepreneurship. It brings together students from all disciplines and backgrounds to create businesses designed to be a force for good. Compete for cash and"
- [operator note] The reference example. Rutgers-New Brunswick undergraduate and graduate students, teams of 1-5. Also the qualifying route into UPitchNJ and the Hult Prize, so it is worth more than its own prize money.
- [founder reviews] None. No target page publishes reviews from past student applicants, so this field is empty by construction rather than by omission. Anything here must be typed in by a human.

### What we do not know

`equity_required`

### Evidence — every field above, traced to the page

| Field | Quoted from the page | Found by |
|---|---|---|
| `organization` | Hosted by the Innovation, Design, and Entrepreneurship Academy (IDEA), ScarletPitch is a campus-wide pitch competition that celebrates student entrepreneurship. It brings together students from all disciplines and backgrounds to create businesses designed to be a force for good. Compete for cash and prizes, gain mentorship and visibility, and advance to UPitchNJ or the Hult Prize, all while connec… | `regex:hosted_by` |
| `award_min` | 2027 Competition Prizes 1st Place $3000 2nd Place $2000 3rd Place $1000 Superlatives $250 Johnson & Johnson MENA Middlesex County, NJ IDEA | `regex:award_block` |
| `award_max` | 2027 Competition Prizes 1st Place $3000 2nd Place $2000 3rd Place $1000 Superlatives $250 Johnson & Johnson MENA Middlesex County, NJ IDEA | `regex:award_block` |
| `award_type` | Hosted by the Innovation, Design, and Entrepreneurship Academy (IDEA), ScarletPitch is a campus-wide pitch competition that celebrates student entrepreneurship. It brings together students from all disciplines and backgrounds to create businesses designed to be a force for good. Compete for cash and prizes, gain mentorship and visibility, and advance to UPitchNJ or the Hult Prize, all while connec… | `regex:award_type:cash prize` |
| `deadline` | Apply Now! 2027 Competition Timeline Nov. 1st Application Opens Dec. 21st Priority Deadline Jan. 29th Final Deadline Feb. 9th Development Round Feb. 25th ScarletPitch Finals | `regex:deadline_no_year:closing(unresolved)` |
| `degree_levels` | Undergraduate and Graduate students enrolled at Rutgers-New Brunswick (part-time or full-time) | `regex:eligibility_block` |
| `institution` | Rutgers Bonner Leaders Who can participate? | `regex:eligibility_block` |
| `applicant_type` | Undergraduate and Graduate students enrolled at Rutgers-New Brunswick (part-time or full-time) | `regex:eligibility_block` |
| `team_size_min` | How many people can be on a team? Teams can consist of 1 to 5 members What is the format for the pitch? Each pitch is 5 minutes , followed by a 3-minute Q&A session. Your pitch should address: The problem you're solving The proposed solution | `regex:team_range` |
| `team_size_max` | How many people can be on a team? Teams can consist of 1 to 5 members What is the format for the pitch? Each pitch is 5 minutes , followed by a 3-minute Q&A session. Your pitch should address: The problem you're solving The proposed solution | `regex:team_range` |

---

## 7. UPitchNJ

**Run by:** New Jersey Collegiate Entrepreneurship Consortium
**Source:** <https://innovate.njaes.rutgers.edu/upitchnj-ru-2021/>
**Scraped:** 2026-08-24T01:39:36.470703+00:00
**Review status:** `NEEDS_HUMAN_REVIEW`

### What kind of application is it

competition prize

### What the money looks like

| | |
|---|---|
| Award range | $500 |
| Deadline as written | **UNKNOWN** — the page does not state this. Not inferred. |
| Deadline as a date | **UNKNOWN** — the page does not state this. Not inferred. |
| Equity taken | **UNKNOWN** — the page does not state this. Not inferred. |

### What it requires

| | |
|---|---|
| Institutions | **UNKNOWN** — the page does not state this. Not inferred. |
| Degree levels | **UNKNOWN** — the page does not state this. Not inferred. |
| Applicant type | **UNKNOWN** — the page does not state this. Not inferred. |
| Team size | **UNKNOWN** — the page does not state this. Not inferred. |

### What past student founders said

- **None available.** No page in this target set publishes reviews from past student applicants, and the scraper does not write this field. Anything that appears here was typed in by a person. Treat the absence as missing information, not as a bad sign about the program.

### Read this before applying

- [indirect access route] "The Scarlet Pitch Winner (if an undergraduate team) will represent Rutgers at the annual"
- [operator note] Statewide, and NOT directly applicable. A Rutgers undergraduate reaches UPitchNJ by winning ScarletPitch, not by applying. Treated as an indirect opportunity so it is never surfaced as something to go and apply for.
- [founder reviews] None. No target page publishes reviews from past student applicants, so this field is empty by construction rather than by omission. Anything here must be typed in by a human.

### What we do not know

`deadline`, `degree_levels`, `institution`, `applicant_type`, `equity_required`, `team_size_min`, `team_size_max`

### Evidence — every field above, traced to the page

| Field | Quoted from the page | Found by |
|---|---|---|
| `organization` | May 1 from 2:00 p.m. to 4:30 in a livestreamed, virtual format together with Nokia Bell Labs . The competition was organized by the New Jersey Collegiate Entrepreneurship Consortium, | `regex:hosted_by` |
| `award_min` | The winning team will receive $500. | `regex:award_block` |
| `award_max` | The winning team will receive $500. | `regex:award_block` |
| `award_type` | Those watching can participate by helping select the “Audience Choice” award. | `regex:award_type:competition prize` |

---

## What a human still has to do

1. Open each `source_url` and check the evidence quotes still match the page.
2. Resolve the `UNKNOWN` fields that matter for your situation — usually
   degree level, deadline year and whether the award is per team.
3. Fill in `founder_reviews` from people who actually competed.
4. Set `review_status` to `ACCEPTED` or `REJECTED` in
   `data/opportunities.rutgers.candidates.json`.
5. Only then move accepted rows into `data/opportunities.candidates.json` and
   run `uv run python scripts/verify_seed.py`.

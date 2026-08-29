"""Where outside data becomes an internal record.

`discovery` and `campus` fetch; `extraction` verifies what a model claimed
about a page against the page's own words; `eligibility` is the pure-Python
filter that turns a founder and an opportunity into a verdict.

Nothing in `eligibility` imports `agent.config` — the filter has to stay
runnable with no credentials and no configuration at all.
"""

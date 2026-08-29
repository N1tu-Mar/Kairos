"""The operator-run scraper: fetch, extract with evidence, hand to a human.

Nothing here reaches a founder on its own. Every row it produces is written
as `NEEDS_HUMAN_REVIEW`, and `agent/tools/campus.py` loads only rows a person
has since marked `ACCEPTED`.

`robots` and `fetch` are the politeness layer — robots.txt is honoured, one
request per host per crawl delay, and every page fetched is archived so an
extraction can be re-checked against the bytes it was made from.
"""

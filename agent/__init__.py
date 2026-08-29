"""Kairos agent: discovery, judgment, drafting, and the rules that bound them.

Layered, and the layering is the design:

*   `models` — Pydantic contracts. Imports nothing from this package.
*   `guardrails`, `sanitize`, `semantic`, `tools/eligibility` — deterministic.
    These must stay importable with no AWS credentials and no `.env`, which
    is why none of them import `config`.
*   `config`, `runtime`, `prompting`, `budget` — the model layer's plumbing.
*   `subagents/` — the three model calls. Each returns a validated model.
*   `tools/`, `scraping/` — where untrusted input enters.
*   `scout` — the orchestrator that runs one pipeline pass.

The rule that shapes all of it: models extract, deterministic code decides.
"""

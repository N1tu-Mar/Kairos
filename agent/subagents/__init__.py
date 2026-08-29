"""The three model calls, and nothing else.

Assessor judges one opportunity, Drafter writes answers, Auditor checks them
against the knowledge base. Each builds its agent from a prompt file on disk,
returns a validated Pydantic model, and records the prompt's git blob hash on
whatever it produced.

The Auditor is deliberately given less context than the Drafter — see
`auditor.render_context`.
"""

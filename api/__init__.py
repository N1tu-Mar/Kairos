"""The HTTP surface and its persistence.

`main` is the FastAPI app, `auth` the identity and authorization layer,
`jobs` the durable boundary between a request and a run, and `repository`
the only place SQL is written.

Nothing under `agent/` imports from this package — the pipeline talks to the
`Repository` protocol and knows nothing about HTTP.
"""

"""Offline test suite. No AWS, no network, no model calls.

`conftest` supplies the environment and the agent fakes, `factories` builds
synthetic records, and `golden_set/` is the drafting eval harness.

Every fixture is marked `[DEMO]` and left unverified, so nothing here can be
mistaken for a curated row if it is copied into a data file.
"""

"""Loading the seed catalog.

The verification boundary: an unverified row is excluded unless a caller
explicitly opts in, and a malformed file is an error rather than an empty
catalog.
"""

from __future__ import annotations

import json

import pytest

from agent.tools.discovery import SeedCatalog, SourceError
from tests.factories import TODAY, opportunity


def test_seed_catalog_excludes_unverified_rows_and_keeps_verified_rows(tmp_path):
    path = tmp_path / "seed.json"
    verified = json.loads(opportunity(id="verified", verified=True).model_dump_json())
    unverified = json.loads(opportunity(id="unverified", verified=False).model_dump_json())
    verified["verification_note"] = "human checked"
    verified["_reviewer"] = "fixture"
    path.write_text(json.dumps([verified, unverified]))

    found = SeedCatalog(path).fetch()

    assert [o.id for o in found] == ["verified"]


def test_seed_catalog_can_explicitly_opt_into_unverified_demo_rows(tmp_path):
    path = tmp_path / "seed.json"
    row = json.loads(opportunity(id="demo_candidate", verified=False).model_dump_json())
    path.write_text(json.dumps([row]))

    found = SeedCatalog(path, allow_unverified=True).fetch()

    assert [o.id for o in found] == ["demo_candidate"]


def test_seed_catalog_fails_loudly_on_malformed_json(tmp_path):
    path = tmp_path / "seed.json"
    path.write_text("{not-json")

    with pytest.raises(SourceError):
        SeedCatalog(path).fetch()

# Backend Method Suites

Grouped pytest suites for the major backend methods and the future methods
needed to complete Kairos.

Run all current and future-target suites:

```bash
uv run pytest backend_method_suites
```

Run one method group:

```bash
uv run pytest backend_method_suites/check_opportunity
```

Future-facing tests are marked `xfail`. They describe desired ground truth for
missing work, such as authentication, semantic recall, and production
scheduling, without breaking the current suite.

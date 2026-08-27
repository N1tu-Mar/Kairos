# Recommended branch protection

**Nothing in this file has been applied.** Repository settings are not
Terraform-managed and this file changes none of them — it is the
recommendation, written down, for whoever owns the repository to apply.

## On `main`

Settings → Branches → Add rule, pattern `main`:

- **Require a pull request before merging.** At least one approval.
- **Dismiss stale approvals when new commits are pushed.** An approval of the
  diff from three pushes ago is an approval of something else.
- **Require status checks to pass**, and require branches to be up to date
  before merging. The checks:

  | Check | Job in `ci.yml` |
  |---|---|
  | `Python tests` | `python` |
  | `Migrations` | `migrations` |
  | `Frontend` | `frontend` |
  | `Whitespace and secrets` | `hygiene` |
  | `Terraform` | `terraform` |
  | `Docker image` | `docker` |

  **`Dependency and image scan` is deliberately not required.** It reports to
  the Security tab. A scanner that fails the build on every new CVE in a
  transitive dependency teaches people to merge past it, and a check everyone
  bypasses protects nothing. The response policy is in `ci.yml` and
  `docs/security.md`; the one finding that does gate the build is a detected
  secret, which is handled inside the job rather than by branch protection.

  **`Integration (credentialed)` must never be required.** It needs AWS
  credentials and costs money per run, so it cannot run on a fork's pull
  request at all.

- **Require conversation resolution before merging.**
- **Do not allow bypassing the above settings**, including for
  administrators. An exception that exists is an exception that gets used at
  4pm on a Friday.
- **Require linear history.** Every commit on `main` should be a revert
  target that makes sense on its own.

## Actions permissions

Settings → Actions → General:

- **Allow only actions pinned to a full commit SHA**, which is what the
  workflows here do. A tag is mutable: `@v4` today and `@v4` next month can
  be different code, and a compromised action runs with whatever permissions
  the workflow holds.
- **Default `GITHUB_TOKEN` permissions: read-only.** The workflows escalate
  explicitly where they need to (`security-events: write` for the SARIF
  upload, `id-token: write` for OIDC).

## The integration environment

Settings → Environments → `integration`:

- **Required reviewers**, so a manual dispatch that spends money is a
  deliberate act by a named person.
- **Deployment branch rule**: `main` only.
- Store `AWS_INTEGRATION_ROLE_ARN` as an environment secret, and
  `BEDROCK_MODEL_REASONING` / `BEDROCK_MODEL_CLASSIFY` / `AWS_REGION` as
  environment *variables* — a model ID is not a credential and does not
  belong in the secret store, where it becomes invisible to everyone
  debugging a region mismatch.

There are no long-lived AWS keys anywhere in this repository's secrets. The
integration workflow assumes a role via OIDC, and that role should be scoped
to `bedrock:InvokeModel` on the two model ARNs and nothing else.

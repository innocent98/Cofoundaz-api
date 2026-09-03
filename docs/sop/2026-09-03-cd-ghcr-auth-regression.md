# SOP — CD staging deploy failed on `username is empty`: the GHCR-auth regression

> **Type:** fix / deploy pipeline · **Date:** 2026-09-03 · **Area:** `.github/workflows/cd-staging.yml`, `.github/workflows/cd-production.yml`, `.github/actions/deploy-stack/action.yml`

## What shipped

The first-ever `cd-staging.yml` run (**run `33776562659`**, `develop` at `f96172c`) failed in
`Deploy to staging` → step `Deploy the staging stack`, 22 seconds in. Two regressions introduced
by the branching restructure (PR #42) are fixed, plus one long-standing warning removed:

| # | Fix | File |
|---|---|---|
| 1 | `ghcr_username`/`ghcr_token` back to `github.actor` / `secrets.GITHUB_TOKEN` | `cd-staging.yml`, `cd-production.yml` |
| 2 | `permissions: packages: read` restored on both deploy jobs | `cd-staging.yml`, `cd-production.yml` |
| 3 | Preflight step that fails fast, by name, on any empty deploy input | `deploy-stack/action.yml` |
| 4 | `script_stop: true` removed (invalid input at the pinned version) | `deploy-stack/action.yml` |

## Why

### The error

```
==> [staging] [2/6] Authenticating to GHCR and pulling by digest
2026/09/03 16:07:54 Process exited with status 1
username is empty
```

The step's own env echo shows the cause directly — an unmasked, **empty** value:

```
IMAGE_REF: ghcr.io/innocent98/cofoundaz-api@sha256:1277e5c8…
DEPLOY_PATH: ***
GHCR_USER:              <-- empty; every other secret rendered as ***
GHCR_TOKEN: ***
STACK_ENV: staging
```

`docker login ghcr.io -u "" --password-stdin` is what `drone-ssh` reported as `username is empty`.

### Root cause — a revert the restructure did not know it was making

`GHCR_PULL_USERNAME` / `GHCR_PULL_TOKEN` were a long-lived PAT pair that was **removed** weeks
earlier, when the VPS-side `docker login` moved to the run's own `GITHUB_TOKEN`. The working
`cd.yml` on `main` (`0de05f4`) passed:

```yaml
ghcr_username: ${{ github.actor }}
ghcr_token: ${{ secrets.GITHUB_TOKEN }}
```

When `cd.yml` was split into `cd-staging.yml` + `cd-production.yml`, both call sites were
rewritten back to the removed secrets. `GHCR_PULL_USERNAME` has **never existed** on this
repository, so it resolved to `""`. `GHCR_PULL_TOKEN` *does* still exist as a stale repo-level
secret — which is why the token masked as `***` and only the username was visibly empty, making
the failure look like a token problem rather than a wiring problem.

The restructure carried the *corrected* documentation forward unchanged —
`GITHUB_ACTIONS_SETUP.md` §4 already said "There is nothing to create here… do not set them" —
while reverting the workflow. Docs and workflow disagreed; the workflow won.

### Why the second fix was mandatory

Both deploy jobs also **lost their `permissions:` block** in the split, inheriting the
workflow-level `contents: read` — so `packages` was `none`. Restoring the `GITHUB_TOKEN` wiring
alone would have produced a *second* failed deploy, one step later, on `docker pull` denied for
a private package. The two fixes are only correct together.

### Why the composite action hid it

A composite action does **not** enforce `required: true`. A missing input, or one wired to a
non-existent secret, silently becomes `""` and execution continues. The failure therefore
surfaced ~20 lines later as an opaque message from a third-party binary — *after* the `.env` and
compose file had already been scp'd to the VPS. Hence fix #3.

## What's involved

| Path | Change |
|---|---|
| `.github/workflows/cd-staging.yml` | `staging-deploy`: added `permissions: {contents: read, packages: read}`; `ghcr_username`→`github.actor`, `ghcr_token`→`secrets.GITHUB_TOKEN` |
| `.github/workflows/cd-production.yml` | `production-deploy`: same two changes; corrected the now-false comment in `resolve-artifact` that described the PAT as still in use by the VPS |
| `.github/actions/deploy-stack/action.yml` | New first step `Preflight - every deploy input is present`; `script_stop: true` removed; `ghcr_username`/`ghcr_token` input descriptions corrected (they still specified a PAT) |

No change to `ci.yml`, `codeql.yml`, `live-e2e.yml`, the deploy script body, or the cost model.

## Verification

| Check | Result |
|---|---|
| `actionlint` (all 5 workflows + composite action) | exit 0 — also clean *before* the fix, i.e. actionlint cannot catch either bug |
| `shellcheck -S style` on all 4 extracted shell blocks | exit 0 |
| YAML parse + job-graph dump | both deploy jobs now show `packages: read`; `needs:` chains unchanged |
| No live `secrets.GHCR_PULL_*` expression remains | confirmed; remaining hits are explanatory comments |
| Preflight, executed locally, `GHCR_USERNAME=""` | exits 1 with `::error::Empty deploy input(s): GHCR_USERNAME …` — reproduces the real failure *before* anything ships |
| Preflight, executed locally, all 8 inputs set | exits 0 |

> **NOT VERIFIED —** no workflow run has executed this fix. The GHCR pull from a VPS has still
> never succeeded. Confirmation requires a push to `develop`; see below.

## Operate / roll back

**To verify for real:** push any commit to `develop` and watch run → `Deploy to staging`. Expect
`[2/6] Authenticating to GHCR and pulling by digest` to proceed past `docker login` into
`docker pull`, then `[6/6]` readiness against `http://127.0.0.1:8001/api/v1/health/ready`.
The `Unexpected input(s) 'script_stop'` annotation should be gone.

**State left behind by the failed run — worth knowing.** `docker login` sits at
`action.yml:169`, but `trap rollback ERR` is not installed until `action.yml:211`. The failure
therefore happened **before the rollback trap existed**: no rollback ran, no `api` logs were
dumped, and `[3/6]` never pinned `API_IMAGE`. The running container was never touched — staging
kept serving `…@sha256:e4cfac69…` and returns 200 — but the scp step at `action.yml:100` had
already completed with `overwrite: true`, so `$DEPLOY_PATH/.env` and `docker-compose.prod.yml`
on the staging box **were replaced**, and that `.env` carries no `API_IMAGE=` line. A manual
`docker compose up` on that host before the next successful deploy would fall back to the
compose default rather than the digest actually running. The next green deploy overwrites both
files and pins `API_IMAGE`, resolving it.

**Roll back this change:** `git revert` the merge; nothing here is stateful.

## Follow-ups

- [ ] **Delete the stale `GHCR_PULL_TOKEN` repo secret.** It is now referenced by nothing and is
      a long-lived `read:packages` credential sitting in the repo. Adebayo's action:
      Settings → Secrets and variables → Actions.
- [ ] Re-run staging and confirm the GHCR pull from a VPS, closing the oldest **NOT VERIFIED**
      note in `GITHUB_ACTIONS_SETUP.md` §4.
- [ ] Consider asserting in CI that every `${{ secrets.X }}` referenced by a workflow actually
      exists — this class of bug is invisible to `actionlint` and to YAML validation, and it
      failed *after* shipping files to the VPS.

### Found while auditing this fix — separate configuration gaps, NOT fixed here

- [ ] **The `production` environment has no required reviewers** (`protection_rules: []`).
      Three places assert otherwise and lean on it as *the* production gate:
      `cd-production.yml:58-59`, `cd-production.yml:291-292`, `live-e2e.yml:59-62`. As it
      stands, `bypass_staging_proof` would deploy an unverified image unattended, and a
      `workflow_dispatch` of Live E2E against production would sign up real users with no
      approval prompt. Adebayo's action: Settings → Environments → `production`.
- [ ] **`main` has no linear-history branch protection**, which the fast-forward promotion model
      depends on (already tracked in the checklist). Adebayo's action.
- [ ] **`ENV_ENCRYPTION_KEY` and all four `VPS_*` are repository-scoped, not environment-scoped.**
      `cd-production.yml:290-291` claims the `production` Environment supplies them; only
      `DEPLOY_PATH` actually is. It works, but one key encrypts both `.enc` files — a staging
      rotation breaks production, and a staging workflow can decrypt the production env. Either
      move them per-environment or correct the comment.
- [ ] **`live-e2e.yml` does not guard `DEPLOY_PATH`.** `cd "${DEPLOY_PATH}"` (line 106) *succeeds*
      when the value is empty (it lands in `$HOME`); the run then fails at `docker compose`,
      naming compose rather than the secret. It fails closed, so this is a diagnosability
      problem rather than a safety hole — same class as the bug fixed here, deliberately left
      alone to keep this diff scoped.
- [ ] **Environment-scoped secrets inside a *called* reusable workflow are unproven.**
      `staging-e2e` is a `uses:` job and cannot declare `environment:`, so resolution depends on
      the called job's own declaration (`live-e2e.yml:63-64`). Documented behaviour, but the
      previously-green path was an inline job — run history proves nothing about this variant.
      Prove it by `workflow_dispatch`-ing Live E2E against staging alone, which is only possible
      once `develop` reaches `main` (dispatch is offered only for workflows on the default
      branch). For the same reason **both documented rollback paths are unavailable today** —
      worth knowing before relying on them during an incident.

# SOP — Staging gate + encrypted environment files

> **Type:** infra / deploy pipeline · **Date:** 2026-08-28 · **Area:** CD, env management, e2e harness

## What shipped

A three-stage gated deploy — `staging-deploy` → `staging-e2e` → `production-deploy` — and
the encrypted-environment pattern from Adebayo's `backend-devops-template`. Production is
now reachable only through a green live-E2E run against deployed staging. Environment
files reach both VPSes as committed AES ciphertext decrypted by CD, so no server path and
no plaintext secret exists anywhere in the repository.

Two incorrect assumptions baked into the previous deployment work are corrected: the VPS
paths, and the name of the environment file on the server.

## Why

**The deploy path was documented against a directory that does not exist.** `/opt/cofoundaz-api`
was written into the guide; the real stacks are `/opt/cofoundaz` and `/opt/cofoundaz-staging`.
Substituting the string would have recreated the same failure a month later, so the fix is
structural: `DEPLOY_PATH` is a **per-GitHub-Environment secret** and no server path appears
in the repo at all. The `staging` environment resolves one, `production` the other.

**The environment file on the server is `.env`, not `.env.production`.** `docker-compose.prod.yml`,
the `Makefile` and the docs all assumed otherwise, so the compose file would not have found
its environment on a real box.

**There was no staging gate.** CD went straight to production behind a manual approval.
Approval is a human reading a diff; it is not evidence the artifact runs.

## How

- **`DEPLOY_PATH` as a per-environment secret** rather than a constant. This is the whole
  correction: the previous value went stale precisely because it lived in a document. Now
  the repository cannot know or contradict it.
- **Deployed filename is `.env`.** `.env.staging` / `.env.production` remain as *local*
  plaintext working copies, so one machine can hold both environments without collision.
  CD decrypts `.env.<env>.enc` → writes `.env` → scp's that.
  - `docker-compose.prod.yml` uses `env_file: ${STACK_ENV_FILE:-.env}`. It defaults to the
    server's `.env`; `make prod-*` overrides it to `.env.production` so exercising the prod
    stack locally never reads or clobbers the DEV `.env` that `docker-compose.yml` uses.
    Verified that Compose interpolates `env_file` paths before adopting this.
- **One composite action for both deploys** (`.github/actions/deploy-stack`). Staging and
  production run an identical sequence and differ only in which Environment supplies the
  secrets. Two copies of ~100 lines of deploy shell would drift, and the copy that drifts
  is the one discovered during an incident.
- **A decrypt failure is fatal.** The template tolerates a missing `.enc` on the theory that
  the server already has a usable `.env`. That was rejected: the deploy would then run
  against whatever stale environment happens to be on the box — unauditable and
  unreproducible. Missing ciphertext, missing key, or a decrypt yielding fewer than five
  variables all abort the deploy.
- **GHCR digest pull retained** against the template's `docker save` tarball + scp. A digest
  is content-addressed, so the registry cannot serve different bytes under it, and BuildKit's
  SBOM and SLSA provenance attestations have nowhere to live inside a tarball. The cost —
  stated rather than hidden — is a GHCR credential on each VPS and a dependency on ghcr.io
  at deploy time.
- **Production deploys the digest staging proved**, not a rebuild, so the two environments
  provably run identical bytes.

## What's involved

| File | Change |
|---|---|
| `scripts/env.sh` | new — six subcommands, three-tier key discovery, AES-256-CBC + PBKDF2 100k. Key passed to openssl on a file descriptor so it never reaches `ps` or shell history |
| `.gitignore` | re-allows `.env.staging.enc` / `.env.production.enc` while still denying `.env`, `.env.staging`, `.env.production`, `.env.key` |
| `.github/actions/deploy-stack/action.yml` | new — decrypt → scp → pull by digest → migrate → readiness → rollback, shared by both environments |
| `.github/workflows/cd.yml` | restructured to `build-and-push` → `staging-deploy` → `staging-e2e` → `production-deploy` |
| `docker-compose.prod.yml` | `env_file` is now `${STACK_ENV_FILE:-.env}` |
| `Makefile` | prod targets use `PROD_ENV_FILE`/`STACK_ENV_FILE`; new `env-*` targets wrapping `scripts/env.sh` |
| `e2e/conftest.py` | `E2E_REMOTE=1` mode — auto-deselects mailbox-dependent tests, reports each one, errors if nothing would run |
| `docs/deployment/ENV_ENCRYPTION.md` | new |
| `docs/deployment/{DEPLOYMENT_GUIDE,GITHUB_ACTIONS_SETUP,ROLLBACK}.md` | two environments, `.env` filename, staging gate, `/opt/cofoundaz-api` removed |

CI (`ci.yml`, `codeql.yml`) was explicitly **out of scope and is unchanged** — all ten jobs
and every gate inside them are intact.

## The staging E2E gate — what it does and does not prove

This is the part most likely to be over-read, so it is stated plainly.

The `e2e/` suite has **27 tests. Only 13 can run against a deployed staging.** The other 14
need one-time tokens read out of file-captured emails (`EMAIL_BACKEND=file` +
`EMAIL_FILE_DIR`), and that directory is on the VPS, not on the runner. They reach the
`mailbox` fixture — most of them transitively through `make_verified_user`, which consumes
it internally, which is why a naive scan of test signatures undercounts them.

| | Count | Gates production? |
|---|---|---|
| Run against deployed staging | **13** | **yes** |
| Deselected (need a local mail dir) | **14** | no — but they run in CI's `e2e` job on every push |

`E2E_REMOTE=1` deselects them **automatically**, by fixture closure rather than a
hand-maintained list, so a new mailbox-dependent test is excluded the moment it is written
instead of failing confusingly in CD months later. Every deselection is printed with its
nodeid. If deselection would leave zero tests, the harness raises rather than reporting a
green run that asserted nothing — and the CD job additionally fails on pytest exit code 5,
deliberately unlike the template, which treats "no tests collected" as success.

**What the gate proves:** staging is alive, correctly wired to Postgres and Redis, serving
its OpenAPI, and enforcing auth, validation and 404 behaviour.
**What it does not:** signup-verify, MFA, password reset, refresh rotation, onboarding,
roadmap, mission and assessment journeys — against the *deployed artifact*. Those are
covered by CI against a locally booted server, so they are not unverified; they are just
not verified against the deployment.

To widen it: ship `EMAIL_FILE_DIR` back to the runner between the signup call and the token
read, or run the suite on the VPS where the directory is local. Neither is done.

**Rate limiting:** the template flushes limiter keys from Redis. That would be a no-op here
— `app.state.limiter._storage` is `MemoryStorage`, because the Limiter is built without a
`storage_uri`, so state lives in each gunicorn worker's memory. The job restarts the `api`
service and re-waits on readiness, which actually clears it.

## Verification

Local, 2026-08-28:

- `black` / `isort` / `ruff` (incl. C901) / `mypy` / `pylint 9.94` / `bandit` / `hadolint` — all clean.
- **406 unit tests passed, 98.25% coverage**; **27 e2e passed** (local mode unchanged).
- **Remote-mode gate: `13 passed, 14 deselected`** against a live HTTP server on `:8021`.
- `actionlint` exit 0 across `ci.yml`, `cd.yml`, `codeql.yml` and both composite actions.
- `shellcheck -S warning` clean on `scripts/env.sh` and `scripts/e2e_run.sh`.
- `make scan` — bandit, Semgrep, gitleaks, `trivy config`, Checkov (**91 passed / 0 failed /
  1 documented skip**) all green; `trivy image` **0 findings**; `trivy fs` 1 (the unfixable
  `ecdsa`).
- `docker compose -f docker-compose.prod.yml --env-file .env config` parses in the **server
  shape**, and `make prod-config` in the **local shape**.
- `env.sh` full round-trip **on fake data in a throwaway directory, deleted afterwards**:
  encrypt→decrypt byte-identical (mode 600), ciphertext starts `Salted__` with no plaintext
  present, both key-discovery tiers work, wrong key exits 1 and leaves no partial file,
  `rotate` invalidates the old key, `diff` masks values, `CHANGE_ME` guard fires.
- `git add --dry-run` proof: `.env`, `.env.staging`, `.env.production`, `.env.key` **blocked**;
  `.env.staging.enc`, `.env.production.enc`, `.env.example`, `.env.production.example` **addable**.

## Operate / roll back

- Environment changes: edit locally → `./scripts/env.sh encrypt <env>` → commit the `.enc` →
  redeploy. Editing `.env` on the server is pointless — the next deploy overwrites it from
  the committed ciphertext.
- Rollback is unchanged in mechanism (ERR trap restores the previous running image, with
  `--no-deps` so the migrate one-shot is not re-run on old code) and now exists identically
  in both environments. The manual `workflow_dispatch` + `image_tag` path now also passes
  through staging first, so a rollback is gated too — slower, and safer.
- `ENV_ENCRYPTION_KEY` rotation is safe and documented. `MFA_ENCRYPTION_KEY` rotation is
  **not** — it permanently locks out every MFA-enrolled user. Different keys, different
  consequences.

## Follow-ups

- **NOT VERIFIED: nothing has run against a real VPS.** No SSH deploy, no scp, no GHCR pull
  from a server, no staging host exists yet. The CD workflow has never executed in this
  three-job shape. Everything above is static validation plus local execution.
- **No secret has been created.** `scripts/env.sh` ships ready; `.env.staging.enc` and
  `.env.production.enc` do not exist yet and cannot be created here. The one-time setup is
  §4 of `docs/deployment/ENV_ENCRYPTION.md` and is Adebayo's to run.
- The 14 mailbox-dependent journeys do not gate production. Widening needs mail-dir
  transport or an on-VPS runner.
- `APP_URL` must be set as a **staging** environment variable, not just production — the
  gate uses it as `E2E_BASE_URL` and the job fails loudly if it is empty.
- During this work the local dev `.env` was destroyed by a `.gitignore` proof that wrote and
  deleted a real path. It was reconstructed and re-verified (406 unit / 27 e2e green), but
  `SECRET_KEY` and `MFA_ENCRYPTION_KEY` are newly generated — any JWT issued locally before
  2026-08-28 is invalid. Test fixtures at real paths are the lesson.

# SOP — Production deployment hardening (compose, image, CI/CD, readiness)

> **Type:** infra / deploy hardening · **Date:** 2026-08-27 · **Area:** Docker, docker-compose,
> GitHub Actions, health checks, code/quality/security scanning

## What shipped

A production-ready deploy path where none existed: a hardened `docker-compose.prod.yml` stack
(no exposed DB/Redis ports, migration gate, resource limits, log rotation), a rewritten
production `Dockerfile` (tini + gunicorn workers, no build tooling in the runtime image), a
**10-job CI pipeline (was 1)**, a build/push/SSH-deploy CD workflow with automatic rollback, and a
real `GET /api/v1/health/ready` endpoint that checks Postgres and Redis instead of returning a
static string.

A second wave then added the scanning layer: a separate **CodeQL** workflow, **Dependabot** for
the pip / Actions / Docker pins, a `quality` job (**pylint**, **radon**, **hadolint**), a
`trivy-repo` job (`trivy fs` + `trivy config`), **bandit** inside `security`, a PR-only
**`dependency-review`** job, an opt-in **SonarCloud** job, a CycloneDX **SBOM** on the built
image, and distinct SARIF categories per scan mode. **Everything is in the working tree —
nothing is committed.** This is the first pass; the user commits it.

## Why

`GET /health` returning a static string meant a deploy gate could pass with a dead database
(finding 4). `docker-compose.yml` was the only compose file and it published Postgres/Redis
directly to the host (finding 7), with no resource limits or log rotation anywhere (finding 8)
— a runaway container could OOM the host or fill the disk. `poetry.lock` was gitignored while
the Dockerfile used a `poetry.lock*` glob, so container builds re-resolved dependencies on every
build — not reproducible — and this also masked that the Dockerfile/CI pinned Poetry 1.8.4,
which cannot read the project's lock-version 2.1 lockfile (finding 1). CI was a single job that
never built the image, never scanned it, and ran Postgres 15 against a compose file running
Postgres 17 (finding 9). Nothing ran `alembic upgrade head` on deploy (finding 2), and
`.env.example` shipped a real-looking 64-hex `SECRET_KEY` (finding 3).

Two bugs were self-inflicted during this pass and are recorded because they'd otherwise recur:
the prod compose initially shared its project name with dev, so `docker compose -f
docker-compose.prod.yml down -v` deleted the **dev** stack's Postgres volume (finding 10); and
`rm -rf .../{pip,setuptools}` in the Dockerfile silently did nothing because Docker `RUN` uses
`/bin/sh` (dash), which has no brace expansion (finding 11) — it left two phantom Trivy findings
until rewritten with explicit paths.

## How

- **Reproducible builds first.** Un-gitignored `poetry.lock`, standardised on Poetry 2.2.1 across
  Dockerfile and CI (replacing the 1.8.4 pin that couldn't read the lockfile), and staged the
  lockfile so the next build resolves deterministically instead of fresh every time.
- **Migration as a gated one-shot service**, not a manual step or an entrypoint hack: a `migrate`
  service runs `alembic upgrade head` and exits; `api` depends on it via
  `condition: service_completed_successfully`, so a broken migration blocks the API from ever
  starting instead of starting against a stale schema.
- **Readiness vs. liveness split**, deliberately: `/health` (liveness) stays a cheap static
  check so orchestrators don't restart a container just because a dependency is slow; the new
  `/api/v1/health/ready` checks Postgres + Redis and returns `503` naming which dependency
  failed, with no DSN or connection-string leak — verified both states live (see Verification).
- **Runtime image stripped to what actually runs it.** `libpq-dev` was a build-time headers
  package; verified via `ldd` that `psycopg2-binary` bundles its own `libpq`, so it was removed
  entirely rather than "kept just in case." `pip`/`setuptools`/`wheel` removed from both the venv
  and the base image for the same reason — they're not needed after the wheel is built. Rejected
  keeping them "for debugging" — a smaller runtime image is a smaller CVE surface, and Trivy is
  the tool for debugging in that scenario, not a live package manager in prod.
- **tini as PID 1 + gunicorn with 4 uvicorn workers**, replacing a single bare `uvicorn` process
  that used one core and had no zombie-reaping init and no worker recycling.
- **Network exposure trimmed to the actual production topology.** Production compose publishes
  neither Postgres nor Redis to the host and binds the API to `127.0.0.1` for nginx to front —
  matching the project's existing nginx-in-front convention rather than exposing the API
  directly.
- **Prod stack gets its own compose project name** (`cofoundaz-api-prod`), specifically so a
  `down -v` on one stack can never delete the other's volumes — the failure mode hit and fixed
  during this pass (finding 10).
- **CI split into fail-fast stages.** `lint`, `test` (Postgres 17 now, matching compose),
  `migrations`, `e2e` and `security` all run in parallel; `build` gates on all five. Trivy runs
  inside `build` rather than as its own job because it scans the resolved image filesystem, so it
  needs the image the same job just produced. Semgrep lives in `security` alongside gitleaks and
  `pip-audit`. The two scanners stay distinct in purpose — Semgrep reads source patterns, Trivy
  reads dependency/OS CVEs — and neither finds what the other finds.
- **CD pushes to GHCR and the VPS pulls from it**, deploying by immutable **digest** rather than
  tag. This is a deliberate departure from the scp-tarball default: it costs a GHCR credential on
  the VPS (`GHCR_PULL_TOKEN`, a PAT with `read:packages`, since the package is private by default)
  and a network dependency on `ghcr.io` at deploy time. The trade bought for that is a
  content-addressed artifact — what runs is byte-identical to what CI built and scanned, and
  rolling back is re-pinning a digest rather than trusting whatever tarball is still on disk.
  Rollback is automatic: an `ERR` trap restores the previous image, restarts, and re-verifies
  health before failing the run.
- **Code / quality / security scanning built as parallel jobs with an explicit
  blocking split.** Ten CI jobs now run, eight of them fully parallel, so breadth
  of scanning costs wall-clock only for the slowest job rather than accumulating.
  What BLOCKS the build was decided per-scanner rather than by default:
  black/isort/ruff (incl. C901)/mypy, pylint, hadolint, coverage, migrations, e2e,
  gitleaks, Semgrep, bandit, pip-audit, `trivy config` and `trivy image` all gate;
  CodeQL, `trivy fs`, radon metrics and SonarCloud report to the Security tab and
  never gate. The two report-only security scanners are deliberate: CodeQL has no
  triaged baseline yet and blocking a brand-new broad ruleset on day one is how
  teams end up deleting the job; `trivy fs` overlaps pip-audit, which already
  blocks on the same dependency CVEs, so its value is the wider view (the locked
  graph including *unfixable* advisories) rather than a second identical veto.
- **CodeQL is a separate workflow, not a CI job**, because it needs a weekly cron
  to keep a current baseline — and a cron in `ci.yml` would run the whole
  ten-job pipeline, Postgres services and e2e included, once a week to produce one
  analysis. It complements Semgrep rather than duplicating it: Semgrep is
  syntactic pattern matching over one window at a time; CodeQL does
  interprocedural dataflow/taint tracking and can follow a request parameter
  through several functions into a sink, which is a class of bug Semgrep
  structurally cannot see.
- **`pylint` was decoration and is now a gate.** It had been a declared dev
  dependency that CI never invoked. Rather than dropping it, it was scoped: a
  `[tool.pylint]` block disables only what ruff/black/mypy already own, plus two
  genuine SQLAlchemy false positives, each with a written reason. That moved the
  signal from 527 messages / 7.48-10 to 15 messages / 9.94-10.
- **Scanner findings were fixed, not silenced, wherever fixing was possible.**
  hadolint's DL3066 became `USER 1000:1000` (a numeric id is verifiable by
  runtimes that enforce runAsNonRoot; a username is not). bandit's three findings
  were all false positives and carry inline `# nosec <ID>` annotations *at the
  site* with reasons, so the same rule still fires anywhere else. Only DL3008
  is ignored wholesale, because pinning an apt version against Debian's
  single-version archive would make the build fail the moment tini is patched -
  and the base-image digest pin already provides the reproducibility it is after.
- **Checkov added for workflow policy, NOT for compose — because it cannot do
  compose.** It was brought in specifically to close the docker-compose gap that
  `trivy config` leaves, and it does not close it: Checkov 3.3.15 has no
  `docker_compose` framework at all, and under the generic `yaml` framework it
  emits no check_type and no results for either compose file. A job pointed there
  could never fail, which reads as coverage while providing none. It was wired to
  `github_actions` instead, where it adds genuine workflow security *policy* that
  actionlint (syntax and typing) does not — and it earned that on first run.
  **Net result: both compose files remain unscanned by any IaC tool.**
- **Nothing suppressed in the security-exception files.** `.github/security/pip-audit-ignores.txt`
  and `.trivyignore` were created so the pattern exists, but both currently allow-list nothing —
  the CVE debt below is left visible on purpose rather than silently exempted, so CI is expected
  to go red on first run until it's triaged (see Follow-ups).

## What's involved

| File | Change |
|---|---|
| `docker-compose.prod.yml` | new — production stack: no host-published DB/Redis, `migrate` one-shot gated by `service_completed_successfully`, resource limits, json-file log rotation, own project name `cofoundaz-api-prod` |
| `docker-compose.yml` | made honestly dev-only (bind-mounted source, exposed DB/Redis for tooling) |
| `Dockerfile` | rewritten — Poetry 2.2.1, no `libpq-dev`/pip/setuptools/wheel in runtime, tini + gunicorn/uvicorn workers, explicit-path cleanup (no brace-expansion bug) |
| `.dockerignore` | inverted to deny-by-default allowlist |
| `.env.production.example` | new — full production env template |
| `.env.example` | real-looking `SECRET_KEY` replaced with placeholder + generation command; missing vars added |
| `.gitignore` | `poetry.lock` un-ignored; `.env.*` denied with templates re-allowed |
| `poetry.lock` | now tracked (git staged, not committed) |
| `.github/workflows/ci.yml` *(first pass)* | 1 job → 6: `lint` (now incl. mypy), `test` on Postgres 17 with a 95% coverage gate, `migrations` (single-head + fresh upgrade + `alembic check` + downgrade round-trip), `e2e`, `security` (gitleaks + Semgrep + pip-audit), `build` (image + smoke + Trivy) |
| `.github/workflows/cd.yml` | new — build once, push to GHCR, deploy the immutable digest over SSH; `production` environment gate, `concurrency: cd-production` (no cancel), automatic rollback via `ERR` trap |
| `.gitleaksignore` | new — baselines one historical committed `SECRET_KEY` (commit `36ee5d51`) that cannot be removed without rewriting published history |
| `.github/actions/setup-python-poetry/action.yml` | new — shared composite action for CI |
| `.github/security/pip-audit-ignores.txt`, `.trivyignore` | new — documented-exception files, currently suppress nothing |
| `app/api/v1/endpoints/health.py` | new `GET /api/v1/health/ready` (Postgres + Redis check, 503 on failure, no leak) |
| `app/core/config.py` | new `LOG_FILE_PATH` setting |
| `app/core/logger.py` | loguru file sink now conditional on `LOG_FILE_PATH` |
| `tests/api/test_readiness.py` | new — 7 tests for the readiness endpoint |
| `Makefile` | `prod-*`, `ci-local`, `docker-scan`, `lint-actions` targets; `docker-compose` → `docker compose` |
| `.github/workflows/codeql.yml` | new — CodeQL `security-and-quality` for Python, weekly cron + PR/push, report-only |
| `.github/dependabot.yml` | new — pip / github-actions / docker. This is what keeps the SHA and digest pins moving; pinning without it rots into confidently-unpatched |
| `sonar-project.properties` | new — committed but INERT; `projectKey`/`organization` are CHANGE_ME placeholders SonarCloud must assign |
| `.hadolint.yaml` | new — ignores exactly one rule (DL3008) with justification; `failure-threshold: info` |
| `.github/workflows/ci.yml` *(scanning wave)* | 6 jobs → 10, +4: `quality` (pylint, radon, hadolint), `trivy-repo` (`trivy fs` + `trivy config`), `dependency-review` (PR-only), `sonarcloud` (skips cleanly without `SONAR_TOKEN`). `security` gained bandit; `build` gained CycloneDX SBOM upload; SARIF categories split per scan mode |
| `pyproject.toml` | `[tool.pylint]` scoped config; ruff `select` gained `C90` with `max-complexity = 12`; bandit + radon added as dev deps |
| `Dockerfile` | `USER appuser` → `USER 1000:1000` (hadolint DL3066) |
| `app/main.py`, `app/db/models/enums.py`, `app/api/v1/endpoints/auth/login.py` | inline `# nosec` annotations with written reasons for three bandit false positives |
| `Makefile` | `make quality`, `make scan`, `make sbom` |
| `docs/deployment/{DEPLOYMENT_GUIDE,GITHUB_ACTIONS_SETUP,ROLLBACK}.md` | new — deployment guide, secrets/enablement setup, rollback runbook |

## Verification

- `black` / `isort` / `ruff` / `mypy` — all green ("Success: no issues found in 91 source files").
- Unit tests: **338 passed**, 98.29% coverage (baseline before this pass: 331 passed, 98%);
  coverage gate `--cov-fail-under=95`.
- `scripts/e2e_run.sh` — **25 passed**.
- `poetry check --lock` — exit 0.
- Image size: 396 MB → 376 MB.
- `actionlint` 1.7.12 + `shellcheck` 0.11.0 — exit 0 on `ci.yml`, `cd.yml`, `codeql.yml` and the
  composite action.
- `docker compose config` — clean for both dev and prod compose files.
- Full production stack brought up locally end to end: `db` healthy → `migrate` ran all 7
  revisions and exited 0 → `api` started healthy; `alembic_version = 0007_roadmap_applied_templates`;
  25 public tables present.
- Readiness verified both ways: `200` when healthy (revision `b0093a1`); after stopping Redis,
  liveness stayed `200` while readiness returned `503` naming `redis` as the failing dependency,
  with no DSN in the response.
- Resource limits confirmed via `docker inspect`: api `Memory=2GiB`/`NanoCpus=2.0`, db
  `3GiB`/`1.5`, redis `512MiB`/`0.5`.
- Read-only rootfs enforced — `touch /probe` inside the container → `Read-only file system`.
- Graceful shutdown — `compose stop api` took 1s, exit code 0 (not `SIGKILL 137`).
- Trivy 0.74.0 on the built image: 6 HIGH, 0 CRITICAL, zero OS-package findings.
- pylint: **9.94/10** with the scoped config (raw, unscoped: 7.48/10 across 527
  messages). Gate set at `--fail-under=9.5`.
- radon: 301 blocks, **average complexity A (2.30)**; **every module's
  maintainability index rated A**. Worst block `validate_answer` — D (23) by
  radon's counting.
- ruff `C901` (the actual complexity gate): worst function is `validate_answer`
  at **11**, everything else ≤ 9, so `max-complexity = 12` passes with one notch
  of headroom. Note ruff's McCabe and radon's count the same function differently
  (11 vs 23) — they are not in disagreement, they are different metrics.
- bandit: 3 findings → **0** after inline `# nosec` annotations; exits 0.
- hadolint: 2 findings → **0**; exits 0 at the strictest threshold (`info`).
- `trivy config`: **0 misconfigurations on the Dockerfile** — the hardening passes
  its own IaC scan.
- **`python-multipart` 0.0.20 → 0.0.32 shipped.** Lockfile diff shows exactly ONE
  version change (122 packages before and after) — `starlette`, `fastapi` and
  `anyio` were not dragged along. Scanner deltas, measured before and after:
  `trivy image` **6 → 3**, `pip-audit` **16 → 10**, `trivy fs` **7 → 4**.
  (pip-audit counts one advisory per fix-version, so 6 advisories cleared for
  3 CVEs.) Its live consumer is `POST /api/v1/onboarding/logo`
  (`UploadFile = File(...)`) — **not** login, which takes a Pydantic JSON body,
  not `OAuth2PasswordRequestForm`. Re-verified after the bump: 4 logo unit tests,
  all 11 auth e2e journeys, and `test_full_onboarding_journey` (a real multipart
  upload) all pass.
- **Checkov 3.3.15**: `.github` → **47 passed, 0 failed, 1 documented skip**. It
  caught `CKV_GHA_7` against `cd.yml`'s `workflow_dispatch` input on first run;
  suppressed inline with written reasoning because `build-and-push` is skipped
  whenever that input is set, so it provably cannot affect build output.
- `trivy fs` (pre-bump baseline): **7 HIGH, 0 CRITICAL** from `poetry.lock` — the 6 already known plus
  `ecdsa CVE-2024-23342` (Minerva attack, no fix), which the image scan hides
  behind `--ignore-unfixed`. Secret scanner: **0 secrets**.
- SBOM: CycloneDX 1.7, **174 components** (173 library + 1 operating-system).
- **All 18 GitHub Action SHA pins verified against the live GitHub API — zero
  mismatches**, and every action input used was validated against the pinned
  action's own `action.yml`.
- `make quality` exits 0. `make scan` runs six stages: bandit PASS, Semgrep PASS,
  gitleaks "no leaks found", `trivy config` PASS, `trivy fs` reports 7 HIGH and
  continues (report-only), `trivy image` 6 HIGH → exit 1 (blocking, as designed).
- Semgrep 1.157.0 with the exact CI ruleset (`p/python`, `p/security-audit`, `p/owasp-top-ten`,
  `p/jwt`, `p/secrets`, `--severity ERROR`) — **exit 0, no findings**.
- gitleaks v8.30.1 over all 158 commits — found the historical `SECRET_KEY` committed to
  `.env.example` in `36ee5d51` (2026-07-30). Replacing the value in the working tree does **not**
  remove it from history, so the finding is baselined by fingerprint in `.gitleaksignore` with a
  written rationale; re-ran and confirmed **exit 0, "no leaks found"**.
- Poetry resolution for the two new dev dependencies (`bandit ^1.9.4`, `radon ^6.0.1`) was
  clean: **8 installs, 0 updates to existing packages** — the scanning wave changed nothing
  about the production dependency set.
- Full re-verification **after** the app edits (the three `# nosec` annotations and the
  `USER 1000:1000` change): black / isort / ruff (incl. `C901`) / mypy clean, **338 tests
  passed at 98.29% coverage**, e2e **25 passed**, `poetry check --lock` exit 0. The image was
  rebuilt and re-checked: `id` inside the container reports `uid=1000(appuser) gid=1000(appuser)`,
  the app imports OK, and the image's `Config.User` is `"1000:1000"`.
- The dev-volume-deletion bug (finding 10) and the brace-expansion no-op (finding 11) were each
  reproduced before the fix and reverified clean after: prod `down -v` no longer touches dev's
  named volume, and the runtime image's Trivy report dropped the two phantom pip/setuptools
  findings once the cleanup used explicit paths.

### NOT VERIFIED — what this pass could not prove

Everything above was observed locally. These could not be, and are recorded so nobody reads
them as tested:

| Claim | Why it could not be verified | What would verify it |
|---|---|---|
| CodeQL analysis runs and produces findings | Cannot run locally; needs GitHub Actions | The first push to `main`/`develop`, then the Security tab |
| `dependency-review-action` blocks a bad dependency | Needs a real pull request to diff a manifest against a base commit | The first PR into `develop` |
| SonarCloud scan — **and its skip-cleanly path** | No SonarCloud account could be created here, so `SONAR_TOKEN` never existed. The skip is correct **by construction** (job-level `env` + a step `if:` gate), not by observation | Run CI once with no `SONAR_TOKEN` (expect a clean skip), then again after enablement |
| Dependabot opens PRs at all — and specifically **Docker digest** PRs | Never run. The digest lives in an `ARG PYTHON_IMAGE=…` default rather than a bare `FROM` literal, and Dependabot's Docker parser is not guaranteed to follow that | Enable it; if no digest PR appears within **two weeks**, refresh by hand and keep treating it as unverified |
| The SBOM as a **workflow artifact** | Generated locally from the built image only; never uploaded, and never attached to a GHCR image | The first `build` job — check for artifact `sbom-cyclonedx-<sha>` |
| SLSA provenance / SBOM attestations on the GHCR manifest | No GHCR push was performed | `docker buildx imagetools inspect --format '{{ json .Provenance }}'` after the first CD run |

## What the first real CI run taught us (2026-08-27, PR #18)

The docs above marked the workflows "NOT VERIFIED — never executed on GitHub". They have now
executed. Four green (lint, test, migrations, sonarcloud), five failed, build skipped. Four
distinct root causes, all fixed forward on the same branch.

### 1. The repo is private and has no GitHub Advanced Security — and the failure CASCADED

`dependency-review` reported *"Dependency review is not supported on this repository"*; every
`upload-sarif` step reported *"Resource not accessible by integration"*. On a **private** repo,
code scanning and the Dependency Graph are paid GHAS features.

The important part was not the upload failing — it was **what the failure did to the job**.
The upload steps sat *between* the scanners, so when one failed GitHub skipped every later
step:

| Job | Ran | **Silently skipped** |
|---|---|---|
| `security` | gitleaks, Semgrep | **bandit, pip-audit** |
| `iac-scan` | Trivy fs | **Trivy config (blocking), Checkov (blocking)** |

Four blocking gates never executed. The jobs were red so nothing shipped — but a bare
`continue-on-error` on the uploads would have turned them **green with no scanning at all**.
That is the dangerous version of this bug, and it is the one an unwary fix produces.

**Fix (structural, not cosmetic):** every scanner now runs *before* any upload; uploads are
last, `continue-on-error`, and guarded on `hashFiles()`. A failed upload can no longer suppress
a gate. A dedicated step then logs a visible warning and step-summary entry saying findings are
enforced by exit code but not uploaded — rather than a silent tolerance someone later mistakes
for flakiness. CodeQL and dependency-review now **skip cleanly** (a permanently-red job trains
people to ignore red), re-enabling automatically if the repo goes public or via an
`ENABLE_CODE_SCANNING=true` repository variable. Costs and both routes are documented in
`docs/deployment/GITHUB_ACTIONS_SETUP.md`.

### 2. `Path does not exist: trivy-config.sarif` was a symptom, not the cause

Reproduced locally: Trivy **does** write a valid SARIF even with zero findings (642 bytes). The
file was missing in CI only because the scan step had been *skipped* by cause 1, while its
upload carried `if: always()` and ran anyway. Both fixed — uploads are now guarded on the file
actually existing, so this failure mode cannot recur even if a scan is skipped for another
reason.

### 3. hadolint version skew — local passed, CI failed, same file and config

CI reported `DL3006 "Always tag the version of an image explicitly"` on both `FROM
${PYTHON_IMAGE}` lines. Local hadolint **2.15.1** exits 0 on the same input. Confirmed by
running both versions against the same Dockerfile and config:

| hadolint | Result |
|---|---|
| **v2.12.0** (shipped by `hadolint-action@v3.1.0`) | DL3006 ×2 → **exit 1** |
| **v2.15.1** (shipped by `hadolint-action@v3.5.0`, == local) | no output → **exit 0** |

2.12.0 does not resolve the `ARG PYTHON_IMAGE` default, so `FROM ${PYTHON_IMAGE}` reads as an
untagged image — against a base that is pinned by *digest*.

**Fixed by pinning the action to v3.5.0**, not by adding DL3006 to `.hadolint.yaml`.
Suppressing it would mask a genuinely untagged `FROM` added later. A gate that disagrees
between local and CI is worse than no gate, so the versions must track — and the pin now
carries a comment saying so.

### 4. A real race in `scripts/e2e_run.sh`, only visible on a cold runner

```
==> [sanity] recreate cofoundaz_e2e (clean state)
psql: error: connection to server on socket ".../.s.PGSQL.5432" failed: No such file or directory
```

~1.3s after the db container started. Two compounding defects, both genuine harness bugs rather
than CI quirks:

1. The `pg_isready` loop `break`s on success but **fell through silently** on timeout — a
   timeout was indistinguishable from success.
2. On a **first-ever** start Postgres runs `initdb`, which boots a *temporary* internal server.
   `pg_isready` can answer "accepting connections" against that, after which initdb stops it to
   start the real one. The wait passed; the next `psql` hit nothing.

It never reproduced locally because the dev volume already existed, so `initdb` never ran.

**Fix:** gate on the compose healthcheck via `docker compose up -d --wait`, then prove the real
server answers a real `SELECT 1` on the admin database — which is exactly what the next command
needs, and which initdb's temporary server cannot satisfy. The fallback loop now **fails loudly
with `docker compose ps` and logs** instead of falling through. `make e2e` is unchanged.

**Verified both ways.** Reproduced the cold start in an isolated compose project
(`COMPOSE_PROJECT_NAME=cfz-coldstart`) so `initdb` genuinely ran: **25 passed**. Forced the wait
to never succeed: **exit 1 with `!! Postgres never accepted a query ... after 30s`**, rather
than the previous silent fall-through.

> The shared dev Postgres volume was deliberately **not** wiped to reproduce this, despite that
> being the obvious route: it holds two concurrent worktree sessions' databases
> (`cofoundaz_mission_dev` has 28 tables). An isolated compose project reproduces the cold-start
> condition exactly and destroys nothing shared.

## Dependency upgrade: fastapi + starlette (clearing 9 advisories)

Adebayo chose "fix the dependencies, then merge". Done, but not the way it looked
from the outside - the obvious upgrade silently disables rate limiting.

### Result

| Scanner | Before | After |
|---|---|---|
| `pip-audit` (locked prod deps) | **10** | **1** |
| `trivy image` (HIGH/CRITICAL, the CI gate) | **3** | **0** |
| `trivy fs` (lockfile, incl. unfixed) | **4** | **1** |

All 9 `starlette` advisories cleared. The 1 remaining is `ecdsa`
`PYSEC-2026-1325` / `CVE-2024-23342`, which has no fix and is left deliberately.

Landed: `fastapi 0.115.14 -> 0.136.3`, `starlette 0.46.2 -> 1.6.0`, plus one new
transitive (`annotated-doc`). Nothing else moved.

### The upgrade window is bounded at BOTH ends, and the top bound is the interesting one

```
fastapi = ">=0.133.0,<0.137.0"
starlette = ">=1.3.1,<2.0.0"
```

**Lower bound.** 0.133.0 is the first fastapi release to drop the starlette upper
bound. 0.128.3 still capped at `<1.0.0`, so nothing below 0.133.0 can reach a
fixed starlette at all. Verified against PyPI metadata for every fastapi release
from 0.115.0 up.

**Upper bound - a real breakage, found by testing rather than by reading.**
fastapi **0.137.0** introduced `_IncludedRouter`, which wraps routes registered
via `include_router()` in a container object instead of flattening them into
`app.routes`. slowapi's `SlowAPIMiddleware` resolves the endpoint for the current
request by iterating `app.routes` and matching entries that expose `.endpoint`.
A `_IncludedRouter` exposes neither, so from 0.137.0 **every route mounted
through a router becomes invisible to the limiter and silently stops being rate
limited.**

For this app that is the entire `/api/v1` surface - auth, onboarding, assessments,
roadmap, everything. Measured at 135 requests against a 120/minute limit:

| Configuration | `/health` (`@app.get`) | `/api/v1/health` (`include_router`) |
|---|---|---|
| fastapi 0.115.14 + starlette 0.46.2 (before) | first 429 at **#121** | first 429 at **#121** |
| fastapi 0.141.1 + starlette 1.6.0 (naive upgrade) | first 429 at **#121** | **never - 0 x 429** |
| fastapi 0.136.3 + starlette 1.6.0 (**shipped**) | first 429 at **#121** | first 429 at **#121** |

Note the middle row: `/health` keeps working because it is declared directly with
`@app.get`. That is precisely why the whole suite stayed green - **405 unit tests,
27 e2e, all passing, with the API's rate limiting entirely disabled.** The
existing rate-limit tests only exercised routes declared on the app.

Confirmed under gunicorn too, not just pytest: 700 concurrent requests against
the production stack produced 480 x 200 + 220 x 429 - exactly 4 workers x 120.

### What makes the pin self-enforcing

`tests/api/test_rate_limit.py::test_default_limits_reach_routes_registered_via_include_router`
mounts a route through `include_router` and asserts the default limit trips.
Verified to **fail on fastapi 0.141.1** with a diagnostic naming the pin, and pass
on 0.136.3. Raising the ceiling without fixing the interaction now breaks a test
instead of silently removing rate limiting.

### The starlette 1.0 major itself was a non-event

Every removal in the 1.0 release notes was checked against this codebase and
slowapi, and none applies: `on_startup`/`on_event`/`add_event_handler` (no event
hooks here), `@app.route`/`@app.websocket_route`/`@app.middleware` decorators (not
used), `iscoroutinefunction_or_partial` (not used by us or slowapi),
`Jinja2Templates` (not used). `@app.exception_handler` **is** used, but FastAPI
defines its own in `fastapi.applications` rather than inheriting Starlette's.
`BaseHTTPMiddleware` and `RequestResponseEndpoint` - which both `LoggingMiddleware`
and slowapi depend on - are not in the removal list and still exist.

slowapi 0.1.10 declares no starlette constraint at all (only `limits>=2.3`), so it
neither blocks resolution nor protects against this. That is exactly why the
breakage had to be found by measurement.

### Largest safe upgrade, if someone wants to go further

**fastapi 0.136.3 is the ceiling without code changes.** Going to 0.137.0+ needs
one of: a patched `_find_route_handler` that descends into `_IncludedRouter`, a
slowapi release that supports it, or replacing slowapi with limiter middleware
that resolves the endpoint from `scope["route"]` rather than by scanning
`app.routes`. None of that was in scope here.

### Pre-existing bug found while verifying (NOT caused by the upgrade)

The 429 body is slowapi's default `{"error":"Rate limit exceeded: 120 per 1 minute"}`
with **no `Retry-After` header** - not the app's documented envelope
(`error.code == "RATE_LIMITED"`). Confirmed **identical on the old versions**, so
it predates this work: `SlowAPIMiddleware` looks up `app.exception_handlers`
directly and does not find FastAPI's registered handler for default-limit
violations. Any FE integration guide promising `RATE_LIMITED` is wrong today.
Tracked as a follow-up; deliberately not fixed here.

### Sizing the `python-jose` -> PyJWT migration (for the unfixable `ecdsa`)

`ecdsa` arrives solely via `python-jose`, and has no fix. The migration surface is
small and worth knowing precisely:

| File | Usage |
|---|---|
| `app/core/security.py` | 1 x `jwt.encode` |
| `app/api/deps.py` | 1 x `jwt.decode`, catches `JWTError` |
| `app/main.py` | 1 x `jwt.decode` (the rate-limit key function), catches `JWTError` |

**3 files, 1 encode, 2 decodes, 2 exception catches.** PyJWT's `encode`/`decode`
signatures match jose's, so the mechanical change is an import swap plus
`JWTError` -> `PyJWTError`. Two things to watch: PyJWT >= 2.10 **requires `sub` to
be a string** (tokens here carry a UUID, so it must be `str()`-cast at issue time),
and PyJWT validates `exp`/`nbf` by default. Estimate: a small change, but it
touches authentication, so it needs the full auth e2e suite behind it rather than
being bundled into an unrelated PR.

## Operate / roll back

- **Nothing is committed.** This is a working-tree pass; review with `git status` / `git diff`
  before staging or committing anything.
- Local dry run: `docker compose -f docker-compose.prod.yml --env-file .env.production config`
  then `up -d`; expect `db` → `migrate` (exits 0) → `api healthy`, in that order.
- Roll back locally: `docker compose -f docker-compose.prod.yml down` (no `-v` — the prod stack
  now has its own project name, so this cannot touch the dev stack's volumes either way).
- **Nothing here has touched a real VPS or GitHub Actions run** — see Follow-ups.

## Follow-ups

- **CI will be red on first run.** Trivy: 6 HIGH in `python-multipart` 0.0.20 and `starlette`
  0.46.2. `pip-audit`: 16 advisories across `starlette`, `python-multipart`, and `ecdsa` 0.19.2
  (`PYSEC-2026-1325`, no fix available, arrives transitively via `python-jose`). Nothing is
  suppressed in `.trivyignore` / `pip-audit-ignores.txt` on purpose — this needs triage, not a
  silent exemption. A resolution probe showed `python-multipart` bumps cleanly to 0.0.32 (fixes 3
  of the 6 Trivy findings); `starlette` stayed at 0.46.2 even with `fastapi` 0.141.1 and needs
  separate investigation; `ecdsa` really needs migrating `python-jose` → `PyJWT`.
- **The historical `SECRET_KEY` — RESOLVED (2026-08-27): rotate, do not purge.** The value
  committed to `.env.example` is public in the repo's history and is treated as compromised.
  Adebayo's decision is that production generates its own key on the server
  (`.env.production.example`), so the committed value never signs anything in a deployed
  environment — which makes the copy left in history a dead string. Clearing it would need
  `git-filter-repo`, a force-push rewriting published history, and a re-clone by every
  collaborator: real cost, no security benefit once the key is dead. It stays baselined in
  `.gitleaksignore` (with the decision recorded there), so the gate still fails on any *new*
  leak. This is not a standing exemption: if an environment is ever found to have run with
  this key, rotating that environment's `SECRET_KEY` remains mandatory and logs out every user.
- **No NEW gate is red.** pylint, hadolint, bandit and `trivy config` are all green
  on the current tree. The only red gate remains the pre-existing dependency CVE
  debt described above.
- Refactor `validate_answer` (`app/services/assessment/engine.py`) so ruff's
  `max-complexity` can drop from 12 to the common default of 10. It is the single
  function standing between the tree and that threshold.
- **Neither `trivy config` NOR Checkov covers docker-compose.** Trivy's targets are
  Dockerfile/K8s/Terraform/CloudFormation/Helm; Checkov 3.3.15 has no
  `docker_compose` framework at all (verified — zero results under its generic
  `yaml` framework). Both compose files therefore remain unscanned by any IaC
  tool, covered only by review and `docker compose config`. Closing this needs a
  compose-specific linter, not another general-purpose IaC scanner.
- Remaining CVE debt after the `python-multipart` bump: **3 HIGH** (`starlette`
  0.46.2) blocking `trivy image`, plus `ecdsa` `CVE-2024-23342` with no fix.
  starlette stays at 0.46.2 even with fastapi 0.141.1, so it needs real
  compatibility work; `ecdsa` needs the `python-jose` → PyJWT migration.
- SonarCloud is committed but inert until someone creates the project and adds
  `SONAR_TOKEN` — see `docs/deployment/GITHUB_ACTIONS_SETUP.md`.
- CodeQL is report-only. Triage the first baseline, then decide whether to promote
  it to blocking via branch protection's code-scanning requirement.
- Confirm Dependabot actually opens Docker digest PRs: the digest lives in an
  `ARG PYTHON_IMAGE=...` default rather than a bare `FROM` literal, and its Docker
  parser is not guaranteed to follow that. If no PR appears within two weeks of
  enabling, refresh the digest by hand and treat this as unverified.
- Image signing was **deliberately not added**. CD already emits SLSA provenance
  and SBOM attestations via BuildKit; cosign was declined because CD deploys the
  digest of the build it just ran and a digest is content-addressed, so the
  registry cannot serve different bytes under it. Without an admission controller
  or a `cosign verify` gate that can refuse a deploy, a signature would be
  ceremony. Revisit if a third party ever consumes these images.
- `deploy.resources.reservations.cpus` is declarative-only outside Swarm — verified `CpuShares`
  / `CpuQuota` / `CpuPeriod` are all `0` on Compose v2.40.3. Memory reservations do apply.
- `uvicorn.workers.UvicornWorker` is deprecated upstream in favour of the `uvicorn-worker`
  package; works today on uvicorn 0.32.1.
- **Nothing here was verified against a real VPS**: no SSH deploy, no nginx, no UFW, no GHCR
  push/pull, and the workflows have never executed on GitHub. Backup/restore procedures are
  documented (`docs/deployment/`) but unexecuted.
- Local venv runs Python 3.14 while the project targets 3.11 (CI uses 3.11) — a pre-existing
  discrepancy, not introduced here.

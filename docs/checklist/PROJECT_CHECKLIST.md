# Cofoundaz API — Master Build Checklist

> **Purpose:** the single, always-current bird's-eye map of the whole backend build — what's
> shipped, what's in progress, what's ahead. Kept as task lists so progress is visible at a
> glance. Complements per-change **specs** (`docs/superpowers/specs/`), **plans**
> (`docs/superpowers/plans/`), and **SOPs** (`docs/sop/`); this file is the index that spans
> them all and never gets archived.
>
> **Source of truth for scope:** `../Cofoundaz_Technical_PRD.md` + the UI handoff in `../cofoundaz/`.
> **Convention:** update on new scope (map it in *before* building), on planning (mirror task
> breakdown), and on shipping (check off + note PR/commit). An item is checked **only when done
> and verified**.

_Last reconciled: 2026-08-29 · `feat/nginx-edge-tls` (nginx edge + two-stack compose; branched off `main` @ `f75d62f`)_

---

## Snapshot

**PRD module tally: 26 total** — 5 fully complete & merged (01 Auth+Onboarding · 04 Today's Mission · 05 Roadmap · 06 Health Score · 07 Assessment) · 21 not started (02·03·08–26).

| State | Count | Modules |
|---|---|---|
| ✅ Shipped & certified (merged) | 5 modules (+spine) | Foundation/Tenancy spine · Auth (01) · Onboarding (01.6) · Assessment (07) · Health Score (06) · Roadmap (05, all 3 slices) · Today's Mission (04) |
| 🟡 In progress | 0 | — |
| ⬜ Planned / next | 21 | Dashboard (02) · AI Co-Founder (03) · Business Builder (08) · 09–26 |

**Health at a glance:** ~63 endpoints · 398 unit tests (real Postgres) + 26 live E2E · ~98% coverage · black/isort/ruff/mypy clean · zero AI-attribution trailers.

**Health at a glance:** ~60 endpoints · 353 unit tests (real Postgres) + 26 live E2E · 98.39% coverage · black/isort/ruff (incl. C901)/mypy clean · pylint 9.94/10 · radon average complexity **A (2.30)**, every module MI **A** · bandit / hadolint / `trivy config` / checkov all exit 0 · zero AI-attribution trailers.

---

## ✅ Foundation & Tenancy Spine — *shipped (PR #1)*

- [x] Error framework + exception handlers + standard response envelope
- [x] Typed SQLAlchemy `Base` + model mixins (UUID, timestamps)
- [x] Real Postgres test harness (per-test transaction rollback) + coverage gate
- [x] Tenancy spine models — `users`, `startups`, `startup_profiles`, `memberships`
- [x] RBAC + tenancy resolution — `require_role`, `require_workspace`, `X-Workspace-Id`
- [x] Real `get_current_user` dependency
- [x] Jobs table + `JobDispatcher` seam + `GET /jobs/{id}` endpoint
- [x] Rate limiting
- [x] Audit log + `write_audit` helper
- [x] Initial Alembic migration `0001_initial_schema`
- [x] Platform seams: `event_bus`, `job_dispatcher`, `EmailSender`, `Storage`, `AIPanel`

## ✅ Module 01 — Auth — *shipped (PR #2)*

- [x] Signup / verify email / resend verification
- [x] Login with lockout + MFA gate
- [x] Refresh-token session service (rotation + reuse-detection → family revocation)
- [x] Refresh / logout endpoints (dual-transport JWT: httpOnly cookie + Bearer)
- [x] Forgot / reset password
- [x] `GET /auth/me` identity endpoint
- [x] MFA service + TOTP setup/challenge endpoints
- [x] OAuth (Google/Apple) + SMS MFA — `501 FEATURE_NOT_ENABLED` seams
- [x] Auth tables migration `0002_auth_tables`
- [x] Live E2E auth verification
- [ ] _Deferred:_ OAuth real providers · SMS MFA real delivery (seams in place)

## ✅ Module 01.6 — Onboarding — *shipped (PR #3)*

- [x] `InvitationStatus` enum + `invitations` model + `assessment_pending` column
- [x] Migration `0003_onboarding`
- [x] Email-verified dependency + draft-workspace resolver (lazy-create)
- [x] `GET /onboarding/state`
- [x] `PATCH /onboarding/state` (per-step autosave + validation, steps 1–4)
- [x] `POST /onboarding/logo` (Storage seam, ≤2 MB, type-checked)
- [x] `POST /onboarding/invites` (founder-only, dedupe, invite email)
- [x] `POST /onboarding/complete` (gate + `roadmap.generate`/`healthscore.initialize` jobs + idempotent)
- [x] `GET /invitations/{token}` (public preview)
- [x] `POST /invitations/accept` (email-bound)
- [x] Live E2E onboarding journey + SOP
- [ ] _Deferred:_ onboarding AI panel (Module 03) · real notification delivery (Module 20)

## ✅ Module 07 — Startup Assessment — *shipped (PR #4)*

- [x] Question bank v1 (versioned, 11 questions × 5 dimensions)
- [x] Adaptive engine — `show_if` skip-logic grammar, server-driven next-question, per-type validation
- [x] Deterministic per-dimension 0–100 scoring (clamp + neutral-50) + templated narrative
- [x] Data model — `assessments` / `assessment_answers` / `assessment_results` + migration `0004`
- [x] `POST /assessments` (start/resume, savepoint+IntegrityError→resume)
- [x] `GET /assessments/{id}/next-question`
- [x] `POST /assessments/{id}/answers` (autosave, `ON CONFLICT` upsert)
- [x] `POST /assessments/{id}/complete` (atomic claim → score → side-effects)
- [x] `GET /assessments` · `GET /assessments/{id}` · `GET /assessments/compare`
- [x] Race-safety on all 3 write paths + concurrency tests
- [x] Live E2E adaptive journey + SOP
- [ ] _Deferred:_ AI-generated narrative (Modules 03/06) · quarterly re-assessment cron (Module 20)

## ✅ E2E Full-Coverage Pass — *shipped (PR #5)*

- [x] Close E2E gaps: logo upload · invite preview · assessment compare
- [x] Smoke openapi assertion covers all 34 endpoints

---

## ✅ Module 06 — Health Score — *shipped (PR #6, merged `0f8523c`)*

_Explainable 0–100 score · 5 dimension sub-scores · trend history · benchmarks · ranked recommendations._

**Design (brainstorming):**
- [x] Resolve computation/recompute model → **A: inline recompute + lazy-on-read, no worker**
- [x] Reconcile dimension naming → **keep internal keys `product/market/money/legal/team` (reuse `Dimension` enum); "Financial" is `money`'s display label only**
- [x] Signal-sourcing scope → **C: build `health_signals` now (assessment-derived), weights as static versioned config, defer `health_dimensions` table**
- [x] Pre-assessment state → **A: honest pending empty-state (no row until first assessment; `healthscore.initialize` still enqueued at onboarding-complete but stays unconsumed by design)**
- [x] Recommendations engine scope → **A: rule-based + persisted (`health_recommendations`, dedupe, never resurrect dismissed)**
- [x] Benchmarks scope → **A: honest empty-state with cohort-size gate (no aggregation pipeline built now)**
- [x] Band thresholds + weekly-delta / drop / record semantics — implemented in `app/services/health_score/config.py` + `scoring.py`
- [x] `healthscore.recalculate` stub replaced by the inline recompute at assessment-complete; `healthscore.initialize` (onboarding) intentionally left as an unconsumed stub — see `docs/sop/2026-08-19-health-score.md`
- [x] Spec written → self-review → user review gate → writing-plans (`.superpowers/sdd/2026-08-19-health-score/`)
- [x] Endpoints: `GET /health-score` · `/dimensions/{dim}` · `/history?range` · `/benchmarks` · `/recommendations` · `POST /recommendations/{id}/{accept,dismiss}`
- [x] Data model: `health_scores` · `health_score_history` · `health_signals` + `health_recommendations` + migration `0005`
- [x] Events: `healthscore.updated` · `healthscore.dropped` · `healthscore.record`
- [x] Live E2E + SOP + **FE integration guide (captured live)** — `e2e/test_health_score.py`, `docs/sop/2026-08-19-health-score.md`, `docs/fe-integration-guide-health-score.md`
- [ ] _Deferred:_ real benchmark cohort aggregation · async worker for the unconsumed stub jobs (Module 05) · AI-generated summary/recommendations (Module 03) — see SOP Follow-ups

---

## ✅ Module 05 — Roadmap — *complete, all 3 slices merged to `main` (Slice 3 via PR #16)*

_Decomposed in brainstorming: each slice = its own spec → plan → build → PR._

**Slice 1 — Core** — *✅ merged (PR #7, `5fef521`)*
- [x] Scope split + locked decisions (stage-only catalog · role-based access · generate honors job contract inline · derived progress/explicit status · inline+lazy)
- [x] Enums (`RoadmapStatus`, `TaskEffort`) + 5 models (`roadmaps`/`roadmap_phases`/`roadmap_milestones`/`roadmap_tasks`/`roadmap_task_dependencies`) + factories
- [x] Migration `0006_roadmap`
- [x] `require_roles(founder, team_member)` editor dep + tenancy resolvers
- [x] Template catalog + `generate_roadmap` (create-once `ON CONFLICT` + race test) + `roadmap.generated`
- [x] `recompute_milestone_progress` + `GET /roadmap` (tree serialize + lazy generate)
- [x] `POST /roadmap/generate` (202 job) + wire inline into `complete_onboarding` (retire stub)
- [x] Phases CRUD · Milestones CRUD (+ mark-complete transition event) · Tasks CRUD (+ progress recompute)
- [x] Live E2E + SOP + FE integration guide (captured live) — `e2e/test_roadmap.py`, `docs/sop/2026-08-21-roadmap-core.md`, `docs/fe-integration-guide-roadmap.md`
- [ ] _Deferred:_ `roadmap.milestone.overdue` event + notifications (Module 20, needs scheduler) · workspace-tz base date

**Slice 2 — Dependencies + Templates** — *✅ done, merged to `main`*
- [x] Scope + locked decisions (separate gallery catalog · apply = append + dedup by template id · write-time DFS cycle detection · duplicate-edge idempotent 200 · dedicated graph endpoint)
- [x] `GALLERY_TEMPLATES` static catalog (named, industry-tagged packs) + counts helper
- [x] Migration `0007_roadmap_applied_templates` (`applied_template_keys` JSONB on `roadmaps`)
- [x] `dependencies.py` — `would_create_cycle` (DFS reachability) + `add_dependency`
- [x] `POST`/`DELETE /roadmap/tasks/{id}/dependencies` (cycle → 409 `DEPENDENCY_CYCLE`, dup → idempotent 200)
- [x] `GET /roadmap/dependencies` graph + populate tree `depends_on`/`dependency_count`
- [x] `GET /roadmap/templates` gallery + `GET /roadmap/templates/{id}` preview
- [x] `POST /roadmap/templates/{id}/apply` (append + dedup + `roadmap.template.applied`)
- [x] Live E2E extension + SOP + FE integration guide update — `e2e/test_roadmap.py` (extended), `docs/sop/2026-08-22-roadmap-deps-templates.md`, `docs/fe-integration-guide-roadmap.md` (updated)
- [ ] _Deferred:_ `add_dependency`/`apply_template` no-row-lock race under concurrent double-calls (benign, same class as Slice 1's `_next_order`) · `roadmap.template.applied` has no consumer until Module 20

**Slice 3 — AI Re-plan** — *✅ done, merged to `main` (PR #16, Tasks 1–7)* — 2026-08-27
- [x] Scope + locked decisions (slip-and-cascade along dependency DAG · stateless preview + deterministic apply · `roadmap_replans` history table + milestone marker columns · templated reason v1 · `roadmap.replan` job stays an unconsumed stub)
- [x] Migration `0008_roadmap_replan` (`roadmap_replans` table + `last_replanned_at`/`last_replan_reason` on `roadmap_milestones`) + `RoadmapReplan` model
- [x] `detect_drift` + `compute_replan` cascade engine (`REPLAN_BUFFER_DAYS = 7`, milestone-precedence DAG, max-not-sum shift propagation, templated reason)
- [x] `apply_replan` (recompute-on-apply, stale `change_id` → skipped, markers + one history row + `roadmap.replanned` event)
- [x] `POST /roadmap/replan/preview` (member) + `POST /roadmap/replan/apply {change_ids[]}` (editor)
- [x] `GET /roadmap/replan/history` + tree `roadmap.drift.slipped_count` + per-milestone `replanned` marker
- [x] Live E2E (26 passed) + smoke surface — `e2e/test_roadmap_replan.py`
- [x] SOP + FE integration guide update + checklist reconcile — `docs/sop/2026-08-26-roadmap-replan.md`, `docs/fe-integration-guide-roadmap.md` (updated)
- [ ] _Deferred:_ AI-authored rationale (Module 03) · notification on `roadmap.replanned` (Module 20) · `roadmap.replan` job permanently unconsumed (by design) · phase/task dates not shifted in v1 · `_milestone_precedence` unscoped query · `change_ids` not deduped on apply — see SOP Follow-ups

---

## ✅ Module 04 — Today's Mission — *shipped, merged to `main` (PR #17)*

_A daily 1–3 task mission generated lazily-on-read from the founder's roadmap · complete/snooze/reorder/reject · custom tasks · derived streak · history + weekly % · settings. Read-only against the roadmap; migration `0009`._

**Design (brainstorming) — locked decisions:**
- [x] Generation → **inline + lazy-on-read** (`GET /missions/today` generates today's mission if none exists; no cron/worker — 06:00 cron + push deferred to Module 20)
- [x] Roadmap link → **soft, unconstrained** `mission_tasks.roadmap_task_id` (nullable UUID, **no FK**) — mission is a snapshot, decoupled from roadmap tables
- [x] Streak → **derived, not stored** (consecutive completed days ending today/yesterday)
- [x] Reason line → **templated** v1 (`"From your '{milestone}' milestone."`); AI-authored rationale deferred to Module 03
- [x] Spec → self-review → plan (7 TDD tasks) — `docs/superpowers/specs/2026-08-26-todays-mission-design.md`, `docs/superpowers/plans/2026-08-26-todays-mission.md`

**Build (subagent-driven, Tasks 1–7):**
- [x] Enums (`MissionStatus`, `MissionTaskStatus`) + 3 models (`missions`/`mission_tasks`/`mission_settings`) + factories
- [x] Migration `0009_mission` (sibling of `0008_roadmap_replan` off `0007`; merge revision reconciles later)
- [x] Generation service — `get_or_generate_today` (read-only roadmap selection + carry-forward snoozed + templated reasons) + derived `streak`
- [x] `GET /missions/today` (lazy-gen + `no_roadmap` empty-state + weekends-off empty mission + streak)
- [x] `GET`/`PATCH /missions/settings` (defaults lazily created · `mission_size` clamp 1–3 → 422)
- [x] `POST /missions/tasks` (custom task, appended) + `PATCH /missions/tasks/{id}` (complete/snooze/reorder/reject)
- [x] Events: `mission.task.completed` · `mission.completed` · `mission.streak.milestone` (7/30/100)
- [x] `GET /missions/history` (per-day completed/total + rolling weekly completion %)
- [x] Access: reads = any member (mentor incl.) · writes = founder/team_member (mentor → 403) · cross-workspace → uniform 404
- [x] Live E2E (`e2e/test_mission.py`, 6 captures) + smoke openapi surface (5 mission paths)
- [x] SOP + FE integration guide (captured live) + this checklist reconcile — `docs/sop/2026-08-26-todays-mission.md`, `docs/fe-integration-guide-mission.md`
- [ ] _Deferred:_ 06:00 cron generation + push notification (Module 20) · AI-authored reason line (Module 03) · real `mission.*` event delivery (Module 20) · workspace-timezone base date

## ✅ Deployment & Infrastructure — *on `chore/production-deployment-hardening` (PR #18, open)*

_Production docker/compose hardening, CI/CD pipeline rework, and a real readiness endpoint —
verified locally, and the CI workflows have now had a first real run on GitHub
(which found four defects, since fixed). Nothing has touched a real VPS. See
`docs/sop/2026-08-27-production-deployment-hardening.md`._

- [x] `docker-compose.prod.yml` — no host-published Postgres/Redis, `migrate` one-shot gated by
      `service_completed_successfully`, resource limits, json-file log rotation, own compose
      project name so `down -v` can't touch the dev stack's volumes. **Superseded 2026-08-29:**
      the project name is now `${COMPOSE_PROJECT_NAME:-cofoundaz-api-prod}` and the fixed
      `container_name:` keys are gone, so one file serves both stacks — see the nginx section
- [x] `docker-compose.yml` made honestly dev-only
- [x] Production `Dockerfile` rewritten — Poetry 2.2.1 (was 1.8.4, couldn't read the
      lock-version 2.1 lockfile), tini + gunicorn/uvicorn workers, `libpq-dev`/pip/setuptools/wheel
      removed from the runtime image (396 MB → 376 MB)
- [x] `poetry.lock` un-gitignored and git-staged for reproducible builds
- [x] `.dockerignore` inverted to deny-by-default; `.env.production.example` added; real-looking
      `SECRET_KEY` in `.env.example` replaced with a placeholder
- [x] CI (`ci.yml`): 1 job → **10**. First pass took it to 6 — `lint` (now incl. mypy), `test` on Postgres 17 with a 95%
      coverage gate, `migrations` (single-head + fresh upgrade + `alembic check` + downgrade
      round-trip), `e2e`, `security` (gitleaks + Semgrep + pip-audit), `build` (image + smoke +
      Trivy); plus a shared `setup-python-poetry` composite action, SHA-pinned actions,
      least-privilege `permissions:` and concurrency groups. The scanning wave added 4 more —
      `quality`, `trivy-repo`, `dependency-review`, `sonarcloud` — leaving 8 jobs fully parallel,
      `sonarcloud` on `needs: [test]`, and `build` on
      `needs: [lint, quality, test, migrations, e2e, security, trivy-repo]`
- [x] `GET /api/v1/health/ready` — checks Postgres + Redis, `503` naming the failed dependency,
      no DSN leak (7 tests); `/health` kept as a cheap liveness check
- [x] Security-exception scaffolding — `.trivyignore` / `.github/security/pip-audit-ignores.txt`
      created, currently suppress nothing
- [x] Semgrep (CI ruleset, severity ERROR) verified clean locally — exit 0, no findings
- [ ] **Code scanning** — `codeql.yml` authored, `actionlint` clean, **NEVER EXECUTED**
      (CodeQL cannot run locally). Own workflow: needs a weekly cron, which in
      `ci.yml` would run the whole pipeline weekly). Python `security-and-quality`
      suite, report-only. Complements Semgrep: CodeQL does interprocedural
      dataflow/taint tracking, Semgrep is single-window pattern matching
- [x] **Quality scanning** — `quality` job: pylint wired up at last (it was a
      declared dev dep CI never ran) with a scoped `[tool.pylint]` config,
      **9.94/10** measured, gate `--fail-under=9.5`; radon complexity/MI report
      (301 blocks, average **A (2.30)**, every module MI **A**); hadolint on the
      Dockerfile at the strictest threshold
- [x] Complexity gate via ruff `C901` (`max-complexity = 12`; measured worst is
      `validate_answer` at 11) — one enforcer, radon reports only
- [x] **bandit** Python SAST in the `security` job — 3 findings, all false
      positives, fixed with inline `# nosec` + written reasons; now exits 0
- [x] **hadolint** — DL3066 FIXED (`USER 1000:1000`, not ignored); only DL3008
      ignored, with justification in `.hadolint.yaml`. Exits 0
- [x] **Trivy beyond the image** — `trivy fs` (report-only; finds the locked graph
      incl. unfixable `ecdsa CVE-2024-23342`, plus 0 secrets) and `trivy config`
      (BLOCKS; **0 misconfigurations** on the hardened Dockerfile)
- [x] **SBOM** — CycloneDX 1.7 from the built image, **174 components** (173 library +
      1 operating-system), ~301 KB — generated and counted locally
- [ ] SBOM as a *workflow artifact* (`sbom-cyclonedx-<sha>`, 90-day retention) —
      **NOT VERIFIED**, never uploaded; likewise the SLSA provenance + SBOM
      attestations CD attaches to the GHCR manifest, since no GHCR push has happened.
      cosign signing was deliberately declined — CD deploys the digest it just built,
      and without a `cosign verify` gate that can refuse a deploy a signature is ceremony
- [ ] **`dependency-review`** job on pull requests — authored (blocks a PR that
      *introduces* a high-severity or copyleft-licensed dependency); **NEVER EXECUTED**,
      it needs a real PR to diff a manifest against a base commit
- [ ] **`.github/dependabot.yml`** — pip / github-actions / docker, authored; what keeps
      the SHA and digest pins moving rather than rotting (pinning *without* an update
      mechanism is strictly worse than not pinning). **NEVER RUN** — needs enabling, and
      the Docker digest PRs specifically need confirming (see follow-ups)
- [x] Distinct SARIF `category:` per scan mode (semgrep / codeql / trivy-fs /
      trivy-config / trivy-image) so Security-tab results don't overwrite
- [x] All **18** Action SHA pins verified against the live GitHub API — zero
      mismatches; every action input validated against its pinned `action.yml`
- [x] `make quality` / `make scan` / `make sbom` reproduce the CI scanning locally —
      `make quality` exit 0; `make scan` runs 6 stages (bandit PASS, Semgrep PASS,
      gitleaks "no leaks found", `trivy config` PASS, `trivy fs` 7 HIGH and continues
      as report-only, `trivy image` 6 HIGH → exit 1, blocking as designed)
- [x] `bandit ^1.9.4` + `radon ^6.0.1` added as dev deps — resolution clean, **8 installs,
      0 updates** to existing packages; `pylint` was already declared but CI never ran it
- [x] **`python-multipart` 0.0.20 → 0.0.32** (approved) — only that package moved
      (122 packages before and after). `trivy image` **6 → 3**, `pip-audit`
      **16 → 10**, `trivy fs` **7 → 4**. Live consumer is the `/onboarding/logo`
      multipart upload (not login, which is a JSON body); re-verified via 4 logo
      unit tests, 11 auth e2e journeys and the onboarding upload journey
- [x] **Checkov** wired for `github_actions` — **47 passed / 0 failed / 1
      documented skip**; caught `CKV_GHA_7` on `cd.yml` first run. NOT used for
      compose: Checkov 3.3.15 has no `docker_compose` framework (verified — zero
      results), so a compose job there could never fail
- [x] **VPS target spec CONFIRMED at 4 vCPU / 8 GB / 80 GB SSD** — restated in
      `docker-compose.prod.yml` and `docs/deployment/` as a confirmed spec rather
      than an assumption. Nominal CPU sums to 4.5 but `api` and `migrate` are
      mutually exclusive (`service_completed_successfully`), so real peaks are
      2.5 (migrating) and **exactly 4.0** (steady state); memory 5632M of 8192M
      leaves 2560M for host + page cache. Connections 42/100 (42%)
- [x] **Staging → production gated pipeline** (2026-08-28) — CD restructured to
      `build-and-push` → `staging-deploy` → `staging-e2e` → `production-deploy`;
      production requires an explicitly-green staging gate and deploys the SAME
      digest staging proved, not a rebuild. Both deploys share one composite
      action (`.github/actions/deploy-stack`) so the logic cannot drift
- [x] **Encrypted environment files** — `scripts/env.sh` (6 subcommands, 3-tier key
      discovery, AES-256-CBC + PBKDF2 100k), `make env-*` wrappers, `.gitignore`
      re-allows `*.enc` while still denying `.env` / `.env.staging` /
      `.env.production` / `.env.key` (proved with `git add --dry-run`)
- [x] **Two corrections to earlier assumptions** — `DEPLOY_PATH` is now a
      per-GitHub-Environment secret so no server path lives in the repo (the
      documented `/opt/cofoundaz-api` never existed); and the deployed env file
      is `.env`, not `.env.production`, throughout compose/Makefile/docs
- [x] **Live staging E2E gate** — `E2E_REMOTE=1` auto-deselects mailbox-dependent
      tests by fixture closure, prints every deselection, and errors if that would
      leave zero tests. Verified `13 passed, 14 deselected` against a live server
- [x] `docs/deployment/ENV_ENCRYPTION.md` + DEPLOYMENT_GUIDE / GITHUB_ACTIONS_SETUP /
      ROLLBACK updated; SOP `docs/sop/2026-08-28-staging-pipeline-env-encryption.md`
- [ ] **SonarCloud** — `sonar-project.properties` committed but INERT; the job
      skips cleanly until a `SONAR_TOKEN` secret exists. Needs a SonarCloud
      account (could not be created here)
- [x] `.gitleaksignore` baselines the one historical `SECRET_KEY` committed to `.env.example` in
      `36ee5d51`; gitleaks over all 158 commits then passes clean
- [x] Full production stack verified locally end-to-end — migration gate, resource limits
      (`docker inspect`), read-only rootfs, graceful shutdown (1s, exit 0), readiness
      `200`→`503` on Redis outage
- [ ] `cd.yml` (build/push GHCR + SSH deploy + automatic rollback) — authored, `actionlint` /
      `shellcheck` clean, **never executed on GitHub**
- [ ] _Deferred:_ Trivy/pip-audit CVE debt (`python-multipart`, `starlette`, `ecdsa` via
      `python-jose`) — CI expected red until triaged · real VPS deploy (SSH, nginx, UFW, GHCR
      push/pull) never attempted · CPU reservations are no-ops outside Swarm (memory
      reservations do apply) · `UvicornWorker` deprecated upstream (works on uvicorn 0.32.1)

---

## ✅ Edge — nginx, TLS & two stacks on one VPS — *on `feat/nginx-edge-tls`*

_The front door, plus the compose blocker that made a second stack impossible. Everything
below is verified LOCALLY — a real nginx parsing and serving the real configuration against
the real application. **No VPS, no DNS, no certificate, no real handshake.** See
`docs/sop/2026-08-29-nginx-edge-two-stack-compose.md` and
`docs/deployment/NGINX_TLS.md`._

- [x] **Two stacks on one host** — `docker-compose.prod.yml` parameterised: project name from
      `COMPOSE_PROJECT_NAME` (default unchanged), all four `container_name:` keys removed
      (a fixed container name is global to the daemon, so it collides regardless of project),
      `API_PORT` per environment — production 8000, staging 8001
- [x] **Volume separation proven, not assumed** — both stacks up simultaneously; distinct
      containers, networks, ports and volumes; a marker row written into each Postgres and read
      back from the correct one; `down -v` on staging destroyed only staging's three volumes and
      production's marker row survived
- [x] Every reference to the old container names audited — `deploy-stack` already resolved via
      `docker compose … ps -q api`; `Makefile` goes through `$(PROD_COMPOSE)`; only prose in
      three docs named them
- [x] **Bug found and fixed:** `API_PORT` was read as `grep … | cut … || echo 8000` in three
      places. `||` tests the last command and `cut` exits 0 on empty input, so a missing
      `API_PORT` gave `http://127.0.0.1:/…` and rolled the deploy back for the wrong reason.
      Fixed in `deploy-stack/action.yml`, `cd.yml` and the `Makefile`
- [x] **nginx configuration version-controlled** in `deploy/nginx/` — http-level `conf.d/`
      (TLS, hardening, upstreams, rate-limit zones), shared `snippets/`, thin per-host vhosts,
      an ACME bootstrap vhost, and a `default_server` catch-all using `ssl_reject_handshake`
- [x] TLS 1.2 floor, ECDHE-only ciphers, session cache with tickets off, HTTP/2, HTTP→HTTPS
      301, HSTS **without `preload`** on either host (an apex-wide, irreversible decision that
      is not a subdomain's to make)
- [x] Security headers on every response including 4xx/5xx (`always`), strict `default-src
      'none'` CSP for the API, a separate relaxed CSP for the docs pages
- [x] `client_max_body_size` — `1m` globally, `3m` on `= /api/v1/onboarding/logo`, sized so a
      2–3 MB upload reaches the app and gets its actionable 422 instead of nginx's HTML 413
- [x] Edge rate limiting as a coarse backstop ~15× above the app's per-user budget, in separate
      zones per environment; staging's auth zone deliberately looser so the CD gate cannot 429
      itself
- [x] JSON error pages for 413/429/502/503/504 in the app's own `{"error":{"code",…}}` envelope,
      with `proxy_intercept_errors off` so the application's own bodies pass through untouched
- [x] gzip on; brotli and HTTP/3 shipped **commented** with the exact enablement steps — both
      fail to parse on an nginx without the module/build, so neither may be enabled blind
- [x] `deploy/nginx/test/verify-local.sh` — real nginx, real backends, self-signed certs at the
      real cert paths: **`nginx -t` clean, 35 assertions, 0 failures**
- [x] `docs/deployment/NGINX_TLS.md` — DNS, first-time setup, certbot `--webroot`, renewal and
      how to prove it before it matters, the port map, and 502/504/handshake troubleshooting
- [x] `DEPLOYMENT_GUIDE.md` / `GITHUB_ACTIONS_SETUP.md` corrected — **nginx is a hard
      prerequisite for the staging E2E gate**, and therefore for reaching production at all.
      Neither document said so
- [ ] **`X-Forwarded-For` defect — diagnosed and proven, deliberately NOT fixed here.** Behind
      nginx every unauthenticated request keys the app's limiter on the Docker bridge gateway,
      so login/signup/forgot-password share one 120/min bucket for the whole internet. The fix
      is one env var, `FORWARDED_ALLOW_IPS=*`, in each encrypted `.env` — Adebayo's action.
      (A CIDR does **not** work: gunicorn rejects it and the container crash-loops.) Documented
      in `.env.production.example` and NGINX_TLS.md §11
- [ ] **Staging posture decided, not enforced** — recommendation is no auth: `noindex`, full
      TLS/header parity, rate limits, docs served (the gate needs `openapi.json`; production
      404s all three). Basic auth is documented and ready to uncomment, with the CD credential
      path verified against the pinned httpx — but it is **off**
- [ ] _Deferred:_ nothing has touched a real VPS — no certificate, no handshake, no SSL Labs
      grade, no certbot renewal, HTTP/3 and brotli never parsed by a capable nginx · the nginx
      upstreams hardcode 8000/8001 while the stacks read `API_PORT` from `.env`, with nothing
      enforcing agreement · no off-host certificate-expiry monitoring · `deploy/nginx/` is not
      linted by CI

---

## ⬜ Upcoming (from PRD — mapped as we reach each)

- [ ] **Module 03 — AI Co-Founder** (unblocks deferred AI narratives/recommendations/panels)
- [ ] **Module 20 — Notifications** (real delivery + quarterly re-assessment cron)
- [ ] **Module 17 — Learning Academy** — *junior handoff prepared* · brief `docs/handoff/module-17-learning-academy.md` · planned blueprint `docs/architecture/planned/modules-17-21-junior-handoff.md`
- [ ] **Module 21 — Founder Journal** — *junior handoff prepared* · brief `docs/handoff/module-21-founder-journal.md` · planned blueprint `docs/architecture/planned/modules-17-21-junior-handoff.md`
- [ ] Remaining PRD modules — to be mapped into their own sections as scope firms up

**Reference docs:** system architecture blueprint `docs/architecture/system-architecture.md` (sync/verify after each module); planned-module blueprints under `docs/architecture/planned/`.

---

## Deferred follow-ups (tracked, non-blocking)

- [ ] `complete_assessment` should return `job_ids` (parity with `complete_onboarding`)
- [ ] Index `assessments.created_by` FK
- [ ] `compare` error distinction (in-progress vs not-found) if FE needs it
- [ ] `team_size` scoring tie edge case
- [ ] `MFA_ENCRYPTION_KEY` must be set per environment (empty → 500)
- [x] Commit `poetry.lock` (currently gitignored) before reproducible deploy — *un-gitignored and
      git-staged 2026-08-27; still needs an actual commit, nothing is committed yet*
- [ ] `LocalStorage` returns a filesystem path, not an HTTP URL — `logo_url` not FE-renderable until a URL-returning storage backend lands
- [ ] No async worker draining `jobs` yet (Modules 05/06 decide)
- [ ] Trivy: **3 HIGH** remaining (all `starlette` 0.46.2; was 6 before the
      `python-multipart` bump). 0 CRITICAL, zero OS-package findings. Nothing
      suppressed in `.trivyignore`; CI's `build` job is expected red until triaged
- [ ] `pip-audit`: **10 advisories** remaining — `starlette` (9) + `ecdsa` 0.19.2
      (`PYSEC-2026-1325`, no fix, via `python-jose`); was 16. Nothing suppressed
      in `.github/security/pip-audit-ignores.txt`
- [ ] Migrate `python-jose` → `PyJWT` to drop the unfixable `ecdsa` advisory
      (`CVE-2024-23342` / `PYSEC-2026-1325`, the Minerva attack — surfaced by
      `trivy fs`, hidden from the image scan by `--ignore-unfixed`)
- [ ] Refactor `validate_answer` (`app/services/assessment/engine.py`) so ruff's
      `max-complexity` can drop 12 → the common default of 10. It is the only
      function above 10
- [ ] Enable SonarCloud (create project, replace the CHANGE_ME `projectKey` /
      `organization`, add `SONAR_TOKEN`) — see `docs/deployment/GITHUB_ACTIONS_SETUP.md`
- [ ] Triage the first CodeQL baseline, then decide whether to promote it from
      report-only to blocking via branch-protection code-scanning requirements
- [ ] Confirm Dependabot actually opens Docker **digest** PRs — the digest lives in
      an `ARG PYTHON_IMAGE=...` default rather than a bare `FROM` literal, which its
      Docker parser is not guaranteed to follow. If nothing appears within two
      weeks of enabling, refresh by hand and treat as unverified
- [ ] **Both compose files remain unscanned by any IaC tool.** `trivy config`
      targets Dockerfile/K8s/Terraform/CloudFormation/Helm; Checkov 3.3.15 has no
      `docker_compose` framework at all. Closing this needs a compose-specific
      linter, not another general IaC scanner
- [ ] `starlette` 0.46.2 — **3 HIGH** still blocking `trivy image`. Stays pinned
      even with fastapi 0.141.1, so it needs real compatibility work (starlette
      1.x is a major version, not a drop-in)
- [ ] **Only 13 of 27 e2e tests gate production.** The other 14 need
      `EMAIL_FILE_DIR` on the same machine as the test run (they reach the
      `mailbox` fixture, mostly via `make_verified_user`), which a remote runner
      does not have. They still run in CI against a local server. Widening needs
      mail-dir transport over SSH or an on-VPS runner
- [ ] **No secret has been created yet.** `.env.staging.enc` / `.env.production.enc`
      do not exist and `ENV_ENCRYPTION_KEY` is not in GitHub — the one-time setup
      in `docs/deployment/ENV_ENCRYPTION.md` §4 is Adebayo's to run
- [ ] `APP_URL` must be set as a **staging** environment variable, not only
      production — the staging gate uses it as `E2E_BASE_URL`
- [ ] The staging/production deploy path has **never run against a real VPS** — no
      SSH deploy, no scp, no GHCR pull from a server; CD has never executed in its
      three-job shape
- [ ] CodeQL, `dependency-review` and SonarCloud have **never executed** — none can
      run locally; first signal comes from the first GitHub Actions run/PR
- [ ] The `SECRET_KEY` committed to `.env.example` in `36ee5d51` is public in git history and must
      be treated as compromised — confirm no deployed environment ever used it; clearing it from
      history needs `git-filter-repo` + force-push + everyone re-cloning
- [ ] Production deploy path (`docker-compose.prod.yml`, hardened `Dockerfile`, `cd.yml`) has
      never been exercised against a real VPS — no SSH deploy, no nginx, no UFW, no GHCR
      push/pull; the workflows have never executed on GitHub
- [ ] `deploy.resources.reservations.cpus` is declarative-only outside Swarm (verified
      `CpuShares`/`CpuQuota`/`CpuPeriod` all `0` on Compose v2.40.3) — memory reservations do apply
- [ ] `uvicorn.workers.UvicornWorker` is deprecated upstream in favour of the `uvicorn-worker`
      package — works today on uvicorn 0.32.1
- [ ] Local venv runs Python 3.14 while the project targets 3.11 (CI uses 3.11) — pre-existing,
      not introduced by the deployment hardening pass
- [ ] **`FORWARDED_ALLOW_IPS=*` is not set in any real environment.** Until it is added to
      `.env.staging` / `.env.production` and re-encrypted, the app's anonymous rate limit is a
      single global bucket and `X-Forwarded-Proto` is not honoured behind nginx
- [ ] nginx upstream ports (8000/8001 in `deploy/nginx/conf.d/20-upstreams.conf`) and each
      stack's `API_PORT` must agree; nothing enforces it. Disagreement shows up as a 502 on one
      vhost only. A deploy-time assertion would close it
- [ ] No certificate-expiry monitoring from off-host — certbot can fail silently on a rate limit
      or a DNS change
- [ ] `deploy/nginx/` has no CI coverage. A job running just `nginx -t` inside the nginx
      container would be cheap and is not done

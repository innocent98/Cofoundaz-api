# Backend Kickoff Brief — cofoundaz-api

> **Read this first.** This is the entry point for building the Cofoundaz
> backend. It orients a fresh session: what we're building, where the source of
> truth lives, the conventions every module must honor, the module map, and
> where to start. It is **not** the spec — the PRD is. This points you at it.

---

## 1. What we're building

Cofoundaz is an **AI operating system for founders** — one workspace that takes
a startup from idea to profitability via a Startup Health Score, a bench of AI
advisors (the "AI Co-Founder"), a stage-based roadmap, and a daily mission.

`cofoundaz-api` is the **backend** for the authenticated product (`/app/*`) and
the auth flows behind the already-built marketing site. The frontend
(`../cofoundaz/`) ships the public marketing website and unwired `/login` /
`/signup` shells; this repo makes them real and powers the 26 product modules.

## 2. Source of truth (read in this order)

| Doc | Path | Use |
|---|---|---|
| **Technical PRD** | `../Cofoundaz_Technical_PRD.md` | The spec. Part 4 = module-by-module (entities, endpoints, events). §2.2 = backend platform conventions (below). |
| UI handoff comps | `../# Cofoundaz Web App UI Build/` | Screen designs + verbatim copy per module. |
| Frontend repo | `../cofoundaz/` | The client that consumes this API. `content/types.ts` and the `(auth)` shells hint at the contract. |
| This scaffold's README | `./README.md` | How to run, structure, tooling. |

**How we work:** brainstorm → plan → build, **module by module**. Each module
gets its own design pass (data model + endpoints + events), a plan, then TDD
implementation with migrations. Don't try to build 26 modules at once.

## 3. The stack (already scaffolded)

The backend-devops-template is already applied. Don't re-scaffold — build on it.

- **Python 3.11**, **FastAPI 0.115** (async), **Pydantic v2** / pydantic-settings
- **SQLAlchemy 2.0** + **Alembic** migrations, **PostgreSQL** (psycopg2)
- **Redis** (caching / pub-sub / rate limiting)
- **JWT** via python-jose; auth/security in `app/core/security.py`
- **Loguru** structured logging; **pytest** (+ asyncio, cov, mock)
- **Docker** + docker-compose; **pre-commit**; **GitHub Actions** CI
- Poetry; `Makefile` targets: `run`, `test`, `test-cov`, `lint`, `format`, `migrate`, `migrate-create`, `docker-up/down`, `shell`

**Layout** (`app/`): `api/v1/` (routers/endpoints) · `core/` (config, logger, security) · `db/` (`base`, `session`, `models/`) · `schemas/` (Pydantic) · `services/` (business logic) · `middleware/` · `utils/`. Keep endpoints thin; put logic in `services/`.

## 4. Platform conventions every module must honor (PRD §2.2)

These are cross-cutting — decide them **once**, in the first design pass, and
every module inherits them:

- **Response envelope** — success `{ "data": …, "meta": { pagination } }`; error `{ "error": { "code", "message", "field_errors": [...] } }`. Machine-readable `code` (e.g. `RUNWAY_MODEL_INVALID`).
- **Pagination** — cursor-based: `?limit=25&cursor=…` → `meta: { next_cursor, total_estimate }`.
- **Idempotency** — side-effectful/billable POSTs accept an `Idempotency-Key` header.
- **Async AI jobs** — `POST …/generate` → `202 { data: { job_id, status: "queued" } }`; poll `GET /api/v1/jobs/{job_id}` or subscribe on WS `jobs.{job_id}`. Terminal: `succeeded | failed | cancelled`. Completion also emits a notification. **→ needs a worker/queue.**
- **Realtime** — WebSocket `wss://…/ws`; channels `workspace.{id}.activity`, `jobs.*`, `notifications.{user_id}`, `ai.chat.{conversation_id}` (token streaming).
- **RBAC** — enforced at the gateway (route→role map) **and** service layer (row policy on `startup_id` + a role-grants table). Standard 403 copy.
- **Audit** — every admin/super-admin mutation and every impersonated request writes `audit_log` (actor, on_behalf_of, action, entity, before/after hash, ip, ts).
- **Rate limits** — 120 req/min/user; AI generation metered by plan credits (Module 24); `429` with `Retry-After`.
- **Multi-tenancy** — everything is scoped to a `startup_id` (workspace). This is the spine of the data model and RBAC.
- **AI prompts** — DB-managed (`ai_model_configs`), referenced by `prompt_key@version`. Never hardcode prompts in services.

## 5. The 26 modules (PRD Part 4)

| # | Module | | # | Module |
|---|---|---|---|---|
| 01 | Authentication & Onboarding | | 14 | Funding Hub |
| 02 | Founder Dashboard | | 15 | Investor Readiness |
| 03 | AI Co-Founder | | 16 | Marketplace |
| 04 | Today's Mission | | 17 | Learning Academy |
| 05 | Startup Roadmap | | 18 | Documents & Templates |
| 06 | Startup Health Score | | 19 | Calendar & Milestones |
| 07 | Startup Assessment | | 20 | Notifications |
| 08 | Business Builder | | 21 | Founder Journal |
| 09 | Validation Hub | | 22 | Analytics & Reports |
| 10 | Marketing Hub | | 23 | Team Collaboration |
| 11 | Sales Hub | | 24 | Subscription & Billing |
| 12 | Finance Hub | | 25 | Admin Portal (`/admin`) |
| 13 | Legal & Compliance | | 26 | Super Admin Portal (`/super-admin`) |

### Proposed build sequence (to validate in brainstorming)

The modules are interdependent — Health Score aggregates signals from many hubs,
Missions/Roadmap are AI-generated, everything hangs off a workspace and the AI
Co-Founder. A sensible dependency-first order:

1. **Foundation (cross-cutting):** the platform conventions in §4 as shared
   infrastructure — envelope, error handling, auth/JWT, RBAC + `startup_id`
   scoping, the jobs/worker substrate, the notifications spine, WebSocket layer.
2. **Module 01 — Auth & Onboarding** (see §6). Unblocks the FE shells; creates
   users, workspaces, profiles.
3. **Module 07 — Assessment** + **Module 06 — Health Score** (onboarding ends by
   triggering `roadmap.generate` + `healthscore.initialize`).
4. **Module 05 — Roadmap** and **Module 04 — Today's Mission** (the daily loop).
5. **Module 03 — AI Co-Founder** (the advisor layer many modules call into).
6. **Module 02 — Dashboard** (aggregates the above).
7. Hubs (**08–15**), then **24 Billing** (gates AI credits), then **16–23**, then
   **25/26 Admin**.

*This is a proposal, not a decision — pressure-test it when we brainstorm.*

## 6. First module: 01 — Authentication & Onboarding

Recommended starting point: foundational (everything is scoped to a
user+workspace), and it makes the already-built FE `/login` and `/signup` real.

- **Services:** `auth-service`, `onboarding-service`. **API base:** `/api/v1/auth`, `/api/v1/onboarding`.
- **Auth surface:** signup (email/password + Google/Apple OAuth), email verification, login (with lockout after 5 fails/15 min), MFA (TOTP + SMS, backup codes), forgot/reset password (no user enumeration), refresh tokens.
- **Onboarding:** 6-step resumable wizard (founder profile → startup profile → industry & stage → 90-day goals → invite team → kickoff assessment); autosave per step; `POST /onboarding/complete` triggers `roadmap.generate` + `healthscore.initialize` jobs.
- **Entities (PRD):** `users(id,email,password_hash,mfa_type,mfa_secret,status,…)` · `auth_sessions(id,user_id,refresh_token_hash,ip,ua,expires_at)` · `startups(id,name,description,website,logo_url,industry,business_model,stage,country,created_by)` · `startup_profiles(startup_id,goals[],notes,onboarding_step,onboarding_completed_at)` · `invitations(id,startup_id,email,role,token,status,expires_at)`.
- **Events:** `auth.user.registered`, `auth.user.verified`, `workspace.created`, `workspace.member.invited`, `onboarding.completed`.
- **Notifications:** welcome email, verify email, onboarding-complete in-app, invitation email.

Full detail: PRD **Module 01** (lines ~250–297).

## 7. FE contract to honor

- The frontend already renders `/login` and `/signup` (currently unwired shells
  in `../cofoundaz/app/(auth)/`). Match its expected request/response shapes.
- Return the **standard envelope** (§4) so the FE's fetch layer can be uniform.
- Auth token flow: `POST /auth/login` → `{ access_token, refresh_token, mfa_required? }` (PRD 01.3).
- Check `../cofoundaz/content/types.ts` and `content/auth.ts` for copy/field
  expectations before finalizing schemas.

## 8. Open decisions to settle in brainstorming

Don't guess these — they shape the architecture:

1. **Architecture shape** — the PRD names per-module "services" and a "gateway,"
   but the scaffold is a single FastAPI app. Modular monolith now (clear
   module boundaries, one deploy) vs. actual microservices? *(Recommend
   discussing modular-monolith-first.)*
2. **Async jobs / worker** — Celery vs ARQ vs Dramatiq (Redis is already a dep).
   Needed for every `…/generate` AI endpoint.
3. **Realtime** — native FastAPI WebSockets vs a managed layer; Redis pub/sub for fan-out.
4. **AI provider & orchestration** — which LLM(s), how prompts are versioned in
   `ai_model_configs`, credit metering, streaming.
5. **AuthN specifics** — access/refresh token TTLs, rotation, session storage
   (Redis?), OAuth providers (Google/Apple) setup, MFA SMS provider.
6. **Email provider** (verification, invites, resets) and **file storage** (logo uploads).
7. **DB conventions** — UUID vs bigint PKs, soft-delete, `created_at/updated_at`
   mixin, naming, tenant-scoping helper/base query.
8. **Testing strategy** — test DB (containerized Postgres), fixtures, factory pattern, coverage gate.

## 9. How to start the next session

1. Open a session with **cwd = `cofoundaz-api/`**.
2. First message: "Let's build the Cofoundaz backend — read `docs/backend-kickoff-brief.md`, the PRD (§2.2 + Module 01), and let's brainstorm."
3. Run the **brainstorming** skill first (design before code), settle §8's
   cross-cutting decisions and the Module 01 data model, then **plan**, then
   build with TDD + a migration.
4. Available specialist agents for this repo: `backend-engineer`,
   `database-ops-engineer`, `devops-engineer`.
5. Per the repo SOP convention, each shipped module/change gets a doc in
   `docs/sop/`.

# Sales Slice A (CRM core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A real CRM backend for Sales (PRD Module 11, slice A): settings, editable pipeline stages, accounts, contacts, leads (+ CSV import, convert), deals with stage history, activities, stall scheduler + notifications, overview, live Dashboard `pipeline` KPI, ⌘K search and activity-feed visibility.

**Architecture:** Mirrors Marketing: `app/db/models/sales.py`, `app/schemas/sales.py`, `app/services/sales/*`, one router `app/api/v1/endpoints/sales.py` gated by `require_role(*SALES_ROLES)`, notifications in `services/notifications/registry.py` + `audience.py`, stall scan in `worker/scheduler.py` with a handler in `worker/handlers/scheduled.py`. Every query is `startup_id`-scoped. Stages are lazily seeded per workspace on first Sales access.

**Tech Stack:** FastAPI, SQLAlchemy 2 (Mapped), Alembic, Postgres (JSONB), Redis (CSV preview), pytest, httpx e2e.

**Spec:** `/Users/adebayovictor/Documents/dev/cofoundaz/wt-sales-design/docs/superpowers/specs/2026-10-21-sales-design.md` (§2 D-SAL-2/3/4/6/7, §3, §4, §6, §11). Gaps: `.../wt-sales-design/docs/checklist/sales-prd-gaps.md`. **Read the spec before any task.**

## Global Constraints

- Access: `SALES_ROLES = frozenset({founder, team_member})` in `app/db/models/enums.py`; every `/sales/*` route uses `require_role(*SALES_ROLES)`; mentor/consultant/accountant get 403.
- Money: `value_minor` int kobo, currency fixed `"NGN"`. No em/en dashes in user-facing copy (notification titles, error messages).
- Errors: `_validation(field, msg)` style (`AppError("VALIDATION_ERROR", msg, 422, field_errors=[...])`), `NotFound()` for cross-tenant ids. Responses via `success_response`.
- Stage rules (D-SAL-4): max 10 live stages, exactly one `won` and one `lost`, soft-delete (`archived_at`) only when empty (§11.6), win_probability 0..100. Defaults: New 10, Contacted 20, Qualified 40, Proposal 60, Negotiation 80, Closed Won 100 (won), Closed Lost 0 (lost).
- Owners (`owner_id`) must be current active founder/team_member of the workspace (validated on write, §11.8).
- DB-touching tests: `DATABASE_URL=postgresql://user:password@localhost:5432/ci_scratch TZ=UTC` ONLY. Never the dev DB, never the docker stack. LLM stub only (no LLM is called in slice A).
- Commits: no AI attribution trailers or footers of any kind; branch `feature/sales-crm-core`.
- Test password style: uuid-hex helper in e2e (GitGuardian). Test code: avoid mutating calls inside `assert` and implicit string concatenation inside lists (CodeQL).
- `db.add()` for dup-key inserts goes INSIDE `with db.begin_nested()` (SAVEPOINT rule).
- Migration head test: assert single head + revision in `alembic history`, never "is THE head".

## Review Focus

1. Cross-tenant ids (account/contact/stage/owner of another workspace) in every write body must 404/422, never link. Tests in T3/T5/T6.
2. Mentor/consultant on EVERY route returns 403 (parametrized over the route table). T8 test enumerates `router.routes`.
3. Moving a deal into the lost stage without `closed_reason`, or a stage from another workspace, 422; reopen clears closed fields. T5.
4. Stall scan run twice, or by two concurrent workers, notifies once per episode; new activity ends the episode. T7.
5. CSV with BOM, quoted commas, CRLF, >5,000 rows, >2 MB, duplicate emails within file, upload_id expired/foreign workspace. T4.

---

## File Structure

| Path | Responsibility |
|---|---|
| `app/db/models/enums.py` (modify) | `SALES_ROLES`, `LeadSource`, `LeadStatus`, `StageKind`, `ActivityKind`, `ClosedReason` |
| `app/db/models/sales.py` (create) | `SalesSettings`, `PipelineStage`, `SalesAccount`, `SalesContact`, `SalesLead`, `Deal`, `DealStageChange`, `SalesActivity` |
| `alembic/versions/0063_sales_crm_core.py` (create) | the 8 tables |
| `app/schemas/sales.py` (create) | request/response models |
| `app/services/sales/errors.py` | `_validation` (copy of marketing's helper, no cycles) |
| `app/services/sales/stages.py` | `ensure_stages`, `list_stages`, `replace_stages` |
| `app/services/sales/people.py` | `check_owner`, accounts + contacts CRUD |
| `app/services/sales/leads.py` | leads CRUD, `convert_lead` |
| `app/services/sales/csv_import.py` | preview (Redis) + commit |
| `app/services/sales/deals.py` | deals CRUD, `move_stage`, stage-change rows, won/lost events |
| `app/services/sales/activities.py` | activity CRUD, `touch_deal` (bump `last_activity_at`, clear `stalled_notified_at`) |
| `app/services/sales/overview.py` | `overview()` and `pipeline_kpi()` |
| `app/services/sales/stall.py` | `find_stalled`, `notify_stalled` |
| `app/api/v1/endpoints/sales.py` | router; registered in `app/api/v1/api.py` at `/sales` |

Tests: `tests/services/sales/*`, `tests/api/sales/*`, `tests/test_sales_crm_core_migration.py`, `e2e/test_sales_a.py`.

---

### Task 1: Enums, models, migration 0063

**Files:** Modify `app/db/models/enums.py`, `app/db/models/__init__.py` (import new models so metadata sees them). Create `app/db/models/sales.py`, `alembic/versions/0063_sales_crm_core.py`, `tests/test_sales_crm_core_migration.py`.

**Interfaces:** Produces the ORM classes in the table above. Column sets per spec §3 plus:
- `SalesSettings`: PK `startup_id`, `stall_days` int default 14 (CHECK 3..90), `outreach_postal_address` Text null, `paused_at`, `paused_reason` (slice B fields, created now).
- `PipelineStage`: `id, startup_id, name(60), position, kind, win_probability, archived_at null`; unique partial index `(startup_id, kind)` WHERE `kind in ('won','lost') AND archived_at IS NULL`.
- `Deal`: also `lost`-related `closed_*`, `last_activity_at` NOT NULL default now, `stalled_notified_at` null, `currency` default "NGN". Indexes on `(startup_id, stage_id)`, `(startup_id, last_activity_at)`.
- `SalesLead`: `converted_deal_id/contact_id/account_id` nullable FKs `ondelete SET NULL`; index on `(startup_id, lower(email))` via functional index.
- `SalesContact`/`SalesLead` email stored as given, compared lowercased.
- `SalesActivity`: CHECK at least one of account_id/contact_id/deal_id not null.
- All use `UUIDMixin, TimestampMixin`, `Enum(..., native_enum=False, length=N)`, FK `startups.id ondelete CASCADE`, user FKs `SET NULL`.
- `DealStageChange`: `id, deal_id (CASCADE), from_stage_id null, to_stage_id, changed_by null, changed_at`.

- [ ] **Step 1:** Write `tests/test_sales_crm_core_migration.py` copying the shape of `tests/test_marketing_prd_b_migration.py` (single head; `0063_sales_crm_core` in history; downgrade to `0062_marketing_prd_b` then upgrade round-trips). Run on `ci_scratch_mig`: expect FAIL (revision missing).
- [ ] **Step 2:** Add enums and `SALES_ROLES`; write models; write migration by hand (down_revision `0062_marketing_prd_b`, drop tables in reverse on downgrade).
- [ ] **Step 3:** `poetry run alembic upgrade head` on `ci_scratch_mig`, then `poetry run alembic check` (drift must be empty; fix model/migration mismatches such as index names), then the migration test: PASS.
- [ ] **Step 4:** Commit `feat(sales): models and migration 0063`.

### Task 2: Settings and stages

**Files:** Create `app/services/sales/{__init__,errors,stages}.py`, schemas in `app/schemas/sales.py`, router skeleton `app/api/v1/endpoints/sales.py` (+ register in `api.py`), tests `tests/services/sales/test_stages.py`, `tests/api/sales/test_stages_api.py`.

**Interfaces:**
- `ensure_stages(db, startup_id) -> list[PipelineStage]` seeds the 7 defaults if the workspace has no live stage; idempotent and race-safe (unique `(startup_id, position)` among live stages not required; guard with `pg_advisory_xact_lock(hashtext(startup_id))`).
- `list_stages(db, startup_id) -> list[PipelineStage]` live stages ordered by position (calls `ensure_stages`).
- `replace_stages(db, startup_id, items: list[StageIn]) -> list[PipelineStage]`; `StageIn{id: UUID|None, name: str 1..60, kind: StageKind, win_probability: int 0..100}`; list order = position. Existing ids kept, missing id = new, omitted existing stage = removed: soft-deleted if it has no deals, else 422 `field: "stages"` "Move the deals out of {name} first." Validation: 1..10 items, exactly one won and one lost, kinds of existing stages cannot change, won/lost cannot be omitted, names unique case-insensitively (422).
- Routes: `GET /sales/stages`, `PUT /sales/stages` (body `{stages: [...]}`), `GET/PATCH /sales/settings` (`stall_days` 3..90, `outreach_postal_address`).
- `get_settings(db, startup_id) -> SalesSettings` lazy-creates the row.

- [ ] **Step 1:** Tests: seeds 7 defaults once (call twice); rename/reorder keeps ids; add an 8th stage; omit empty stage soft-deletes (row remains with `archived_at`); omit stage with a deal 422 with that message; omit won stage 422; two won stages 422; 11 stages 422; duplicate name 422; `stall_days` 2 and 91 422; mentor 403 on all three routes; stages of workspace B never returned to A.
- [ ] **Step 2:** Run: FAIL. **Step 3:** Implement. **Step 4:** Run: PASS. **Step 5:** Commit.

### Task 3: Accounts and contacts

**Files:** Create `app/services/sales/people.py`; extend schemas + router; tests `tests/services/sales/test_people.py`, `tests/api/sales/test_people_api.py`.

**Interfaces:**
- `check_owner(db, startup_id, owner_id) -> None` raises `_validation("owner_id", "Owner must be a member of this workspace.")` unless an active founder/team_member membership exists. Also used by T4–T6.
- Account: `name` 1..200, `website`, `industry`, `description`, `owner_id`. Contact: `account_id` (must belong to workspace), `first_name` 1..100, `last_name`, `email` (EmailStr optional), `phone`, `title`.
- Routes: `GET/POST /sales/accounts` (list filters `q` over name, `owner_id`; pagination `limit` default 50 max 200, `offset`), `GET/PATCH/DELETE /sales/accounts/{id}`; `GET/POST /sales/contacts` (filters `account_id`, `q` over name/email), `GET/PATCH/DELETE /sales/contacts/{id}`.
- `GET /sales/accounts/{id}` returns `{account, contacts, open_deals, lifetime_won_minor}` (open_deals empty until T5 wires deals; the field exists now, T5 fills it; lifetime = sum `value_minor` of deals currently in the won stage).
- Deleting an account nulls `account_id` on contacts/deals/leads? No: refuse with 422 "Remove or move its deals first." if it has deals; contacts and leads are detached (account_id null). Deleting a contact with deals: deals' `contact_id` set null.
- Account `name` unique case-insensitively per workspace (422) so convert's match (T6) is unambiguous.

- [ ] **Step 1:** Tests: CRUD; cross-tenant `account_id` on contact create 422; foreign `owner_id` 422; duplicate account name differing by case 422 (use the SAVEPOINT rule: follow-up read in the same test must still work); list `q` filter; pagination; 404 for other workspace's id; mentor 403.
- [ ] **Step 2–5:** run FAIL, implement, run PASS, commit.

### Task 4: Leads and CSV import

**Files:** Create `app/services/sales/{leads,csv_import}.py`; extend schemas/router; tests `tests/services/sales/test_leads.py`, `test_csv_import.py`, `tests/api/sales/test_leads_api.py`.

**Interfaces:**
- Lead: `name`, `company`, `email`, `source` (`LeadSource`), `status` (`LeadStatus`), `owner_id`. At least one of name/company non-empty (422). Routes: `GET/POST /sales/leads` (filters `status`, `owner_id`, `source`, `q`; pagination as T3), `GET/PATCH/DELETE /sales/leads/{id}`. A converted lead cannot be set back or deleted (422 "This lead was already converted.").
- Email dedupe on create: duplicate lowercased email among leads or contacts of the workspace is allowed for manual create (no dedupe; only CSV dedupes, per §4).
- `preview_csv(redis, startup_id, user_id, raw: bytes) -> PreviewOut{upload_id, headers, sample_rows (first 5), suggested_mapping, row_count}`: enforce ≤2 MB (413-style `AppError("PAYLOAD_TOO_LARGE", ..., 413)`) and ≤5,000 rows (422), decode `utf-8-sig`, `csv.Sniffer`-free standard dialect, stores `{startup_id, rows}` JSON in Redis key `sales:csv:{upload_id}` TTL 1800s. `suggested_mapping: {name, company, email, source}` -> header or null, by case-insensitive header-name match (`name|full name`, `company|organisation|organization`, `email|e-mail`, `source`).
- `commit_csv(db, redis, startup_id, user_id, upload_id, mapping) -> ImportResult{added, duplicates_skipped, invalid_rows: list[{row, error}] (max 50), invalid_total}`. Unknown/expired/other-workspace upload_id: `NotFound()`. Invalid = unparseable email, or no name and no company. Dedupe by lowercased email against existing leads, contacts, and earlier rows; rows without email never deduped. Source `csv` on created leads, status `new`. Upload key deleted after commit. Rows numbered from 1 = first data row.
- Routes: `POST /sales/leads/import/preview` (multipart `file`), `POST /sales/leads/import` (`{upload_id, mapping}`). Define these BEFORE `/leads/{lead_id}` in the router so the path is not shadowed.
- Redis access through `app/core/redis.py` (inspect the existing helper; tests use the same fake/real Redis the auth tests use).

- [ ] **Step 1:** Tests per Review Focus 5 plus: mapping with missing required column 422; second commit of same upload_id 404; leads CRUD, filters, convert-lock; foreign-workspace upload_id 404.
- [ ] **Step 2–5:** FAIL, implement, PASS, commit.

### Task 5: Deals, stage moves, stage history

**Files:** Create `app/services/sales/{deals,activities}.py` (activities only `touch_deal` here); extend schemas/router; tests `tests/services/sales/test_deals.py`, `tests/api/sales/test_deals_api.py`.

**Interfaces:**
- Deal fields per §3; create body `{name, account_id?, contact_id?, value_minor >= 0, stage_id? (default first live stage), next_step, next_step_due (date), owner_id?}`. A contact must belong to the given account when both set (422).
- Create writes the initial `DealStageChange` (`from_stage_id` null), sets `last_activity_at = now`; creating directly into the won stage is allowed and sets `closed_at`.
- `move_stage(db, *, startup_id, deal_id, stage_id, actor_id, closed_reason=None, closed_note=None) -> Deal`: stage must be live and in workspace (else 422 `stage_id`); lost stage requires `closed_reason` (422 `closed_reason`); moving to the same stage is a no-op returning the deal (no history row). Into won/lost: `closed_at=now`; lost stores reason/note; won clears reason/note. Out of won/lost (reopen, §11.7): clears `closed_at/closed_reason/closed_note`. Always: insert `DealStageChange`, `touch_deal`, `write_activity(action="sales.deal.stage_changed"|"sales.deal.won"|"sales.deal.lost", entity_type="deal")`, publish via `event_bus.publish(db, "sales.deal.stage_changed", {...})` and `sales.deal.won|lost`. Payload: `{startup_id, deal_id, name, from_stage_id, to_stage_id, actor_id, href: "/sales?deal={id}"}`; actor key `actor_id` so the actor is excluded by `_actor`? For `sales.deal.won` the PRD audience is F + TM including the mover, so register it with `minus_actor=False`.
- `touch_deal(db, deal, *, at=None)`: sets `last_activity_at = at or now`, `stalled_notified_at = None`. Called on stage move, on `next_step`/`next_step_due` edit, and by activity create (T6).
- Routes: `GET/POST /sales/deals` (filters `stage_id`, `account_id`, `owner_id`, `q`, `open_only`; pagination; list items include `stage_id`, `account_name`, `contact_name`, `is_stalled` computed from workspace `stall_days`, `days_inactive`), `GET/PATCH/DELETE /sales/deals/{id}` (GET includes `stage_history`), `POST /sales/deals/{id}/stage`.
- PATCH changing `stage_id` is rejected (422 "Use the stage endpoint."). Wire `GET /sales/accounts/{id}` `open_deals` and `lifetime_won_minor` now.

- [ ] **Step 1:** Tests: create writes initial history row (from null); move writes row and bumps `last_activity_at` and clears `stalled_notified_at`; lost without reason 422; lost with reason stores; reopen from won clears `closed_*`; same-stage no-op; foreign stage / account / contact / owner 422; archived stage rejected; won publishes `sales.deal.won` (spy on bus) and writes the activity row; `next_step` edit touches, `name` edit does not; contact/account mismatch 422.
- [ ] **Step 2–5:** FAIL, implement, PASS, commit.

### Task 6: Lead convert and activities

**Files:** Extend `app/services/sales/leads.py` (`convert_lead`), `activities.py`; schemas/router; tests `tests/services/sales/test_convert.py`, `test_activities.py`, `tests/api/sales/test_convert_activities_api.py`.

**Interfaces:**
- `convert_lead(db, *, startup_id, lead_id, actor_id) -> ConvertResult{deal, account|None, contact}` per D-SAL-6/§11.11: account found case-insensitively by `company` or created (none when company empty/blank); contact from lead (first/last split of `name` on first space; if no name use company as `first_name`), `email` copied (may be null); deal named `"{company or name}"` in the first live stage (`value_minor` 0), owner = lead owner else actor; lead `status=converted` with three links. Already converted: 422 "This lead was already converted." Atomic (one transaction; SAVEPOINT around the account create to survive a racing duplicate-name insert by re-selecting).
- `POST /sales/leads/{id}/convert` -> `{deal, account, contact}`; initial `DealStageChange` row as in T5 (reuse `create_deal` service).
- Activity: `kind`, `body` 1..5000, `occurred_at` default now, at least one of account_id/contact_id/deal_id (422 otherwise), all in workspace; `author_id` = caller. Routes: `GET /sales/activities?account_id|contact_id|deal_id` (one filter required, 422 otherwise; newest first; pagination), `POST /sales/activities`, `PATCH/DELETE /sales/activities/{id}` author only (others 403 via `Forbidden()`). Create with `deal_id` calls `touch_deal(at=occurred_at if not in the future else now)`. Edit/delete never touch the deal. `write_activity` namespace `sales`, entity_type `sales_activity`.

- [ ] **Step 1:** Tests: convert with existing account (case-insensitive) reuses it; no company -> no account, deal/contact `account_id` null; no email contact; double convert 422; convert lands in first stage with history row; activity without target 422; foreign deal 422; non-author PATCH/DELETE 403, author OK; activity on a deal clears `stalled_notified_at`; list requires a filter.
- [ ] **Step 2–5:** FAIL, implement, PASS, commit.

### Task 7: Stall scheduler and notifications (Opus review)

**Files:** Create `app/services/sales/stall.py`; modify `app/worker/scheduler.py`, `app/worker/handlers/scheduled.py`, `app/services/notifications/{registry,audience,categories}.py`; tests `tests/services/sales/test_stall.py`, `tests/worker/test_sales_stall_scheduler.py`, `tests/services/notifications/test_sales_notifications.py`.

**Interfaces:**
- `SCHED_SALES_STALL = "scheduled.sales.stall"`; `_due_sales_stalls(db, now) -> list[Due]`: deals in an open stage (kind `open`, stage not archived), `Startup.deleted_at IS NULL`, `stalled_notified_at IS NULL`, `last_activity_at <= now - stall_days` (join `sales_settings` with `coalesce(stall_days, 14)`), batch cap 500. `Due("sales.stall", str(deal_id), period=last_activity_at UTC `%Y%m%dT%H%M%S%f`, ...)` so the existing `_claim` unique `(task, scope, period)` makes the scan idempotent and concurrent-safe per episode. Add to `scheduler_tick`.
- `handle_sales_stall(db, job)` -> `stall.notify_stalled(db, deal_id, startup_id, now)`: `SELECT ... FOR UPDATE` the deal, re-check workspace matches payload, still open, `stalled_notified_at IS NULL`, still past threshold (an activity since the scan wins); set `stalled_notified_at = now`; publish `sales.deal.stalled` payload `{startup_id, deal_id, name, days, owner_id|None, href: "/sales?deal={id}&followup=1"}` (days = whole days since `last_activity_at`).
- Registry: `sales.deal.stalled` recipients = deal owner if still an active F/TM member else all active founders (custom recipients function using `payload["owner_id"]`); title `Deal stalled: {name} has had no activity for {days} days`, body `Suggest a follow-up`. `sales.deal.won` audience `SALES_ROLES` via `_audience("sales.deal.won", minus_actor=False)`, title `Deal moved to Closed Won: {name} 🎉`; dedupe once per deal per day (§11.7): in the handler path, before creating, skip if a `notifications` row of this type exists for the same `data->>'deal_id'` created today (UTC date) for the startup. Add both to `AUDIENCE` (`SALES_ROLES`; stalled is `SALES_ROLES` too so the read filter hides it from other roles). `sales.deal.stage_changed` and `sales.deal.lost` are published but NOT in `SPECS` (no notification). Categories: leave in-app only.
- Stall episode ends when `touch_deal` clears `stalled_notified_at`; the next scan gets a new period key (new `last_activity_at`).

- [ ] **Step 1:** Tests: deal at 14d inactive found, 13d not; custom `stall_days` 3; won/lost-stage and archived-stage deals not found; already-notified not found; `scheduler_tick` twice enqueues once; second tick after `touch_deal` and another 14d enqueues again; two sessions racing `notify_stalled` (second sees `stalled_notified_at` set, publishes nothing); handler skips when an activity landed after the scan; owner recipient vs owner-left falls back to founders; mentor never receives, and read filter hides the type from mentor; `sales.deal.won` second move into won same day creates no second notification, reopen+won next day does.
- [ ] **Step 2–5:** FAIL, implement, PASS, commit.

### Task 8: Overview, Dashboard KPI, search, activity visibility, permission sweep

**Files:** Create `app/services/sales/overview.py`; modify `app/services/dashboard/kpis.py`, `kpi_history.py` (INTEGER_METRICS += `pipeline`), `kpi_snapshots.py` (`current_values` adds pipeline), `app/services/search/service.py`, `app/platform/activity.py`; tests alongside each + `tests/api/sales/test_permissions.py`.

**Interfaces:**
- `overview(db, startup_id) -> {total_pipeline_minor, weighted_forecast_minor, win_rate (float percent 0..100 or null when no closed deals), currency: "NGN", stages: [{stage_id, name, kind, deal_count, value_minor}]}`; pipeline = sum of open-stage deals; weighted = sum(value * win_probability / 100) rounded to int; win rate = won/(won+lost) all time over current deals. Route `GET /sales`.
- `_pipeline(db, startup, role)` in `kpis.py`: `restricted` for non-SALES_ROLES; `no_data` when the workspace has no deals; else `{status:"ok", value_minor, currency:"NGN"}`. Update module docstring (`not_built` no longer used by pipeline), `_TREND_FIELD["pipeline"]="value_minor"`, wrap with `_with_trend` under the savepoint runner. Update existing dashboard KPI tests that asserted `not_built`.
- Search: four `Source`s (deals by name, accounts by name, contacts by first/last/email, leads by name/company/email), roles `SALES_ROLES`, hrefs `/sales?deal={id}`, `/sales/accounts/{id}`, `/sales/contacts?contact={id}`, `/sales/leads?lead={id}`; add Pages "Sales Hub /sales", "Leads /sales/leads", "Accounts /sales/accounts" (and Sequences/Coach/Analytics pages are NOT added in A). Update the module docstring line about contacts.
- Activity: `_RESTRICTED_NAMESPACES["sales"]=SALES_ROLES`, `_RESTRICTED_ENTITIES` add `deal`, `sales_activity`, `sales_lead`, `sales_account`, `sales_contact` -> `SALES_ROLES`. All sales mutations (create/update/delete of leads, accounts, contacts, deals) call `write_activity` with action `sales.<entity>.<verb>` (add in T3–T6 services; this task verifies via test that every mutating route writes one).
- Permission sweep test: build a mentor and a consultant client; iterate `router.routes` of the sales router, call every method/path with dummy ids/bodies, assert 403 (not 404/422).

- [ ] **Step 1:** Tests: overview math (weights, win rate null, archived stage excluded, per-stage rows incl. empty stages); KPI statuses (restricted/no_data/ok), trend present after a snapshot; `capture_snapshots` stores `pipeline`; search hits for founder, none for mentor, cross-tenant isolation; `activity_visible(mentor, "sales.deal.won", "deal")` False and SQL filter agrees; sweep test.
- [ ] **Step 2–5:** FAIL, implement, PASS, commit.

### Task 9: E2E, docs, CI parity, PR

**Files:** Create `e2e/test_sales_a.py` (pattern of `e2e/test_marketing_prd_a.py`; captures to `e2e/_captures/sales_a/*.json`), `docs/fe-integration-guide-sales-crm.md`, `docs/sop/2026-10-21-sales-crm-core.md`; modify `docs/checklist/PROJECT_CHECKLIST.md`.

- [ ] **Step 1:** E2E journey (lazy app imports; uses scheduler tick + worker drain helpers like the Marketing e2e): onboard founder -> GET stages (seeded) -> PUT stages (rename + add + failed omit-with-deals 422) -> settings stall_days -> create account/contact -> leads create, CSV preview + import (with duplicate + invalid row), convert -> create deal -> stage moves (lost without reason 422, lost with reason, reopen, won; notification list shows won once) -> activities (create/patch/delete, filter) -> force a stall (set `last_activity_at` back via DB) -> tick + drain -> stalled notification captured -> overview -> dashboard KPIs `pipeline` ok -> search -> activity feed contains sales items -> a second invited mentor user if the harness supports invitations (else label as unit-only). Capture every request/response body.
- [ ] **Step 2:** Run the e2e via `./scripts/e2e_run.sh` equivalent against a scratch DB/port (read the script; do not touch the docker stack). Fix until green.
- [ ] **Step 3:** Write the FE guide only from the captures (verbatim bodies, error strings, nesting traps, stall UX, state notes, verification table marking anything not live). Write the SOP and checklist rows (Sales module: slice A done/in review; B, C, FE-A planned).
- [ ] **Step 4:** Reproduce every CI gate from `.github/workflows/ci.yml` locally with the project's poetry env: black, isort, ruff, mypy; full pytest with coverage gate (copy the exact flags from the workflow) on `ci_scratch`; alembic one head / upgrade from empty / `alembic check` / downgrade base + upgrade on `ci_scratch_mig`; e2e; gitleaks/semgrep/bandit/pip-audit as far as installed; sonar-style quality notes. Report anything not reproducible.
- [ ] **Step 5:** `git push -u origin HEAD:feature/sales-crm-core`; open PR to `develop` (no attribution lines in the body); do NOT merge. Note in PR: Opus review requested for permission checks (T8 sweep) and scheduler (T7).

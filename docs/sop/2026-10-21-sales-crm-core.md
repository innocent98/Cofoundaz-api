# SOP — Sales Hub slice A: CRM core (Module 11)

| | |
|---|---|
| **Shipped** | Branch `feature/sales-crm-core` → PR to `develop` (link in the PR; built, awaiting review and merge) |
| **Date** | 2026-10-21 |
| **Area** | Sales: `app/db/models/sales.py`, `app/schemas/sales.py`, `app/services/sales/*`, `app/api/v1/endpoints/sales.py`, scheduler, notifications, Dashboard KPI, search |
| **Migration** | `0063_sales_crm_core` (8 new tables, additive) |
| **Spec / plan** | `wt-sales-design/docs/superpowers/specs/2026-10-21-sales-design.md`, `docs/superpowers/plans/2026-10-21-sales-crm-core.md` |
| **FE guide** | `docs/fe-integration-guide-sales-crm.md` (from `e2e/_captures/sales_a/`, 66 captures) |

## What shipped

A real CRM backend behind `/api/v1/sales`: editable pipeline stages, settings, accounts, contacts, leads
(manual + two-step CSV import + "Make it a deal" conversion), deals with stage moves and history,
activities, a stall scheduler with two notifications, the board overview, the live Dashboard `pipeline`
KPI, ⌘K search and activity-feed rows.

## Why

Sales (PRD Module 11) had a UI comp and no backend; the Dashboard `pipeline` tile was a placeholder
(`no_data`). Slice A is the base every later Sales slice (sequences, coach, analytics) builds on.

## How (decisions)

| Decision | Choice | Alternative rejected |
|---|---|---|
| Access (D-SAL-2) | `SALES_ROLES` = founder + team member, one `require_role` on the router; every other role 403 | Per-route grants (Marketing precedent D-MKT-1 is also full access) |
| Stages (D-SAL-4) | Per-workspace rows, lazily seeded on first Sales call under an advisory lock; `PUT /stages` replaces the whole list; max 10, exactly one won and one lost; remove only when empty, as a soft delete (`archived_at`) so history keeps the name | Hard delete (breaks `deal_stage_changes` names); a seed migration per workspace |
| Money (D-SAL-3) | `value_minor` BIGINT kobo, currency fixed `"NGN"` | Decimal column |
| Convert (D-SAL-6) | Find-or-create account by case-insensitive company name, contact from the lead, deal in the first stage; no company means no account | Always create an account |
| Stall (D-SAL-7) | `deals.last_activity_at` stamped by activity, stage move, next-step edit; scanner picks open deals past the workspace `stall_days`; `notify_stalled` re-checks under a row lock and sets `stalled_notified_at`; new activity clears it so a fresh episode can alert | Alert on every scan; trust the job payload |
| Won notification | Deduped per deal per UTC day in the recipient resolver (`_deal_won_once_a_day`) | Dedupe in the endpoint |
| CSV import | Preview parses into Redis (30 min, scoped to workspace + user), commit claims the upload with `GETDEL`, then inserts; duplicates matched case-insensitively against leads, contacts and the file itself; error text never echoes cell content | Store the file in the DB; synchronous one-shot upload |
| Websites | http/https only (a `javascript:` value must never be stored, the FE renders it as a link) | Free text |
| Health Score (D-SAL-10) | Not wired: no sales signal in this build-out | n/a |

## What's involved

| Area | Paths |
|---|---|
| Enums | `app/db/models/enums.py` (`SALES_ROLES`, `LeadSource`, `LeadStatus`, `StageKind`, `ActivityKind`, `ClosedReason`) |
| Models + migration | `app/db/models/sales.py` (`SalesSettings`, `PipelineStage`, `SalesAccount`, `SalesContact`, `SalesLead`, `Deal`, `DealStageChange`, `SalesActivity`), `alembic/versions/0063_sales_crm_core.py` |
| API | `app/api/v1/endpoints/sales.py` (registered at `/sales`), `app/schemas/sales.py` |
| Services | `app/services/sales/{stages,people,leads,csv_import,deals,activities,overview,stall,errors}.py` |
| Scheduler + worker | `app/worker/scheduler.py` (`_due_sales_stalls`, `SCHED_SALES_STALL`), `app/worker/handlers/scheduled.py` (`handle_sales_stall`) |
| Notifications | `app/services/notifications/registry.py`, `audience.py` (`sales.deal.won`, `sales.deal.stalled`, founders + team members only) |
| Dashboard / search | `app/services/dashboard/kpis.py` (`pipeline` via `overview.pipeline_kpi`), `app/services/search/service.py` (deals, accounts, contacts, leads, Sales pages) |
| Tests | `tests/services/sales/*`, `tests/api/sales/*` (incl. a permission sweep over the route table), `tests/services/notifications/test_sales_notifications.py`, `tests/worker/test_sales_stall_scheduler.py`, `tests/test_sales_crm_core_migration.py` |
| E2E | `e2e/test_sales_a.py`, captures `e2e/_captures/sales_a/` |

Endpoints (all founder + team member): `GET /sales`; `GET|PUT /sales/stages`; `GET|PATCH /sales/settings`;
accounts, contacts, leads, deals, activities CRUD; `POST /sales/leads/import/preview`, `POST /sales/leads/import`,
`POST /sales/leads/{id}/convert`; `POST /sales/deals/{id}/stage`.

## Verification

- Unit/integration: sales suites above on `ci_scratch` (real Postgres, rollback per test).
- Live: `e2e/test_sales_a.py` passes against a real uvicorn server with a scratch DB (`b_e2e`, port 8041), stub LLM, file mail backend.
  The journey covers stages (seed, edit, 422s), settings, account/contact, leads, CSV preview and import
  (duplicate + invalid rows), convert (with and without company), deals, stage moves (lost needs reason,
  reopen, won, one won notification), activities, a forced stall + scheduler tick + worker drain (one stalled
  notification, none on a second tick), overview, Dashboard KPI, search, activity feed, and a mentor 403.
- Not live (unit only): consultant/investor/accountant/legal advisor 403s, CSV size/row limits, cross-workspace
  id rejection, owner-based stalled recipient. The stall clock is aged by a direct SQL update in the e2e (no endpoint can).

## Operate / roll back

- Deploy: `alembic upgrade head` creates 8 tables; no new env vars, no new service. The existing scheduler and
  worker processes pick up the new job type (`scheduled.sales.stall`); the CSV preview needs the existing Redis.
- Roll back: `alembic downgrade -1` drops the 8 tables in FK order (`sales_activities`, `sales_leads`,
  `deal_stage_changes`, `deals`, `sales_contacts`, `sales_accounts`, `pipeline_stages`, `sales_settings`); this
  **deletes all Sales data**. The Dashboard tile falls back to `no_data` only if the code is also reverted. Revert the PR for code.
- Queued `scheduled.sales.stall` jobs for a reverted build have no handler: purge them before downgrading.

## Follow-ups

- **FE-A**: Pipeline board, Leads, Accounts and drawers against `docs/fe-integration-guide-sales-crm.md`.
- **Slice B**: sequences and sending (separate outreach provider, guardrails, unsubscribe, webhook). **Slice C**: coach, call prep, analytics.
- **Health Score signal (D-SAL-10)**: Health Score does not read sales data; needs per-stage targets, like D-HEA-2.
- **Roles**: investor and legal advisor are 403 today. Whether investors should get read-only pipeline access is an open product question.
- CSV `source` column is accepted but ignored (every imported lead is `source: "csv"`); either honour it or drop it from the mapping.
- Activity-feed copy reads `Logged a email` (article is fixed server text).
- Final whole-feature Opus review requested for the permission sweep and the stall scheduler.

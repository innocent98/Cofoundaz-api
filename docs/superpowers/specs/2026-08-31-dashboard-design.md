# Design — Module 02 Founder Dashboard (v1)

> **Status:** Approved (brainstorm) · **Date:** 2026-08-31 · **Repo:** `cofoundaz-api`
> **Sources of truth:** `../Cofoundaz_Technical_PRD.md` (Module 02 — Founder Dashboard), the UI
> handoff `../cofoundaz/app/(dashboard)/dashboard/page.tsx`, and the shipped modules it aggregates —
> Health Score (06), Roadmap (05), Today's Mission (04), Assessment (07).
>
> First module built after the parallel Roadmap-Slice-3 + Today's-Mission pair. Built in the
> `feat/dashboard` branch, **migration `0010`** (head is `0009_mission`). Single branch — no
> migration-sibling coordination this time.

---

## 1. Scope

Module 02 is the founder's home screen: a single aggregation call that assembles what every other
module already computes, plus a durable team-activity feed. It is a **read-mostly aggregation BFF**
(`dashboard-service`) — it introduces no new business logic, only composition and one new write
(`write_activity`).

**In scope**

- `GET /dashboard/summary` — one BFF call composing greeting, Health card, Today's Mission widget,
  Upcoming (next 7 days), a derivable KPI strip, and the calibration-banner state.
- A durable, workspace-scoped **activity feed** — `activity_log` table + `write_activity()` helper +
  `GET /dashboard/activity` (cursor-paginated).
- Per-section resilience in `summary` (the PRD's *"each card errors independently"*).

**Aggregated from shipped modules (read-only):**

| Widget | Source | Call |
|---|---|---|
| Startup Health card | Module 06 | `health_score.get_overview(db, startup)` |
| Today's Mission widget | Module 04 | `mission.get_or_generate_today` + `streak` + `serialize_mission` |
| Upcoming (next 7 days) | Module 05 | roadmap milestones with `due_on ∈ [today, today+7]`, `status != done` |
| KPI: tasks done this week | Module 04 | `mission_tasks` completed in the last 7 days |
| Calibration state | Module 07 | whether a completed assessment result exists |

**Deferred — surfaced as the PRD's own honest empty-states (their copy provides the strings):**

| Deferred | To | v1 behaviour |
|---|---|---|
| AI Briefing (`GET /dashboard/briefing/today`, action-accept) | Module 03 (AI Co-Founder) | `summary.briefing` returns the empty-state marker; the two briefing endpoints are **not built** in v1 |
| Risks · Opportunities | Module 03 | `summary.risks` / `summary.opportunities` return empty-state markers |
| Financial/sales KPIs (revenue, runway, pipeline, campaign perf) | Modules 09–11 (Finance/Sales/Marketing) | present in the `kpis` shape as `null` values |
| Realtime `workspace.{id}.activity` | Module 20 (delivery infra) | v1 is poll-on-read |
| Widget-level role/grant filtering (BC/M "shared widgets only") | later pass | v1: all active members see the same aggregation |
| `kpi_snapshots` / `briefings` tables + nightly cron | Modules 03 / 09–11 / 20 | not created — building tables ahead of their real consumers risks the wrong shape |

## 2. Decisions (locked in brainstorming)

| # | Decision | Choice |
|---|---|---|
| 1 | v1 scope line | **Aggregate-what-exists + a real activity feed.** Compose the shipped modules; render AI/financial widgets as honest empty-states. Fully unblocked — no AI provider, no unbuilt module needed. |
| 2 | Activity feed source | **Durable `activity_log` + `write_activity()` helper** (mirrors the existing `write_audit` pattern). Not the auth-only `audit_log` (wrong concern); not derive-on-read (heterogeneous, hard to page); not an event-bus pub/sub upgrade (that delivery infra is Module 20's job, and current payloads lack a uniform actor/title). |
| 3 | Activity row shape | **Denormalized `summary` string rendered at write time**, alongside structured `action`/`entity_type`/`entity_id` for deep-linking. The feed read needs no per-row entity lookups (no N+1) and the text survives later renames of the entity. |
| 4 | Aggregation resilience | **Each `summary` section composed under its own try/except** → a failing section returns a `{ "error": true }` marker for that card, never a 500 for the whole call. |

## 3. Data model — migration `0010_dashboard`

**New table `activity_log`** (UUIDMixin + TimestampMixin):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `startup_id` | UUID FK→startups | `ondelete=CASCADE`, indexed |
| `actor_user_id` | UUID FK→users, nullable | `null` = system-generated (e.g. auto-generated mission) |
| `action` | String(80) | dotted verb, e.g. `mission.task.completed`, `roadmap.milestone.completed`, `assessment.completed`, `member.joined` |
| `entity_type` | String(40), nullable | e.g. `mission_task`, `roadmap_milestone`, `assessment`, `membership` |
| `entity_id` | UUID, nullable | the entity the FE deep-links to; **no FK** (soft, cross-module, mirrors `mission_tasks.roadmap_task_id`) |
| `summary` | String(300) | human-readable, rendered at write time (e.g. `"Aisha completed 'Draft pricing options'"`) |
| `meta` | JSONB, nullable | small structured extras (e.g. `{"streak": 5}`) |
| (TimestampMixin) | | `created_at` orders the feed |

Indexes: `ix_activity_log_startup_created (startup_id, created_at DESC, id)` — powers keyset pagination.

No other tables. No changes to any existing table.

## 4. `write_activity()` helper — `app/platform/activity.py`

Sibling of `write_audit` (`app/platform/audit.py`), same call shape and semantics:

```python
def write_activity(
    db: Session, *,
    startup_id: uuid.UUID,
    action: str,
    summary: str,
    actor_user_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    meta: dict | None = None,
) -> ActivityLog: ...   # db.add(row); db.flush(); return row
```

Written in the **same transaction** as the action that triggers it, so a rolled-back action leaves
no phantom activity and a committed one always records. Called at the ~6–8 **meaningful team-activity**
verbs (not every mutation), wherever the actor and the committed entity are both in hand — the
**endpoint layer** in most cases (actor comes from `get_verified_user`), keeping service logic
untouched:

| Action site | `action` | actor |
|---|---|---|
| mission task → complete | `mission.task.completed` | acting user |
| mission task → snooze | `mission.task.snoozed` | acting user |
| mission task → reject | `mission.task.rejected` | acting user |
| mission custom task added | `mission.task.added` | acting user |
| roadmap milestone → done | `roadmap.milestone.completed` | acting user |
| roadmap re-plan applied | `roadmap.replanned` | acting user (`apply_replan` already takes `actor`) |
| assessment completed | `assessment.completed` | acting user |
| member joined (invite accepted) | `member.joined` | the joiner |

`summary` is composed at the call site from data already loaded (entity title + actor first name).
The set is intentionally tight — the feed is *"things worth telling the team"*, not an audit trail
(that is `audit_log`'s job, unchanged).

## 5. Aggregation service — `app/services/dashboard/service.py`

Two entrypoints, both read-only over the workspace:

`get_summary(db, startup, user) -> dict` composes:

- `greeting` — `{ "salutation": "Good morning|afternoon|evening", "first_name": …, "startup_name": … }`
  (time-of-day from server clock; workspace-tz deferred, consistent with Roadmap/Mission).
- `health` — `health_score.get_overview(...)` (or its documented no-assessment shape).
- `mission` — `serialize_mission(get_or_generate_today, streak)` or `null` when no roadmap.
- `upcoming` — milestones `due_on ∈ [today, today+7]` and `status != done`, each
  `{ id, title, due_on, milestone_id }`, ascending by `due_on`.
- `kpis` — `{ "tasks_done_this_week": <int>, "revenue": null, "runway": null, "pipeline_value": null,
  "campaign_performance": null }` (only the first is computable in v1; the rest are declared-but-null
  so the FE can render the strip and its "coming soon" states).
- `calibration` — `{ "assessment_complete": <bool> }` (drives the first-run banner from 01.6).
- `briefing` / `risks` / `opportunities` — empty-state markers
  (`{ "status": "empty", "message": <PRD copy> }`).

**Each of `health`, `mission`, `upcoming`, `kpis` is composed inside its own `try/except`**; on error
that key becomes `{ "error": true }` and the rest of the payload is unaffected. The greeting and the
empty-state markers cannot fail.

`get_activity(db, startup, cursor, limit=20) -> dict` — keyset pagination over `activity_log`
ordered `(created_at DESC, id DESC)`; `cursor` encodes the last `(created_at, id)` seen; returns
`{ "items": [ { id, action, entity_type, entity_id, summary, meta, actor, created_at } ], "next_cursor": … | null }`
where `actor` is `{ "id": …, "name": … } | null` resolved by a **single `LEFT JOIN users`** on the
paginated query (not a per-row lookup — no N+1; `null` for system-generated rows). The `summary`
string already embeds the actor's name, so `actor` is a convenience for avatar/linking only.
`limit` clamped to `1..50` (default 20).

## 6. Endpoints (`/api/v1/dashboard`)

All verified. **Reads = any active member** (founder / team_member / mentor); mentor sees the same
aggregation in v1 (widget-level grant filtering deferred).

| Route | Access | Behaviour |
|---|---|---|
| `GET /dashboard/summary` | member | The composed payload from `get_summary`. Never 500s on a single-section failure. |
| `GET /dashboard/activity?cursor=&limit=` | member | Cursor-paginated activity feed. Empty workspace → `{ items: [], next_cursor: null }`. |

Cross-tenant / no-workspace → uniform `403`/`404` via `require_workspace` (no path-resolved
resource, so 403 for a non-member — consistent with `/missions/today`). Reuses the `AppError`
taxonomy — **no new error codes**.

## 7. Events, errors, config

- **Events:** the dashboard **emits none** in v1 (it is a read/aggregation module). The one new
  write is `write_activity`, which is a durable row, not an event.
- **Errors:** `NOT_FOUND` (no roadmap for the upcoming/mission sections is a normal empty, not an
  error), `FORBIDDEN` (non-member), `EMAIL_NOT_VERIFIED` (shared guard). Per-section failures inside
  `summary` are swallowed into that section's `{ "error": true }` marker, logged, not raised.
- **Config:** none new. Upcoming window = 7 days (named constant `UPCOMING_WINDOW_DAYS = 7` in the
  service). `tasks_done_this_week` window = rolling last 7 days ending today (same convention as
  Mission's weekly %).

## 8. Testing

- **TDD**, real Postgres + per-test rollback; add `create_activity` factory, reuse the module
  factories (mission, roadmap, assessment, health).
- **`write_activity`:** persists the row in the caller's transaction; `actor_user_id` nullable;
  `summary`/`meta` round-trip.
- **`get_summary`:** every section present; assessment-not-done → `calibration.assessment_complete
  == false`; no-roadmap → `mission == null` and `upcoming == []`; a monkeypatched section raising →
  that key is `{ "error": true }` and the others still populate (resilience); `tasks_done_this_week`
  counts only done tasks in the window; `upcoming` respects the `[today, today+7]` window and
  excludes done milestones.
- **`get_activity`:** newest-first; keyset pagination returns disjoint pages and a stable
  `next_cursor`; empty workspace → empty list, `null` cursor; `limit` clamp.
- **Wiring:** each of the ~6–8 action sites writes exactly one `activity_log` row with the right
  `action`/`actor`/`summary` (e.g. completing a mission task creates a `mission.task.completed` row
  attributed to the caller).
- **Tenancy/access:** member reads; cross-workspace `403`; mentor read OK; verified gate; activity
  rows never leak across `startup_id`.
- **Live E2E** (`e2e/test_dashboard.py`): onboard → assessment → roadmap generates → mission →
  `GET /dashboard/summary` (assert health + mission + upcoming populated; briefing/risks/opps carry
  empty-state markers; `calibration.assessment_complete == true`) → complete a mission task →
  `GET /dashboard/activity` shows the `mission.task.completed` row. Capture bodies to
  `e2e/_captures/dashboard/`.
- **FE integration guide** (`docs/fe-integration-guide-dashboard.md`) from live captures — the full
  `summary` shape (including which fields are null/empty-state in v1 and **why**, so the FE builds
  the real widgets now and lights them up as Modules 03/09–11 ship), the activity feed + its cursor
  contract, and a verification table.

## 9. Plan shape

One implementation plan (`writing-plans`), ~7 TDD tasks, subagent-driven in the `feat/dashboard`
branch:

1. `activity_log` model + migration `0010_dashboard` + `write_activity` helper + `create_activity` factory
2. Wire `write_activity` into the ~6–8 action sites (additive edits to shipped mission/roadmap/assessment/onboarding endpoints)
3. `dashboard` aggregation service — `get_summary` composition + per-section resilience + `UPCOMING_WINDOW_DAYS`
4. `GET /dashboard/summary`
5. `get_activity` (keyset pagination) + `GET /dashboard/activity`
6. Live E2E + smoke surface
7. SOP + FE integration guide + checklist reconcile

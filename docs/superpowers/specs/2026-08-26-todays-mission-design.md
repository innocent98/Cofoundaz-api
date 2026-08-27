# Design — Module 04 Today's Mission

> **Status:** Approved (brainstorm) · **Date:** 2026-08-26 · **Repo:** `cofoundaz-api`
> **Sources of truth:** `../Cofoundaz_Technical_PRD.md` (Module 04), the UI handoff
> `../cofoundaz/app/(dashboard)/mission/*`, and the shipped Roadmap (Module 05, Slices 1–2).
>
> **Parallel build note:** built in the `feat/todays-mission` worktree, **migration `0009`**
> (Roadmap Slice 3 owns `0008`). **Read-only against the roadmap** — this module reads roadmap
> tasks/milestones to generate a mission but must not edit any `app/services/roadmap/*` or
> `app/api/v1/endpoints/roadmap.py` file.

---

## 1. Scope

**In scope** — a daily 1–3 task mission generated from the founder's roadmap, task completion +
snooze/reorder/reject, custom tasks, a derived streak, mission history, and mission settings.

**Deferred:**

| Deferred | To |
|---|---|
| 06:00 cron generation + push notification | Module 20 (scheduler + notifications) — v1 generates **lazily on read** |
| AI-authored "why" reason line | Module 03 — v1 uses templated reasons |
| Real event delivery (`mission.*`) | Module 20 (emitted now, no consumer) |

## 2. Decisions (locked in brainstorming)

| # | Decision | Choice |
|---|---|---|
| 1 | Generation | **Inline + lazy-on-read.** `GET /missions/today` generates today's mission if none exists (no cron/worker). Deterministic selection of incomplete roadmap tasks. |
| 2 | Roadmap link | **Soft, unconstrained reference.** `mission_tasks.roadmap_task_id` is a nullable UUID with **no FK** to `roadmap_tasks` (like `jobs.startup_id`), keeping this module decoupled from the roadmap tables. |
| 3 | Streak | **Derived, not stored** — consecutive days ending today/yesterday with a completed mission. |
| 4 | Reason line | **Templated** v1; AI-authored rationale deferred to Module 03. |

## 3. Data model (migration `0009_mission`)

Three tables; two new enums (`app/db/models/enums.py`):
`MissionStatus(pending | complete)`, `MissionTaskStatus(todo | done | snoozed | rejected)`.
Reuse the existing `TaskEffort(small | medium | large)` for `mission_tasks.effort`.

**`missions`** (UUIDMixin + TimestampMixin)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `startup_id` | UUID FK→startups | CASCADE, indexed |
| `mission_date` | Date | |
| `generated_by` | String(10) | `"system"` (auto) or `"user"` |
| `status` | `MissionStatus` enum | default `pending` |
| unique | | `(startup_id, mission_date)` — one mission per workspace per day |

**`mission_tasks`** (UUIDMixin + TimestampMixin)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `mission_id` | UUID FK→missions | CASCADE, indexed |
| `roadmap_task_id` | UUID? | **no FK** — soft link for the "milestone chip"; null for custom tasks |
| `title` | String | |
| `reason` | String? | templated why-line |
| `effort` | `TaskEffort` enum | default `medium` |
| `order` | Integer | |
| `status` | `MissionTaskStatus` enum | default `todo` |
| `completed_at` | timestamptz? | set on complete |
| `reject_reason` | String? | set on reject ("Already done"/"Wrong priority"/"Doesn't apply") |

**`mission_settings`**
| Column | Type | Notes |
|---|---|---|
| `startup_id` | UUID **PK** FK→startups | CASCADE — one row per workspace |
| `mission_size` | Integer | 1–3, default **3** |
| `delivery_time` | Time | default `06:00` (metadata for the future cron) |
| `weekend_missions` | Boolean | default **false** ("weekends off") |
| (TimestampMixin) | | |

## 4. Generation — `app/services/mission/service.py`

`get_or_generate_today(db, startup) -> Mission | None` (lazy):
1. If a `missions` row exists for `(startup_id, today)`, return it.
2. If the startup has **no roadmap**, return `None` (the API renders the empty-state
   *"Your mission comes from your roadmap"*).
3. Else **select tasks** (read-only over roadmap): incomplete `roadmap_tasks` (`status != done`)
   ordered by `(milestone.due_on asc nulls last, phase.order, milestone.order, task.order)`;
   take the first `mission_settings.mission_size` (default 3). **Carry forward** any `snoozed`
   tasks from the most recent prior mission first (they take priority), then fill to size.
4. Create the `missions` row (`generated_by="system"`, `status=pending`) + one `mission_task`
   per selected task: `roadmap_task_id`, `title` (from the roadmap task), `effort`, `order`, a
   templated `reason` (e.g. *"From your '{milestone title}' milestone."* or *"Due {relative}."*).
5. Return the mission.

`weekend_missions=false` + today is Sat/Sun → generate an **empty** mission (status pending, no
tasks) so the FE shows "Weekends off" rather than pulling roadmap work.

`streak(db, startup) -> int`: count consecutive days ending today (or yesterday if today's mission
is not yet complete) whose `missions.status == complete`. Derived on read.

## 5. Endpoints (`/api/v1/missions`)

All verified. **Reads = any member; writes = editor** (founder/team_member); mentor read-only.

| Route | Access | Behaviour |
|---|---|---|
| `GET /missions/today` | member | Lazy-generate + return `{ mission_date, status, streak, tasks:[…] }`, or `{ status:"no_roadmap" }` empty-state. |
| `POST /missions/tasks` | editor | Add a custom task to today's mission (`roadmap_task_id=null`, appended order). |
| `PATCH /missions/tasks/{id}` | editor | `{action: "complete" | "snooze" | "reorder" | "reject", order?, reject_reason?}`. |
| `GET /missions/history` | member | Past missions (reverse-chron): date, completed/total, status; weekly completion %. |
| `GET /missions/settings` | member | The workspace's mission settings (lazily created with defaults). |
| `PATCH /missions/settings` | editor | Update `mission_size` (1–3) / `delivery_time` / `weekend_missions`. |

**`PATCH /missions/tasks/{id}` actions:**
- `complete` → `status=done`, `completed_at=now`; emit `mission.task.completed`. If **all** of the
  mission's tasks are now `done` (ignoring `rejected`), set `missions.status=complete`, emit
  `mission.completed`, then if the new `streak` crosses **7/30/100** emit `mission.streak.milestone`.
- `snooze` → `status=snoozed` (tomorrow's generation re-includes it first). Idempotent.
- `reorder` → set `order`.
- `reject` → `status=rejected`, `reject_reason` (one of the allowed chips).

**GET `/missions/today` shape** (FE contract, verified live later):
```json
{ "data": { "mission_date":"2026-08-26", "status":"pending", "streak":3,
  "tasks":[ { "id":"…","title":"…","reason":"From your 'Validate demand' milestone.",
    "effort":"medium","status":"todo","order":0,"roadmap_task_id":"…"|null,
    "completed_at":null } ] } }
```

## 6. Events, errors

**Events (via `event_bus.publish`, fire-and-forget — Module 20 consumes):**

| Event | When | Payload |
|---|---|---|
| `mission.task.completed` | a task → `done` | `{startup_id, mission_id, task_id}` |
| `mission.completed` | all tasks done | `{startup_id, mission_id, mission_date}` |
| `mission.streak.milestone` | streak crosses 7/30/100 on completion | `{startup_id, streak}` |

**Errors (reuse `AppError` taxonomy — no new codes):** `NOT_FOUND` (cross-tenant/unknown mission
task), `VALIDATION_ERROR` (bad `action`, `mission_size` out of 1–3, bad `reject_reason`),
`FORBIDDEN` (mentor write), `EMAIL_NOT_VERIFIED`. Uniform-404 cross-workspace.

## 7. Testing

- **TDD**, real Postgres + per-test rollback; factories `create_mission`, `create_mission_task`,
  `create_mission_settings`.
- **Generation:** picks the right N incomplete roadmap tasks in the right order; respects
  `mission_size`; carries forward snoozed tasks first; no-roadmap → `None`; idempotent (second call
  same day returns the same mission); weekend-off → empty mission.
- **Streak:** consecutive completed days; broken by a gap; today-incomplete counts from yesterday.
- **Actions:** complete → event + `completed_at`; all-done → `mission.completed` + streak-milestone
  at 7/30/100; snooze/reorder/reject transitions; custom task append.
- **Settings:** defaults lazily created; `mission_size` clamp 1–3 (`422` outside).
- **Tenancy/access:** cross-workspace `404`; mentor write `403`; verified gate.
- **Live E2E** (`e2e/test_mission.py`): onboard → roadmap generates → `GET /missions/today`
  returns 1–3 tasks drawn from the roadmap → complete them → `mission.completed` + streak → history
  reflects it. Captured to `e2e/_captures/mission/`.
- **FE integration guide** (`docs/fe-integration-guide-mission.md`) from live captures.

## 8. Plan shape

One plan, ~7 TDD tasks, subagent-driven in the `feat/todays-mission` worktree:

1. Enums (`MissionStatus`, `MissionTaskStatus`) + 3 models + migration `0009` + factories
2. Generation service (`get_or_generate_today`, read-only roadmap selection + templated reasons) + streak
3. `GET /missions/today` (lazy-gen + empty-state + streak) + `GET /missions/settings` + `PATCH /missions/settings`
4. `POST /missions/tasks` + `PATCH /missions/tasks/{id}` (complete/snooze/reorder/reject + events)
5. `GET /missions/history`
6. Live E2E + smoke surface
7. SOP + FE integration guide + checklist reconcile

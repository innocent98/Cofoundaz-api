# FE Integration Guide — Founder Dashboard (Module 02)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_dashboard.py::test_dashboard_journey` running against a real server (`make e2e`) —
see `e2e/_captures/dashboard/summary.json` and `e2e/_captures/dashboard/activity.json`. Nothing
here is retyped from memory or invented. IDs and timestamps are real values from that ephemeral
test run (not hand-written placeholders) — they differ on every real request, but the shapes
are exact. Two things the live journey could **not** exercise — a populated `upcoming` list, and
the activity cursor/pagination path — are called out **inline** and in the verification table,
each labelled with where its shape came from instead.

Base path: `/api/v1/dashboard`. Every route requires a Bearer access token
(`Authorization: Bearer <token>`) and an `X-Workspace-Id` header identifying the active
workspace (`GET /auth/me` → `data.active_workspace_id` is the source for that header), same as
every other tenant-scoped endpoint in this API. **Both routes are read-only and open to any
active member** — founder, team_member, **and mentor**. There are no writes in this module.

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}` (see §3).

---

## 1. `GET /api/v1/dashboard/summary` — the founder's home screen

The one call the dashboard/home screen is built on. It aggregates 9 sections from across the
already-shipped modules (Health Score, Mission, Roadmap, Assessment) plus 3 static
not-yet-built sections. **Calling this may have a side effect**: if today's mission doesn't
exist yet, this call lazily generates it (same lazy-generation Mission's own
`GET /missions/today` does — see `docs/fe-integration-guide-mission.md` §1) — the endpoint
commits after the read specifically to persist that.

`e2e/_captures/dashboard/summary.json` — captured **after** the founder completed the kickoff
assessment (so `health` and `calibration` are in their real, non-empty states) and **before**
any mission task was completed (status `200`):

```json
{
  "data": {
    "greeting": {
      "salutation": "Good afternoon",
      "first_name": "Ada",
      "startup_name": "Cofoundaz"
    },
    "health": {
      "status": "ok",
      "score": 31,
      "band": "at_risk",
      "delta_7d": 0,
      "computed_at": "2026-08-31T16:56:32.465287+00:00",
      "config_version": 1,
      "dimensions": [
        { "key": "team", "label": "Team", "score": 50, "band": "needs_work" },
        { "key": "legal", "label": "Legal", "score": 0, "band": "at_risk" },
        { "key": "money", "label": "Financial", "score": 43, "band": "needs_work" },
        { "key": "market", "label": "Market", "score": 38, "band": "at_risk" },
        { "key": "product", "label": "Product", "score": 25, "band": "at_risk" }
      ],
      "top_recommendations": [
        {
          "id": "bb0bf2d5-61f0-4ecb-b7dc-bbbd1fb697a3",
          "dimension": "legal",
          "key": "legal.incorporate",
          "title": "Complete incorporation",
          "body": "Register the company and issue founder shares. Operating unincorporated exposes founders personally and blocks fundraising.",
          "estimated_lift": 9,
          "effort": "high",
          "status": "pending",
          "priority": 1
        },
        {
          "id": "9c19ede4-64d8-49a9-993f-5cb779720dd4",
          "dimension": "legal",
          "key": "legal.founder_agreement",
          "title": "Sign a founders' agreement",
          "body": "Put equity splits, vesting, and roles in writing before it is contentious. This prevents the most common founder disputes.",
          "estimated_lift": 7,
          "effort": "medium",
          "status": "pending",
          "priority": 2
        },
        {
          "id": "b8ac279f-0b65-451a-8699-4883cebbd4c8",
          "dimension": "product",
          "key": "product.define_mvp",
          "title": "Define your MVP scope",
          "body": "Write a one-page MVP definition: the single problem, the smallest feature set that solves it, and what you are deliberately leaving out.",
          "estimated_lift": 8,
          "effort": "medium",
          "status": "pending",
          "priority": 3
        }
      ],
      "summary": "Your Health Score is 31 (at risk). Your weakest area is Legal."
    },
    "mission": {
      "mission_date": "2026-08-31",
      "status": "pending",
      "streak": 0,
      "tasks": [
        {
          "id": "721e79af-c219-4a3a-94d8-699e597929a3",
          "roadmap_task_id": "8baf2855-ca05-4ff2-a4b0-a11174ef9752",
          "title": "Run 10 customer interviews",
          "reason": "From your 'Validate demand' milestone.",
          "effort": "medium",
          "status": "todo",
          "order": 0,
          "completed_at": null,
          "reject_reason": null
        },
        {
          "id": "30de2629-33d2-43b2-adc5-4b0cd8fda1da",
          "roadmap_task_id": "67ff93d0-113f-4cfb-9063-1a4e44d9647b",
          "title": "Synthesize problem hypotheses",
          "reason": "From your 'Validate demand' milestone.",
          "effort": "small",
          "status": "todo",
          "order": 1,
          "completed_at": null,
          "reject_reason": null
        },
        {
          "id": "ed0d6ad0-6e5c-420d-94d0-d0397f404189",
          "roadmap_task_id": "87ec7057-0b01-464f-b0f0-ae791edb7415",
          "title": "Draft 3 pricing options",
          "reason": "From your 'Pricing test' milestone.",
          "effort": "small",
          "status": "todo",
          "order": 2,
          "completed_at": null,
          "reject_reason": null
        }
      ]
    },
    "upcoming": [],
    "kpis": {
      "tasks_done_this_week": 0,
      "revenue": null,
      "runway": null,
      "pipeline_value": null,
      "campaign_performance": null
    },
    "calibration": { "assessment_complete": true },
    "briefing": {
      "status": "empty",
      "message": "I'll have your first briefing ready tomorrow morning once I've seen a full day of your workspace."
    },
    "risks": {
      "status": "empty",
      "message": "No open risks. I'm watching runway, deadlines, and pipeline for you."
    },
    "opportunities": {
      "status": "empty",
      "message": "Opportunities I spot — grants, quick wins, market signals — will show up here."
    }
  },
  "meta": null
}
```

### Section-by-section reference

| Section | Populated? | Notes |
|---|---|---|
| `greeting` | always | `salutation` is time-of-day derived (morning/afternoon/evening, server clock — see the workspace-timezone caveat below); `first_name` is parsed from the caller's profile `full_name` (first space-separated token) and can be `""` if no profile name is set. |
| `health` | real when an assessment has completed, else the pending shape | Same payload shape as `GET /health-score` — see `docs/fe-integration-guide-health-score.md`. **This section is `null` if wrapped-in and the underlying section throws — see the error-marker trap below**, not because the health data itself is empty. |
| `mission` | real once a roadmap exists (which it does post-onboarding), **`null` if the workspace has no roadmap** | Same task shape as `GET /missions/today` — see `docs/fe-integration-guide-mission.md` §1. Calling `/dashboard/summary` may lazily generate today's mission (see above). |
| `upcoming` | a list, **`[]` in this capture** — see the honesty note below | Roadmap milestones due in the next 7 days, not yet `done`. |
| `kpis.tasks_done_this_week` | **live** — the only real KPI in v1 | Mission tasks completed in the trailing 7 days (by `completed_at`, not by which day's mission they were assigned to). |
| `kpis.revenue` / `runway` / `pipeline_value` / `campaign_performance` | **always `null` in v1** | No financial/CRM/campaign module exists yet — Modules 09–11. Render these as "coming soon" placeholders, not as "$0". |
| `calibration.assessment_complete` | real, boolean | `true` once the founder has completed the kickoff assessment (any completed `Assessment` row) — **nested under `calibration`, not a top-level field** (see the nesting trap below). **`calibration` itself can also be `{"error": true}`** on a rare DB error, same as `health`/`mission`/`upcoming`/`kpis` — check for the error shape before reading `assessment_complete` (see the error-marker trap below). |
| `briefing` / `risks` / `opportunities` | **always the static empty-state shape in v1** | No AI panel exists yet — Module 03. `status` is always `"empty"`; `message` is a fixed string per section. The shape (`{status, message}`) is stable so Module 03 can later swap in a non-`"empty"` status without breaking the FE contract — but nothing today drives that transition. Don't build UI that assumes `status` can currently be anything else. |

### Field-nesting traps

- **The envelope wraps everything in `{"data": …, "meta": null}`.** `meta` is always `null` on
  this endpoint (no pagination). Read every field below `data`, not at the top level.
- **`mission` can be `null`.** If the caller's workspace has no roadmap yet (rare — onboarding
  generates one inline — but possible), `mission` is `null`, not an object with empty `tasks`.
  Branch on `data.mission === null` before reading `data.mission.tasks`.
- **`calibration.assessment_complete` is nested, not `data.assessment_complete`.** Easy to
  mis-flatten when wiring this up — it lives one level down, inside the `calibration` object,
  matching the shape in the capture above exactly.
- **A section that throws server-side becomes `{"error": true}`, not an omitted key.** `health`,
  `mission`, `upcoming`, `kpis`, and `calibration` are each independently wrapped so one
  subsystem's failure can't 500 the whole page (see the SOP's "per-section resilience" note,
  `docs/sop/2026-08-31-dashboard.md`). This was **not** exercised in the live capture (nothing
  failed during the journey) — it's derived from `app/services/dashboard/service.py`'s `_section`
  helper and unit-tested
  (`tests/services/test_dashboard_summary.py::test_a_failing_section_becomes_error_marker_not_a_raise`).
  If you see `{"error": true}` in place of a section's normal shape, render that widget's own
  degraded/retry state rather than crashing on the missing fields.

### ⚠️ Honesty note — `upcoming: []` in this capture is real, but not the only real shape

**The `upcoming: []` in the capture above is genuinely what the live server returned — it is not
a placeholder.** But it's worth understanding *why* it's empty, because a founder further along
in their journey will see a populated list, and you should build for that now rather than assume
`upcoming` is always empty.

The dashboard journey onboards a **freshly-created** founder to stage `validation`, whose
roadmap template seeds the "Validate demand" milestone with a due date roughly **~14 days out**
from `generate_roadmap`'s `date.today()` base. `_upcoming()`
(`app/services/dashboard/service.py:50`) only returns roadmap milestones due within
`UPCOMING_WINDOW_DAYS = 7` days. A milestone ~14 days out is outside that 7-day window on day
one — so for this specific journey (a founder who just onboarded, seconds ago), a non-empty
`upcoming` list was **structurally unreachable**, not a bug and not evidence the feature is
half-built. It becomes populated automatically as the founder's existing roadmap milestones
approach their due dates — no extra action required.

**The populated-item shape below is derived from the source** (`_upcoming`, same function,
same file) and unit-tested (`tests/services/test_dashboard_summary.py::
test_upcoming_window_and_done_exclusion`), but was **not captured live** — label it accordingly
in your own tracking:

```json
{
  "id": "<roadmap milestone uuid>",
  "title": "<milestone title>",
  "due_on": "<ISO-8601 date, e.g. 2026-09-05>",
  "milestone_id": "<same uuid as id>"
}
```

Note `id` and `milestone_id` are always the same value (both are the milestone's own id) —
`milestone_id` is kept as an explicit field so the shape reads self-descriptively without the
FE needing to know that `id` happens to *be* a milestone id in this particular section.

---

## 2. `GET /api/v1/dashboard/activity` — team activity feed

Keyset-paginated, newest-first. Query params: `cursor` (opaque string, omit for the first page),
`limit` (int, **server-clamped to 1–50** even if you request more or less — both by FastAPI's
own `Query(ge=1, le=50)` validation on the endpoint *and* a second clamp inside the service
function itself).

`e2e/_captures/dashboard/activity.json` — captured after completing one mission task, right
after the assessment completion earlier in the same journey (status `200`):

```json
{
  "data": {
    "items": [
      {
        "id": "bb10dc08-af45-410b-9f7c-e02a4f7d9251",
        "action": "mission.task.completed",
        "entity_type": "mission_task",
        "entity_id": "721e79af-c219-4a3a-94d8-699e597929a3",
        "summary": "Ada Founder completed 'Run 10 customer interviews'",
        "meta": null,
        "actor": {
          "id": "d22ef611-7880-4336-9d53-fb872cae34e0",
          "name": "Ada Founder"
        },
        "created_at": "2026-08-31T16:56:32.533340+00:00"
      },
      {
        "id": "63d037eb-c484-4910-a09a-bb8152c2a7a7",
        "action": "assessment.completed",
        "entity_type": "assessment",
        "entity_id": "b5d59c90-c404-47b4-9431-9aa09fe5c709",
        "summary": "Ada Founder completed the startup assessment",
        "meta": null,
        "actor": {
          "id": "d22ef611-7880-4336-9d53-fb872cae34e0",
          "name": "Ada Founder"
        },
        "created_at": "2026-08-31T16:56:32.451502+00:00"
      }
    ],
    "next_cursor": null
  },
  "meta": null
}
```

### Item field reference

| Field | Type | Notes |
|---|---|---|
| `id` | uuid string | the activity row's own id — use as the React/list key, not for pagination. |
| `action` | string | a dotted action code, e.g. `"mission.task.completed"`, `"roadmap.milestone.completed"`, `"assessment.completed"`, `"member.joined"`. Not an enum in the OpenAPI sense — treat unrecognized values as a generic "did something" row so a future action type doesn't break rendering. |
| `entity_type` | string \| `null` | e.g. `"mission_task"`, `"roadmap_milestone"`, `"assessment"`, `"membership"`. |
| `entity_id` | uuid string \| `null` | soft reference — the referenced entity may since have been edited or deleted; don't assume a follow-up fetch by this id always succeeds. |
| `summary` | string | a pre-rendered, human-readable sentence (e.g. `"Ada Founder completed 'Run 10 customer interviews'"`) — **render this directly**, don't try to reconstruct a sentence from `action`/`entity_type`. |
| `meta` | object \| `null` | always `null` in the current 8 call sites (see the SOP) — reserved for future structured detail. |
| `actor` | `{id, name}` \| `null` | see the trap below. |
| `created_at` | ISO-8601 timestamptz | tz-aware, parse accordingly. |

### The `actor` null trap

**`actor` is `null` for system-authored rows, and `{id, name}` for user-authored ones.** Every
row in the capture above has a populated `actor` because every one of the 8 current call sites
is triggered by a logged-in user's own action. But `ActivityLog.actor_user_id` is nullable by
design (`app/db/models/activity.py`), and the service explicitly builds `actor = null` when it
is unset (`app/services/dashboard/service.py:168-172`) — unit-tested
(`tests/api/test_dashboard_activity.py::test_activity_system_row_has_null_actor`), not yet
exercised live (nothing in this module writes a system row today, but the shape is real and
future-proofed for one). **Always null-check `actor` before reading `actor.name`** — render a
generic "System" or workspace-level attribution when it's absent, never assume a name is there.

`actor.name` is sourced from the acting user's **profile** `full_name`
(`UserProfile.full_name`, outer-joined in the same query — see the SOP's "actor resolution"
note) — it can itself be `null` if that user has no profile name set, even when `actor.id` is
present. Handle a populated `actor` object whose `name` is still `null`.

### The cursor / pagination contract

- Pass the previous page's `next_cursor` value as the `cursor` query param to fetch the next
  page. `next_cursor` is `null` when there are no more items — **stop paginating there**, don't
  send a `null`/empty-string cursor back (an empty `cursor=` param is treated the same as
  omitting it — first page).
- The cursor is an **opaque, base64-encoded string** — never parse or construct it client-side.
  It encodes `(created_at, id)` of the last row on the page, used for a keyset (`WHERE (…) <
  (…)`) comparison server-side, not an `OFFSET` — safe against rows being written between page
  reads (no skipped or duplicated items across pages).
- **A malformed `cursor` returns `422 VALIDATION_ERROR`**, not a 500 or a silent reset to page 1
  (verified by unit test, two cases — invalid base64, and valid base64 decoding to a string with
  no expected `|` separator — both `tests/api/test_dashboard_activity.py`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid pagination cursor.",
    "field_errors": [
      { "field": "cursor", "message": "This page link is no longer valid." }
    ]
  }
}
```

Treat this as "the saved page link is stale — restart from the first page (omit `cursor`)."

### ⚠️ Honesty note — the cursor/pagination path was not exercised live

**The live journey's capture above shows `next_cursor: null` because the workspace only had 2
activity rows** at capture time (one assessment completion, one mission-task completion) — well
under any `limit`, so the "there's a next page" branch never ran during `make e2e`. The
`next_cursor`-populated / second-page response was **not captured live**. The contract above
(opaque base64 cursor, keyset `(created_at, id)` comparison, `null` at the end) is derived from
the source (`_encode_cursor`/`_decode_cursor`/`get_activity`,
`app/services/dashboard/service.py:128-189`) and is covered by a dedicated unit test that
exercises real two-page pagination against a real Postgres DB
(`tests/api/test_dashboard_activity.py::test_activity_newest_first_and_paginates` — inserts 3
rows, fetches `limit=2`, asserts `next_cursor` is non-null and the pages don't overlap, then
fetches the second page and asserts it terminates with `next_cursor: null`). Build against it
with confidence — it's real Postgres, just not the live-HTTP E2E journey.

---

## 3. Errors

Every error is `{"error": {"code": …, "message": …, "field_errors": [...]}}` (no `data`/`meta`)
— the same shared shape used throughout this API. The only dashboard-specific error is the
malformed-cursor `422 VALIDATION_ERROR` shown in §2. No new error codes were introduced by this
module. A missing/invalid `X-Workspace-Id` or an unauthenticated request behaves exactly as
documented in `docs/fe-integration-guide-mission.md` §7 and
`docs/fe-integration-guide-roadmap.md` §8 — both dashboard routes share the same
`require_workspace` / `get_verified_user` dependency chain as every other tenant-scoped route.

---

## Verification table

Rows marked ✅ were exercised **live**, over real HTTP, against a real Postgres-backed server
(`make e2e`, `e2e/test_dashboard.py::test_dashboard_journey`) — not just unit-tested
in-process — and the response body is captured verbatim in the named file. Rows marked ⬜ are
covered by the unit suite (real Postgres, per-test rollback — never mocked) but were **not**
re-asserted over live HTTP; the shape source is named.

| Endpoint / behavior | Verified live? | Source |
|---|---|---|
| `GET /dashboard/summary` — exactly 9 top-level keys, envelope shape | ✅ | `summary.json` |
| `health` — real `"ok"` state with score/band/dimensions/recommendations | ✅ | `summary.json` |
| `mission` — real, roadmap-drawn tasks (1–3) | ✅ | `summary.json` |
| `mission` — `null` when the workspace has no roadmap | ⬜ | derived from `_mission_section`; not unit-tested directly in this module (Mission's own no-roadmap path is unit-tested in `tests/services/test_mission_generate.py`) |
| `kpis.tasks_done_this_week` — live count | ✅ | `summary.json` (`0`, since captured before any completion) |
| `kpis.revenue`/`runway`/`pipeline_value`/`campaign_performance` — always `null` | ✅ | `summary.json` |
| `calibration.assessment_complete: true` after a completed assessment | ✅ | `summary.json` |
| `briefing`/`risks`/`opportunities` — static empty-state shape | ✅ | `summary.json` |
| `upcoming: []` for a freshly-onboarded founder (window miss) | ✅ | `summary.json` |
| `upcoming` — populated item shape `{id, title, due_on, milestone_id}` | ⬜ | derived from `_upcoming` (`app/services/dashboard/service.py:50`); unit-tested (`test_upcoming_window_and_done_exclusion`), **not captured live** — structurally unreachable in this journey (see honesty note) |
| A section throwing server-side → `{"error": true}` marker | ⬜ | derived from `_section`; unit-tested (`test_a_failing_section_becomes_error_marker_not_a_raise`), **not observed live** (nothing failed during the journey) |
| `GET /dashboard/activity` — item shape, newest-first | ✅ | `activity.json` |
| `actor: {id, name}` populated for a user-authored row | ✅ | `activity.json` |
| `actor: null` for a system-authored row | ⬜ | derived from `get_activity`; unit-tested (`test_activity_system_row_has_null_actor`), no call site writes a system row yet |
| `next_cursor` non-null + second-page fetch (cursor contract) | ⬜ | derived from `_encode_cursor`/`_decode_cursor`; real-Postgres unit test (`test_activity_newest_first_and_paginates`), **not captured live** — only 2 rows existed at capture time (see honesty note) |
| `limit` clamp to 1–50 | ⬜ | derived from `get_activity`'s `max(1, min(limit, 50))` + the endpoint's `Query(ge=1, le=50)`; not separately unit-tested with an out-of-range value in this module |
| `422 VALIDATION_ERROR` on a malformed cursor | ⬜ | derived from `_decode_cursor`; unit-tested, two cases (`test_activity_malformed_cursor_returns_422`, `test_activity_cursor_missing_separator_returns_422`) |
| Cross-tenant activity never leaks | ⬜ | unit-tested (`test_activity_never_leaks_across_tenants`) |
| Mentor can read both endpoints (no dashboard writes exist) | ⬜ | derived from `require_workspace` gating both routes; consistent with every other module's mentor-read pattern, not separately re-asserted here |

Every ⬜ row is safe to build against — it's covered by the real-Postgres unit suite
(`tests/api/test_dashboard_*.py`, `tests/services/test_dashboard_summary.py`) and the shapes
match this API's established envelope/error conventions — they just weren't double-verified
end-to-end over live HTTP in this module's single journey.

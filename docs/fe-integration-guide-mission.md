# FE Integration Guide — Today's Mission (Module 04)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_mission.py::test_mission_journey` running against a real server (`make e2e`) — see
`e2e/_captures/mission/*.json`. Nothing here is retyped from memory or invented. IDs and
timestamps in the examples are real values from that ephemeral test run (not hand-written
placeholders) — they differ on every real request, but the shapes are exact. The three states
that the live journey could not exercise (`no_roadmap` empty-state, weekend-off empty mission,
and the 4xx error bodies) are called out **inline**, each labelled with where its shape came from.
The "AI reason line" section (§1, below) is pasted verbatim from a separate live capture,
`e2e/test_mission_reason.py::test_mission_reason_ai` — see `e2e/_captures/mission_reason/*.json`.

Base path: `/api/v1/missions`. Every route requires a Bearer access token
(`Authorization: Bearer <token>`) and an `X-Workspace-Id` header identifying the active workspace
(`GET /auth/me` → `data.active_workspace_id` is the source for that header), same as every other
tenant-scoped endpoint in this API. **Reads** (`GET /today`, `GET /history`, `GET /settings`) are
open to any active member — founder, team_member, **or mentor**. **Writes** (`PATCH /settings`,
`POST /tasks`, `PATCH /tasks/{id}`) require the caller's membership role to be `founder` or
`team_member` — a `mentor` gets a `403`.

**Every success response is the standard envelope `{"data": …, "meta": null}`** — `meta` is
literally `null` on every mission endpoint (no pagination cursor is used). Errors drop `data`/
`meta` and return `{"error": {…}}` (see §7). **Every mission endpoint returns HTTP `200` on
success** — including `POST /missions/tasks` (it is *not* a `201`; the route sets no custom status
code). Don't branch on `201` vs `200` for the create call.

---

## 1. `GET /api/v1/missions/today` — today's mission (lazy-generated)

The one call the daily-mission screen is built on. If today's mission doesn't exist yet, this
endpoint **generates it on the fly** — it selects the next 1–3 incomplete tasks from the founder's
roadmap, snapshots each into a mission task, and returns them. A second call the same day returns
the *same* mission (generation is once-per-day, verified live), so it is safe to call on every
screen focus.

`e2e/_captures/mission/today.json` — freshly generated, three roadmap-drawn tasks, before any are
completed (status `200`):

```json
{
  "data": {
    "mission_date": "2026-08-27",
    "status": "pending",
    "streak": 0,
    "tasks": [
      {
        "id": "19e5b553-3c15-4b84-bd7c-2d61b4b8aed9",
        "roadmap_task_id": "958ef280-7390-4b5e-8550-de9982c2cc1e",
        "title": "Run 10 customer interviews",
        "reason": "From your 'Validate demand' milestone.",
        "effort": "medium",
        "status": "todo",
        "order": 0,
        "completed_at": null,
        "reject_reason": null
      },
      {
        "id": "2f866da1-4a36-423d-a060-d052b292bedc",
        "roadmap_task_id": "24a2e59a-4cf4-4abf-a58f-70a96d2b1e8f",
        "title": "Synthesize problem hypotheses",
        "reason": "From your 'Validate demand' milestone.",
        "effort": "small",
        "status": "todo",
        "order": 1,
        "completed_at": null,
        "reject_reason": null
      },
      {
        "id": "2aef6d93-a13c-47cf-9d8b-106044556573",
        "roadmap_task_id": "4114b8ca-7324-4525-a692-42c5f15baf22",
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
  "meta": null
}
```

**Task field reference (this exact shape is returned everywhere a task appears — the tree in
`/today`, and the single-task bodies of `POST /tasks` and `PATCH /tasks/{id}`):**

| Field | Type | Notes |
|---|---|---|
| `id` | uuid string | the **mission task** id — use this in `PATCH /missions/tasks/{id}`, *not* `roadmap_task_id`. |
| `roadmap_task_id` | uuid string **or `null`** | the roadmap task this was drawn from; `null` for custom tasks (see the trap below). |
| `title` | string | snapshotted from the roadmap task at generation time. |
| `reason` | string **or `null`** | templated why-line; `null` for custom tasks (see the trap below). |
| `effort` | `"small"` \| `"medium"` \| `"large"` | reused `TaskEffort` enum. |
| `status` | `"todo"` \| `"done"` \| `"snoozed"` \| `"rejected"` | |
| `order` | int | render tasks sorted by this. |
| `completed_at` | ISO-8601 timestamptz **or `null`** | `null` until the task is completed (see §4). |
| `reject_reason` | string **or `null`** | one of the three reject chips, else `null`. |

**Field-nesting / null traps — call these out in the UI:**
- **`roadmap_task_id` is `null` on custom tasks and a real UUID on roadmap-drawn tasks.** In
  `today.json` all three tasks carry a real `roadmap_task_id`; in `task_create.json` (§5) the
  custom task's is `null`. Use its presence to decide whether to render the "from your roadmap"
  milestone chip — don't assume it's always populated.
- **`reason` is `null` on custom tasks** and a `"From your '…' milestone."` string on
  roadmap-drawn tasks. Same discriminator as `roadmap_task_id` today; render the reason line only
  when it's non-null.
- **`completed_at` is `null` until the task is completed**, then an ISO-8601 timestamp *with*
  timezone offset (`"2026-08-27T11:34:08.276135+00:00"`, see §3) — parse it as tz-aware.

The empty-state message is a **separate shape** — see §2.

### `streak` — derived, and it can reset

`streak` is a whole number computed on every read: consecutive days ending today (or yesterday,
if today's mission isn't complete yet) whose mission reached `complete`. It is `0` in `today.json`
(nothing completed yet) and `1` in `today_complete.json` (§3, first complete day). **It is not
stored** — a missed day silently resets it to a lower number on the next read. Render it as a live
value from this endpoint; never cache it client-side across days as if it only ever increases.

### `reason` — AI-personalized shortly after generation

`reason` starts life as the templated string shown above (`"From your '<milestone>' milestone."`,
or `null` for a custom task) the instant a mission is generated. If the mission has at least one
task, generation also enqueues an `ai.mission.reason` job (Module 03) that rewrites each task's
`reason` with an LLM-authored one-liner — this typically lands within a few seconds, well before a
founder who just opened the app would notice. There is no separate endpoint or webhook for this:
**re-fetch `GET /api/v1/missions/today`** (the same call the screen already makes on focus) to
pick up the upgraded text once the job has run. The 06:00 scheduler (Module 20 Slice 3) pre-warms
today's mission ahead of most founders' first open, so in practice `reason` is already
AI-authored by the time the screen loads.

`reason` is `string | null`, **≤300 characters** — the worker truncates defensively
(`new_reason[:300]`) even though the model is asked for a single sentence. It is exactly the same
field, same nullability, as the templated version — there is no separate "is this AI-authored"
flag on the task; the FE cannot and should not try to distinguish templated from AI-personalized
text at render time.

> **AI budget note:** if the workspace is over its daily LLM token budget, this rewrite is skipped
> and `reason` stays on the templated string (see `docs/fe-integration-guide-ai-status.md` —
> `GET /ai/status` reports `over_budget`).

`e2e/_captures/mission_reason/today_before_drain.json` — `GET /missions/today` immediately after
generation, task at `order: 0`, templated `reason` (status `200`):

```json
{
  "id": "4bfc3a44-a753-407e-ac3b-9fbb9af320e6",
  "roadmap_task_id": "1b8b6f2c-12dd-4a80-8f2c-277537d7a1c1",
  "title": "Describe the problem in one paragraph",
  "reason": "From your 'Write your problem statement' milestone.",
  "effort": "small",
  "status": "todo",
  "order": 0,
  "completed_at": null,
  "reject_reason": null
}
```

`e2e/_captures/mission_reason/today_after_drain.json` — same task, same `id`, `GET
/missions/today` re-fetched after the `ai.mission.reason` worker drained (status `200`):

```json
{
  "id": "4bfc3a44-a753-407e-ac3b-9fbb9af320e6",
  "roadmap_task_id": "1b8b6f2c-12dd-4a80-8f2c-277537d7a1c1",
  "title": "Describe the problem in one paragraph",
  "reason": "[stub-llm] reason",
  "effort": "small",
  "status": "todo",
  "order": 0,
  "completed_at": null,
  "reject_reason": null
}
```

`"[stub-llm] reason"` is the offline stub's deterministic marker (`LLM_PROVIDER=stub`, used in
tests and e2e) — a real provider returns a genuine sentence in its place, same field, same shape.
**Note the other two tasks in the same capture (`order: 1`, `order: 2`) keep their templated
`reason` in `today_after_drain.json`** — the deterministic stub's `complete_json` only ever
returns one array item per call in this harness, so only one task was rewritten in this
particular run. This is a property of the stub, not evidence the real upgrade only rewrites one
task per mission — the job's schema accepts up to `len(tasks)` reasons
(`app/services/mission/ai_reason.py::mission_reason_schema`) and a real provider is expected to
return one per task.

---

## 2. The empty-states — `no_roadmap` and weekends-off

> ⚠️ **Neither state below was exercised by the live E2E journey** (the journey onboards a founder
> *with* a roadmap, on whatever weekday the suite runs). Both shapes are **derived from the source**
> — `app/api/v1/endpoints/mission.py::get_today` and
> `app/services/mission/service.py::{get_or_generate_today, serialize_mission}` — and are
> unit-tested, not captured live. Build against them, but treat them as not-double-verified
> end-to-end (see the verification table).

### 2a. `no_roadmap` — the workspace has no roadmap yet

When `GET /missions/today` runs for a workspace that has no roadmap, generation returns nothing and
the endpoint returns a distinct discriminated shape (source: `get_today` returns
`success_response({"status": "no_roadmap"})`):

```json
{
  "data": {
    "status": "no_roadmap"
  },
  "meta": null
}
```

Branch on `data.status === "no_roadmap"` **before** reading `tasks` — in this shape there is **no
`tasks`, no `streak`, no `mission_date`**. Render the empty-state ("Your mission comes from your
roadmap — generate one first"). In practice this is rare, because onboarding-complete generates
the roadmap inline; but a workspace that somehow has no roadmap will hit it.

### 2b. Weekends-off — an empty but real mission

When `weekend_missions` is `false` (the default) and today is Saturday or Sunday,
`get_or_generate_today` still creates the mission row but leaves it **task-less**, so the FE can
show "Weekends off" rather than pulling roadmap work. The shape is the normal `/today` mission with
an empty `tasks` array (source: `serialize_mission` over a task-less mission):

```json
{
  "data": {
    "mission_date": "2026-08-29",
    "status": "pending",
    "streak": 0,
    "tasks": []
  },
  "meta": null
}
```

Distinguish this from `no_roadmap`: here `status`/`streak`/`mission_date` are all present and
`tasks` is an empty array — it is a real mission, just empty. (`weekly_completion_pct` in history,
§6, deliberately ignores these empty missions so "weekends off" doesn't dent the percentage.)

---

## 3. `GET /api/v1/missions/settings` · `PATCH /api/v1/missions/settings`

`GET /missions/settings` (member) — settings are **lazily created with defaults** on first read, so
this never 404s. `e2e/_captures/mission/settings.json` (status `200`):

```json
{
  "data": {
    "mission_size": 3,
    "delivery_time": "06:00:00",
    "weekend_missions": false
  },
  "meta": null
}
```

**Field-nesting trap: `delivery_time` is serialized as a bare `"HH:MM:SS"` string
(`"06:00:00"`)** — not an ISO datetime, no date, no timezone. It is metadata for a future
scheduled-generation feature (Module 20); it does **not** currently drive when the mission is
generated (generation is lazy, on the first `GET /today` of the day). Send it back in the same
`"HH:MM:SS"` form on `PATCH`.

`PATCH /missions/settings` (editor) accepts any subset of `{mission_size, delivery_time,
weekend_missions}` and returns the **same three-field shape** as the GET above. The live journey
exercises it only on weekend days, with body `{"weekend_missions": true}` → `200` (its response is
not separately captured — it is byte-identical to `settings.json` with `weekend_missions: true`).

- `mission_size` must be `1`, `2`, or `3` — anything outside that range is a `422 VALIDATION_ERROR`
  (see §7 for the exact shape). Omitting a field leaves it unchanged.
- Changing `mission_size` affects **future** generation only; it does not resize a mission already
  materialised for today.

---

## 4. `POST /api/v1/missions/tasks` — add a custom task

Editor-only. Appends a user-authored task to **today's** mission (calling `/today`'s generation
first if today's mission doesn't exist yet — and if there's no roadmap either, it starts today's
mission itself rather than blocking you). Body: `{"title": "…", "effort": "small"|"medium"|"large"}`
(`effort` optional, defaults to `"medium"`).

`e2e/_captures/mission/task_create.json` (`POST /missions/tasks`,
`{"title": "Call three design partners", "effort": "small"}`) — status `200`:

```json
{
  "data": {
    "id": "aa1ed676-eb4f-40d5-9db7-d9d778cba4d6",
    "roadmap_task_id": null,
    "title": "Call three design partners",
    "reason": null,
    "effort": "small",
    "status": "todo",
    "order": 3,
    "completed_at": null,
    "reject_reason": null
  },
  "meta": null
}
```

**This is the canonical "custom task" shape — `roadmap_task_id: null` and `reason: null`** (compare
the roadmap-drawn tasks in §1, which have both populated). `order` is `3` here because it was
appended after the three roadmap tasks (`order` 0–2). The response is the single task, not the whole
mission — re-fetch `GET /missions/today` if you need the updated full list.

---

## 5. `PATCH /api/v1/missions/tasks/{task_id}` — the action endpoint

Editor-only. `{task_id}` is the **mission task** `id` (the `id` field, not `roadmap_task_id`). Body
is `{"action": "…", …}` where `action` is one of four values. All four return the updated single
task in the §1 shape, status `200`.

| `action` | Extra body | Effect | Event(s) fired |
|---|---|---|---|
| `"complete"` | — | `status → "done"`, sets `completed_at`. If this was the **last** non-rejected task, the whole mission flips to `complete`. | `mission.task.completed`; then `mission.completed` if the mission is now complete; then `mission.streak.milestone` if the new streak is exactly 7/30/100. |
| `"snooze"` | — | `status → "snoozed"`. Tomorrow's generation re-includes snoozed tasks **first**, ahead of fresh roadmap picks. Idempotent. | — |
| `"reorder"` | `"order": <int>` (**required**) | sets the task's `order`. Missing `order` → `422`. | — |
| `"reject"` | `"reject_reason": <string>` (**required**) | `status → "rejected"`, records the reason. | — |

**`reject_reason` must be exactly one of three strings** (validated server-side; anything else is a
`422`): `"Already done"`, `"Wrong priority"`, `"Doesn't apply"`. Render these as the only three
reject chips.

**Events are internal-only in v1** — they publish to an in-process bus with no consumer yet
(Module 20 will deliver them). There is no client-visible push today; they're documented so the FE
knows completion is a meaningful backend transition, not just a field flip.

`e2e/_captures/mission/task_complete.json` (`PATCH /missions/tasks/{id}`, `{"action": "complete"}`,
the last of the three roadmap tasks — the one that flipped the mission to complete) — status `200`:

```json
{
  "data": {
    "id": "2aef6d93-a13c-47cf-9d8b-106044556573",
    "roadmap_task_id": "4114b8ca-7324-4525-a692-42c5f15baf22",
    "title": "Draft 3 pricing options",
    "reason": "From your 'Pricing test' milestone.",
    "effort": "small",
    "status": "done",
    "order": 2,
    "completed_at": "2026-08-27T11:34:08.303973+00:00",
    "reject_reason": null
  },
  "meta": null
}
```

Note `completed_at` is now a tz-aware ISO-8601 timestamp. This response only shows the **task** —
it does *not* tell you the mission flipped to `complete`. Re-fetch `GET /missions/today` after the
completing call to pick up the mission-level `status: "complete"` (below).

### The mission after all roadmap tasks are done

`e2e/_captures/mission/today_complete.json` — `GET /missions/today` re-fetched immediately after
completing all three roadmap tasks (status `200`):

```json
{
  "data": {
    "mission_date": "2026-08-27",
    "status": "complete",
    "streak": 1,
    "tasks": [
      {
        "id": "19e5b553-3c15-4b84-bd7c-2d61b4b8aed9",
        "roadmap_task_id": "958ef280-7390-4b5e-8550-de9982c2cc1e",
        "title": "Run 10 customer interviews",
        "reason": "From your 'Validate demand' milestone.",
        "effort": "medium",
        "status": "done",
        "order": 0,
        "completed_at": "2026-08-27T11:34:08.276135+00:00",
        "reject_reason": null
      },
      {
        "id": "2f866da1-4a36-423d-a060-d052b292bedc",
        "roadmap_task_id": "24a2e59a-4cf4-4abf-a58f-70a96d2b1e8f",
        "title": "Synthesize problem hypotheses",
        "reason": "From your 'Validate demand' milestone.",
        "effort": "small",
        "status": "done",
        "order": 1,
        "completed_at": "2026-08-27T11:34:08.292472+00:00",
        "reject_reason": null
      },
      {
        "id": "2aef6d93-a13c-47cf-9d8b-106044556573",
        "roadmap_task_id": "4114b8ca-7324-4525-a692-42c5f15baf22",
        "title": "Draft 3 pricing options",
        "reason": "From your 'Pricing test' milestone.",
        "effort": "small",
        "status": "done",
        "order": 2,
        "completed_at": "2026-08-27T11:34:08.303973+00:00",
        "reject_reason": null
      }
    ]
  },
  "meta": null
}
```

The mission's `status` is `complete` and `streak` is `1` (first complete day). Note this capture
was taken **before** the custom task was added — which is why it shows three tasks, while history
(§6, captured after) shows four. That's the subject of the next trap.

---

## 6. `GET /api/v1/missions/history` — past missions + weekly %

Member-readable, and **strictly read-only** — unlike `/today`, it never generates a mission, so a
mission only appears here once `/today` or a task action has materialised it. Missions are
newest-first, each with `completed`/`total` task counts and its `status`, plus a rolling 7-day
completion percentage.

`e2e/_captures/mission/history.json` — captured after completing all three roadmap tasks **and**
adding one still-`todo` custom task (status `200`):

```json
{
  "data": {
    "missions": [
      {
        "mission_date": "2026-08-27",
        "completed": 3,
        "total": 4,
        "status": "complete"
      }
    ],
    "weekly_completion_pct": 100
  },
  "meta": null
}
```

- **`total` counts non-rejected tasks; `completed` counts `done` tasks.** A task rejected via
  `PATCH …/{id}` `{"action":"reject"}` drops out of `total` entirely (it isn't counted against the
  founder) — so a mission with, say, 2 done + 1 rejected reads as `completed: 2, total: 2`.
- `weekly_completion_pct` is the share (rounded %) of the workspace's **non-empty** missions in the
  last 7 days that reached `complete`. Empty weekends-off missions (§2b) and all-rejected missions
  are excluded from both numerator and denominator, so they can't drag it down. It's `100` here
  because today's is the only qualifying mission and it's complete. Returns `0` when the window has
  no qualifying missions (never a divide-by-zero).

### ⚠️ UX consequence — a `complete` mission can show `completed:3, total:4`

**`history.json` above is exactly this case: `status: "complete"` while `completed:3, total:4`.**
This is not a bug and the FE must **not render it as a contradiction** (e.g. don't show "Complete"
next to a red "3/4 — incomplete" warning). Here's why it happens: the mission was marked
`complete` the moment its last *roadmap* task was done (3/3). A custom task added **afterward** does
**not** downgrade a mission that has already reached `complete` — the completion check only runs
when a task is *completed*, never when one is *added*. So the mission stays `complete` while its
counts read 3/4.

**Guidance:** treat `status` as the source of truth for the mission's completion state, and render
`completed/total` as informational progress ("3 of 4 done"), not as a second, competing completion
signal. A mission that is `complete` with `total > completed` simply means work was added after the
founder had already cleared the day's plan — show it as done, with the extra task as an optional
add-on, not as an unfinished mission.

---

## 7. Errors — shapes, and the auth boundary

> ⚠️ **The 4xx bodies below were not individually captured by the live E2E journey** (it walks the
> happy path). They are **derived from the shared `AppError` taxonomy** used across this API and are
> covered by the unit suite (`tests/api/test_mission_today.py`, `test_mission_tasks.py`). The error
> **envelope shape** is identical to every other module's — it is exercised live throughout the
> roadmap and health-score journeys (see `docs/fe-integration-guide-roadmap.md` §8,
> `docs/fe-integration-guide-health-score.md` §6) — so the *shape* is trustworthy even though these
> specific mission bodies weren't re-captured here.

Every error is `{"error": {"code": …, "message": …, "field_errors": [...]}}` (no `data`/`meta`).

**`403 FORBIDDEN` — a mentor (or any non-editor) attempting a write** (`PATCH /settings`,
`POST /tasks`, `PATCH /tasks/{id}`):

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

Gate every mission write control on the caller's role being `founder` or `team_member`; a mentor
should see today's mission and history but never see write affordances enabled.

**`404 NOT_FOUND` — an unknown *or* cross-workspace mission task id** on `PATCH /missions/tasks/{id}`:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

This is a **uniform 404**: a task id that is syntactically valid and genuinely exists — just under
a different workspace's mission — returns the exact same `NOT_FOUND` shape as an id that exists
nowhere. Never build UI that distinguishes "doesn't exist" from "not yours" from this response;
treat any 404 here as "drop it from local state" (same guidance as roadmap/health-score).

**`422 VALIDATION_ERROR` — bad input.** The `field_errors` array names the offending field. Cases:
`mission_size` outside 1–3; `action` not one of `complete/snooze/reorder/reject`; `reorder` without
`order`; `reject` with a `reject_reason` not in the allowed set. Example — `mission_size` out of
range (source: `patch_settings`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Mission size must be 1–3.",
    "field_errors": [
      { "field": "mission_size", "message": "Must be 1, 2, or 3." }
    ]
  }
}
```

---

## Verification table

Rows marked ✅ were exercised **live**, over real HTTP, against a real Postgres-backed server
(`make e2e`, `e2e/test_mission.py::test_mission_journey`) — not just unit-tested in-process — and
the response body is captured verbatim in the named file. Rows marked ⬜ are covered by the unit
suite but were **not** re-asserted over live HTTP; the shape source is named.

| Endpoint / behavior | Verified live? | Source |
|---|---|---|
| `GET /missions/today` — freshly-generated, 3 roadmap-drawn tasks, `streak: 0` | ✅ | `today.json` |
| `GET /missions/today` — idempotent second read returns the same mission | ✅ | (asserted in journey; same body as `today.json`) |
| `GET /missions/today` — after all roadmap tasks done: `status: complete`, `streak: 1` | ✅ | `today_complete.json` |
| `GET /missions/today` — `no_roadmap` empty-state | ⬜ | derived from code (`get_today`); unit-tested, **not captured live** |
| `GET /missions/today` — weekends-off empty mission (`tasks: []`) | ⬜ | derived from code (`serialize_mission`); unit-tested, **not captured live** |
| `roadmap_task_id`/`reason` populated on roadmap tasks, `null` on custom | ✅ | `today.json` (populated) + `task_create.json` (null) |
| `tasks[].reason` — templated on generation, rewritten by `ai.mission.reason` worker (stub) within seconds; re-fetch `/today` to see it | ✅ | `today_before_drain.json` → `today_after_drain.json` (`e2e/_captures/mission_reason/`) |
| `GET /missions/settings` — defaults, `delivery_time` as `"06:00:00"` | ✅ | `settings.json` |
| `PATCH /missions/settings` — `{"weekend_missions": true}` → 200 | ⬜ | live **only on weekend runs** — the journey flips it only on Sat/Sun, and the captures were taken on a Thursday (2026-08-27), so this PATCH was **not exercised** in the capture run; body derived (identical to `settings.json` with `weekend_missions: true`); unit-tested |
| `PATCH /missions/settings` — `mission_size` outside 1–3 → `422` | ⬜ | derived from `AppError`; unit-tested (`test_mission_today.py`) |
| `POST /missions/tasks` — custom task, `roadmap_task_id: null`, appended `order` | ✅ | `task_create.json` |
| `PATCH /missions/tasks/{id}` — `complete`, sets `completed_at`, flips mission | ✅ | `task_complete.json` |
| `PATCH /missions/tasks/{id}` — `snooze` / `reorder` / `reject` transitions | ⬜ | derived from code; unit-tested (`test_mission_tasks.py`) |
| `mission.task.completed` / `mission.completed` / `mission.streak.milestone` events fire | ⬜ | unit-tested (event bus asserted in `test_mission_tasks.py`); internal, no live-observable effect |
| `GET /missions/history` — `completed/total`, `weekly_completion_pct` | ✅ | `history.json` |
| History `status: complete` while `completed:3, total:4` (custom task added after completion) | ✅ | `history.json` |
| Mentor write → `403 FORBIDDEN` | ⬜ | derived from `AppError`; unit-tested |
| Cross-workspace / unknown task id → uniform `404 NOT_FOUND` | ⬜ | derived from `AppError`; unit-tested (`test_mission_tasks.py`) |

Every ⬜ row is safe to build against — it's covered by the unit suite
(`tests/api/test_mission_*.py`, `tests/services/test_mission_generate.py`) and the error/empty-state
shapes match this API's established envelope — it just wasn't double-verified end-to-end over live
HTTP in this module's journey.

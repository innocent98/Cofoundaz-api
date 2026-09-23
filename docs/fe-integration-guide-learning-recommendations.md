# FE Integration Guide — Learning Recommendations (`GET /learning/recommendations`)

The response bodies below are pasted **verbatim** from a live capture taken by
`e2e/test_learning.py::test_learning_journey` running against a real server
(`bash scripts/e2e_run.sh`) — see `e2e/_captures/learning/recommendations_before.json` and
`e2e/_captures/learning/recommendations_reason.json`. Nothing here is retyped from the schema,
the service code, or memory.

This guide covers only the recommendations shelf and its `recommendation_reason` field (the
Module 03 deferred-AI upgrade shipped on this branch). No FE guide previously existed for the
Learning Academy module (Module 17) — the rest of `/learning/*` (`/courses`, `/courses/{id}`,
enrolment, lesson completion, certificates, paths, articles) is not documented here; ask if you
need those endpoints covered too.

Base path: `/api/v1/learning`. Every route requires a Bearer access token
(`Authorization: Bearer <token>`) and an `X-Workspace-Id` header identifying the active workspace
(`GET /auth/me` → `data.active_workspace_id`), same as every other tenant-scoped endpoint. Reads
are open to `founder` and `team_member` roles; a `mentor` gets `403`.

---

## 1. `GET /api/v1/learning/recommendations` — the front-page shelf

`e2e/_captures/learning/recommendations_before.json` — the very first read for a freshly
onboarded founder at the `validation` stage, before the AI worker has run:

```json
{
  "data": {
    "stage": "validation",
    "recommendation_reason": "Recommended for your validation stage.",
    "recommended": [
      {
        "id": "idea-shape-the-problem",
        "title": "[Placeholder] Shape the problem",
        "level": "beginner",
        "stage_tags": ["idea", "validation"],
        "lesson_count": 3,
        "duration_min": 30,
        "enrolled": false,
        "progress": 0,
        "completed": false
      },
      {
        "id": "validation-talk-to-customers",
        "title": "[Placeholder] Talk to customers",
        "level": "beginner",
        "stage_tags": ["validation"],
        "lesson_count": 3,
        "duration_min": 34,
        "enrolled": false,
        "progress": 0,
        "completed": false
      },
      {
        "id": "build-scope-the-mvp",
        "title": "[Placeholder] Scope the MVP",
        "level": "intermediate",
        "stage_tags": ["validation", "build"],
        "lesson_count": 2,
        "duration_min": 22,
        "enrolled": false,
        "progress": 0,
        "completed": false
      }
    ],
    "continue_watching": []
  },
  "meta": null
}
```

## Field reference

| Field | Type | Meaning |
|---|---|---|
| `stage` | string \| `null` | The startup's onboarding stage (`idea`/`validation`/`build`/…); `null` if not set. Drives which courses are shelved. |
| `recommendation_reason` | string | **New in this branch.** One line explaining why this shelf was chosen. **Always present** — never `null`, never absent — from the very first read. Starts templated, upgrades to AI-authored text in place; see §2. |
| `recommended` | array of course summaries | The stage-matched shelf: beginner-first, catalog order on ties, completed courses excluded. Each item is the same course-summary shape used by `/learning/courses`. |
| `continue_watching` | array of course summaries | Enrolled-but-not-completed courses, most recently updated first. Empty for a founder who hasn't enrolled in anything yet. |

---

## 2. `recommendation_reason` — templated first, AI-personalized on a later poll

This is a **shelf-level** field — one reason for the whole `recommended` list, not a per-course
reason (contrast with Today's Mission's per-task `reason`, `docs/fe-integration-guide-mission.md`
§1, which is the closest existing analogue in this API).

**Lifecycle:**

1. The **first** `GET /learning/recommendations` for a startup writes a templated row
   (`"Recommended for your {stage} stage."`, or `"Recommended to help you get started."` when
   `stage` is `null`) and enqueues an `ai.learning.recommendations` job. The templated line is
   returned **immediately** — there is no `"generating"` placeholder state exposed on this field,
   unlike the dashboard briefing's `{status, message}` shape (`docs/fe-integration-guide-
   dashboard.md`). The FE cannot tell "AI hasn't run yet" from "AI ran and produced this exact
   templated line" by inspecting the field alone (see the note below).
2. Within seconds, the `ai.learning.recommendations` worker (`app/worker/handlers/ai.py:351`)
   overwrites `reason` with LLM-authored text and the shelf reason never regenerates again
   after that — it is a one-shot upgrade per startup, not re-triggered on later reads.
3. **There is no separate "is this AI-authored" flag.** `recommendation_reason` is exactly the
   same field before and after the upgrade — poll `GET /learning/recommendations` again (the
   same call the screen already makes on load/focus) to pick up the personalized line once the
   job has run. Do not try to distinguish templated from AI-authored text at render time; render
   whatever string is present.

`e2e/_captures/learning/recommendations_reason.json` — the same founder, same shelf, `GET
/learning/recommendations` re-fetched after the `ai.learning.recommendations` worker drained:

```json
{
  "data": {
    "stage": "validation",
    "recommendation_reason": "[stub-llm] reason",
    "recommended": [
      {
        "id": "idea-shape-the-problem",
        "title": "[Placeholder] Shape the problem",
        "level": "beginner",
        "stage_tags": ["idea", "validation"],
        "lesson_count": 3,
        "duration_min": 30,
        "enrolled": false,
        "progress": 0,
        "completed": false
      },
      {
        "id": "validation-talk-to-customers",
        "title": "[Placeholder] Talk to customers",
        "level": "beginner",
        "stage_tags": ["validation"],
        "lesson_count": 3,
        "duration_min": 34,
        "enrolled": false,
        "progress": 0,
        "completed": false
      },
      {
        "id": "build-scope-the-mvp",
        "title": "[Placeholder] Scope the MVP",
        "level": "intermediate",
        "stage_tags": ["validation", "build"],
        "lesson_count": 2,
        "duration_min": 22,
        "enrolled": false,
        "progress": 0,
        "completed": false
      }
    ],
    "continue_watching": []
  },
  "meta": null
}
```

**`"[stub-llm] reason"` is the offline stub provider's deterministic marker**
(`LLM_PROVIDER=stub`, used in tests and e2e) — a real OpenAI provider returns a genuine one-line
sentence in its place, same field, same shape, same ≤300-character cap (the worker truncates
defensively, `reason[:300]`).

### The stage-change edge case

If the founder's stage changes (onboarding is edited, or a later slice adds stage transitions),
the *next* read of `/learning/recommendations` detects the mismatch, resets `recommendation_reason`
to the newly-templated line for the new stage, and re-enqueues the AI job — so a stage change is
never left showing a stale AI reason for the old stage. This is not separately captured live
(no stage-change step in the journey); shape/behavior confirmed by
`tests/services/learning/test_reco_reason.py::test_stage_change_regenerates_and_reenqueues`.

### Over-budget behavior

When the workspace is over its daily LLM token budget, the `ai.learning.recommendations` job
skips its LLM call entirely and `recommendation_reason` **stays on the templated line** — this is
not an error state, and the row does not surface a "failed" or "stuck" signal of its own. See
`docs/fe-integration-guide-ai-status.md` — `GET /ai/status`'s `over_budget` field explains *why*
new AI enrichment is paused workspace-wide; this endpoint itself gives no per-record signal that
a specific `recommendation_reason` is still templated because of budget vs. simply not upgraded
yet (same ambiguity `ai-status.md` §0 already documents for every other AI consumer in this API).

### No-signal fallback (no stage set)

When `stage` is `null` (a startup with no onboarding stage recorded), the shelf falls back to
beginner courses across every stage and the AI job itself no-ops without an LLM call (there is
nothing course-specific to personalize a reason for) — `recommendation_reason` stays on
`"Recommended to help you get started."` permanently for that startup. Not separately captured
live; shape confirmed by
`tests/services/learning/test_reco_reason.py::test_none_stage_uses_generic_fallback` and
`app/worker/handlers/ai.py::handle_learning_recommendations` (the `stage is None or not titles`
early-return).

---

## 3. Verification table

| Behaviour | Verified live? | Source |
|---|---|---|
| `GET /learning/recommendations` returns 200 with `stage`, `recommendation_reason`, `recommended`, `continue_watching` | ✅ | `recommendations_before.json` |
| `recommendation_reason` present and templated on the very first read | ✅ | `recommendations_before.json` |
| `recommendation_reason` upgraded to AI-authored text after the `ai.learning.recommendations` worker drains; re-fetch picks it up | ✅ | `recommendations_before.json` → `recommendations_reason.json` |
| Stage-change resets the templated reason and re-enqueues | ⚠️ unit only — not exercised by the live journey (no stage-change step) | `tests/services/learning/test_reco_reason.py::test_stage_change_regenerates_and_reenqueues` |
| `stage: null` → generic fallback reason, no LLM call ever enqueued for the reason field | ⚠️ unit only | `tests/services/learning/test_reco_reason.py::test_none_stage_uses_generic_fallback` |
| Over-budget → `ai.learning.recommendations` skips the LLM call, keeps the templated reason | ⚠️ unit only — same class of gap as every other AI consumer in this API (see `docs/fe-integration-guide-ai-status.md`'s own verification table); forcing a live over-budget run would require seeding the `llm_usage_daily` ledger directly | `app/worker/handlers/ai.py::handle_learning_recommendations` (`metered_complete_json` returns `None`), `tests/worker/test_learning_reason_handler.py` |
| Concurrent first-reads race-guard (`uq_learning_reco_startup`) re-selects the winner's row instead of erroring | ⚠️ unit only — not exercised under real concurrency | `tests/services/learning/test_reco_reason.py::test_first_call_creates_generating_row_and_enqueues` (race-guard code path mirrors `get_or_create_enrollment`'s, same pre-existing gap noted in that pattern's other SOPs) |

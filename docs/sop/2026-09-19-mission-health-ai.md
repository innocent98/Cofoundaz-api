# SOP — Mission Reason + Health Recommendation AI Upgrade (Module 03 Slice 4)

**What shipped** — two more Module 03 (AI Co-Founder) consumers of the LLM seam, upgrading
templated text to LLM-authored text on two existing, already-shipped features: today's mission's
per-task `reason` line (Module 04) and each health-score recommendation's `body` (Module 06).
Both follow the same "async worker upgrades a templated value, in place, with the templated value
as the permanent fallback" pattern Module 03 Slices 1–3 already established for assessment
narrative, canvas ai-fill, and records ai-fill. No new API surface, no new route, **no
migration** — both slices only add a worker handler, a prompt builder + JSON schema, and one new
`job_dispatcher.enqueue(...)` call site each.

Commits (branch `work`, off `develop` @ `e72f783`), oldest to newest:
`35b6ff2` (mission reason prompt builder + schema) → `e27be8b` (`ai.mission.reason` worker) →
`3d9a959` (enqueue `ai.mission.reason` when a mission is generated) → `6daec95` (health
recommendation prompt builder + schema) → `09eeef6` (`ai.health.recommendations` worker) →
`e43b120` (enqueue `ai.health.recommendations` after health recompute) → `95087ce` (live e2e for
both, with captures) → **this commit** (FE guides, SOP, checklist reconcile). PR to `develop` to
follow.

## Why

Module 03 Slice 3's own SOP Follow-ups named the mission reason line and health-score
recommendations as two of the five still-unbuilt "deferred to Module 03" AI consumers (the other
three — dashboard AI briefing, onboarding AI panel, roadmap replan rationale — remain unbuilt; see
Follow-ups). Both had been shipping templated-only text since their own modules launched: Module
04's mission reason line has always been `"From your '{milestone}' milestone."`
(`docs/sop/2026-08-26-todays-mission.md`), and Module 06's recommendation `body` has always been
the static sentence baked into `RECOMMENDATION_CATALOG`
(`docs/sop/2026-08-19-health-score.md`). Upgrading both closes two more items off that deferred
list using infrastructure Module 03 Slices 1–2 already built (`complete_json`, the structured-
output method on `LLMClient`) — no new LLM capability was needed, only two new schema/prompt
pairs and two new worker handlers.

## How

**Same async-upgrade pattern, reused twice, not re-designed.** Both slices follow Slice 1–3's
shape exactly: the synchronous code path keeps writing the templated/catalog value as it always
has (unchanged — a founder who never triggers the AI upgrade, or whose job fails, still gets
correct text); a new job type is enqueued at the existing write point; a new handler in
`app/worker/handlers/ai.py` re-fetches the row(s), calls `get_llm_client().complete_json(...)`
with a strict schema, and overwrites the field(s) in place with `db.flush()` only (the runner
owns the transaction, exactly as every other `ai.py` handler does).

**Mission: `_enqueue_mission_reason` gates on `order`, not on task content.**
`get_or_generate_today` (`app/services/mission/service.py`) already returns an empty
"weekends-off" mission when `weekend_missions` is off (§2b of the mission FE guide) — that mission
has zero tasks, and there is nothing for an LLM to write a reason for. Rather than inline an
`if tasks:` check at the call site, the implementer extracted a small `_enqueue_mission_reason(db,
mission, startup, order)` helper (`order` being the running task-count from the generation loop)
so the one-line gate (`if order:`) reads clearly and keeps `get_or_generate_today` under ruff's
C901 complexity ceiling — the reviewer verified this refactor preserves the brief's exact
enqueue/skip semantics (task 3 review, clean). The handler itself
(`handle_mission_reason`) re-fetches all of the mission's tasks by `order`, asks the model for a
`{order, reason}` pair per task (schema `maxItems = len(tasks)`), and only overwrites a task whose
order the model actually returned a reason for — a task the model skips keeps its templated
reason, so a partial or malformed LLM response degrades gracefully instead of blanking anything.
`reason` is truncated to 300 chars defensively (`new[:300]`) even though the prompt already asks
for a single short sentence.

**Health: idempotency via a body-equals-catalog-default guard, with a genuine no-LLM-call fast
path.** Unlike the mission reason line (which is safe to re-run — regenerating the same mission
row doesn't happen), the health recompute can run repeatedly for the same startup (every
assessment, every lazy-read fallback — see the corrected mechanics below). `handle_health_
recommendations` avoids both re-personalizing an already-personalized row and re-touching a row
the founder has already acted on: it queries only `status == pending` rows, then filters to
`r.body == defaults.get(r.key)` (`catalog_bodies()` — every catalog key's default text) — a row
whose body no longer matches the default has already been personalized (or, being non-pending,
was excluded by the first filter) and needs no further work. **If that filtered list is empty,
the handler returns before making any LLM call at all** — a job that fires on a health recompute
where every pending recommendation is already personalized costs nothing, not even a wasted
network round-trip.

**The `key` enum in the health schema is what lets the stub upgrade a *real* row.**
`health_recommendation_schema(keys)` constrains the model's `key` field to
`{"type": "string", "enum": list(keys)}` — the exact set of pending, not-yet-personalized keys for
this call. This matters beyond strictness: `StubLLMClient`'s enum handling always returns the
*first* enum member, so constraining `keys` to the real to-do list (rather than, say, the whole
catalog) is what makes the stub's response land on `legal.founder_agreement` — a recommendation
that actually exists on this startup's row set — instead of on an arbitrary or non-existent key.
The mission schema's `order` field is a plain integer (not enum-constrained), since mission task
orders are dense small integers a model can reliably echo back without an enum's help.

**`title` is left as the catalog headline; only `body` is personalized.** `title` is the
recommendation's short, stable label (`"Sign a founders' agreement"`) used for UI headings, list
sorting, and (implicitly) as a display key alongside `key`/`dimension`. Only `body` — the longer,
explanatory sentence — is a natural fit for personalization; rewriting `title` too would make the
recommendation list's headings non-deterministic across reads, which nothing in the product
currently expects (§5 of the health-score FE guide relies on `title` being cache-stable).

**Enqueue points: `get_or_generate_today` (mission) and `recompute_health_score` (health).**
Neither call is behind a feature flag or config gate — every mission generation with at least one
task enqueues `ai.mission.reason`, and every successful health recompute enqueues
`ai.health.recommendations` (further protected by the idempotency guard above, so a burst of
recomputes for one startup is cheap even though the enqueue itself is unconditional).

**Two things the task-7 live e2e work corrected that the design/plan text got wrong (carried into
both FE guides in this pass) — see `.superpowers/sdd/2026-09-19-module-03-mission-health-ai/
progress.md`, "Task 7: FINDING":**

1. **The mission endpoint is `/api/v1/missions/today` (plural "missions")**, not `/mission/today`
   as the spec/plan text wrote — the router prefix (`app/api/v1/api.py`) is plural. The FE guide
   already used the plural path (it predates this slice); this slice's e2e and this SOP both
   verify against the real plural path.
2. **Health recommendations actually materialize via the lazy-read `GET /health-score` path, not
   the assessment-complete path the health-score FE guide's §1 previously implied.**
   `complete_assessment`'s own inline recompute call runs in the same request as
   `db.add(AssessmentResult(...))`, but `SessionLocal` is `autoflush=False`
   (`app/db/session.py`) — nothing flushes between the `add()` and the recompute's own read of
   `latest_completed_result`, so that read doesn't see the row it was just given and the
   recompute silently returns `None`: no `HealthScore`, no recommendation rows, no
   `ai.health.recommendations` enqueue. `get_overview`'s existing lazy-on-read fallback (built in
   Module 06, unrelated to this slice) is what actually creates everything, in a separate request
   whose fresh session sees the already-committed assessment. This is a **pre-existing** ordering
   quirk in `complete_assessment`, not something this slice introduced — but this slice's AI
   upgrade only fires once recommendations exist at all, so the finding is directly relevant to
   "when does the FE actually see AI-personalized bodies," and both FE guides now document the
   real mechanics inline (mission guide §1's "AI reason line" section; health-score guide's §1
   correction callout and §5 `body` section) rather than repeating the incorrect same-transaction
   claim.

## What's involved

**No migration.** `MissionTask.reason` and `HealthRecommendation.body` are pre-existing columns
(Module 04's `0009_mission`, Module 06's `0005` migration) — this slice only changes what a worker
writes into them. `git diff --stat e72f783..95087ce -- alembic app/db` is empty; no schema files
touched.

**Mission reason (`35b6ff2` → `e27be8b` → `3d9a959`)**
- `app/services/mission/ai_reason.py` (new) — `mission_reason_schema(n) -> dict` (strict schema,
  `reasons: [{order, reason}]`, `maxItems = n`) · `build_mission_reason_messages(tasks, *, name,
  industry, stage) -> list[LLMMessage]` (PII-free: startup name/industry/stage + `order`/`title`
  per task only).
- `app/worker/handlers/ai.py::handle_mission_reason(db, job)` — re-fetches the mission's tasks by
  `order`, calls `complete_json`, overwrites only the tasks the model returned a reason for
  (`[:300]` truncation), `db.flush()`. Registered as `"ai.mission.reason"`.
- `app/services/mission/service.py::_enqueue_mission_reason` + one call site inside
  `get_or_generate_today` — gated on `order` (skips the empty weekends-off mission).
- Tests: `tests/services/mission/test_ai_reason.py` (schema/prompt), `tests/worker/
  test_mission_reason_handler.py`, `tests/services/test_mission_generate.py` (enqueue gating,
  extended).

**Health recommendations (`6daec95` → `09eeef6` → `e43b120`)**
- `app/services/health_score/ai_recommendations.py` (new) — `catalog_bodies() -> dict[key, body]`
  (every catalog key's default text) · `health_recommendation_schema(keys) -> dict` (strict
  schema, `recommendations: [{key, body}]`, `key` enum-constrained to the given pending keys) ·
  `build_health_recommendation_messages(recs, *, name, industry, stage) -> list[LLMMessage]`
  (PII-free: startup name/industry/stage + `key`/dimension-label/`title` per recommendation only).
- `app/worker/handlers/ai.py::handle_health_recommendations(db, job)` — idempotency guard
  (`body == catalog default` + `status == pending`), no-op / no-LLM-call fast path when nothing is
  left to personalize, `complete_json` call, overwrite-by-key, `db.flush()`. Registered as
  `"ai.health.recommendations"`.
- `app/services/health_score/service.py::recompute_health_score` — one new
  `job_dispatcher.enqueue(db, "ai.health.recommendations", {"startup_id": ...}, startup.id)` call,
  right after `generate_recommendations(...)`.
- Tests: `tests/services/health_score/test_ai_recommendations.py` (schema/prompt),
  `tests/worker/test_health_recommendations_handler.py` (idempotency + fast path + overwrite),
  `tests/services/test_health_recompute.py` (enqueue, extended).

**Live e2e + docs (`95087ce` → this commit)**
- `e2e/test_mission_reason.py`, `e2e/test_health_recommendations_ai.py` (new) — see Verification.
- `e2e/_captures/mission_reason/{today_before_drain,today_after_drain}.json`,
  `e2e/_captures/health_recommendations_ai/{overview_after_complete,recommendations_after_drain}.json`
  (new).
- `docs/fe-integration-guide-mission.md` — new "AI reason line" subsection under §1 (before/after
  task objects pasted verbatim) + a verification-table row.
- `docs/fe-integration-guide-health-score.md` — a correction callout under §1 (the real
  lazy-read trigger mechanics) + a new "`body` — AI-personalized..." subsection under §5 (pasted
  verbatim) + three verification-table rows (one superseding the now-inaccurate "recompute
  triggered inline by assessment completion" row rather than deleting it, so the doc's own
  history stays honest).
- `docs/checklist/PROJECT_CHECKLIST.md` — this reconcile (see below).

**Errors / API surface — none new.** Both `GET /api/v1/missions/today` and `GET
/api/v1/health-score` / `GET /api/v1/health-score/recommendations` are pre-existing, unchanged
routes; this slice only changes what value a field holds a few seconds after a read, never a
status code or an envelope shape.

## Verification

**Per-task unit verification (already green before the e2e task):**
- `tests/services/mission/test_ai_reason.py` — 3 passed (schema shape, `maxItems`, PII-free
  prompt).
- `tests/worker/test_mission_reason_handler.py` — 6 passed (rewrite-by-order, partial-response
  tolerance, 300-char truncation, missing-mission no-op).
- `tests/services/test_mission_generate.py` (extended) — 17 passed, including the enqueue-gating
  cases (`order > 0` enqueues, empty weekends-off mission does not).
- `tests/services/health_score/test_ai_recommendations.py` — 4 passed (schema shape, `key` enum
  constrained to given keys, PII-free prompt).
- `tests/worker/test_health_recommendations_handler.py` — 9 passed (idempotency guard, no-LLM-call
  fast path when nothing pending needs personalizing, overwrite-by-key, accepted/dismissed rows
  untouched, missing-startup no-op). Full `tests/worker/` suite re-run: 49 passed (no regression).
- `tests/services/test_health_recompute.py` (extended) — 9 passed, including the new enqueue
  assertion.

Each task's own `black --check` / `isort --check-only` / `ruff check` / targeted `mypy` passed
before merge to this branch (task reports 1–6); ruff's C901 complexity check specifically passed
on both `get_or_generate_today` (post `_enqueue_mission_reason` extraction) and
`recompute_health_score` with no new warnings.

**Live e2e (task 7, `95087ce`) — `./scripts/e2e_run.sh`, full suite:**

```
e2e/test_health_recommendations_ai.py::test_health_recommendations_ai PASSED [ 29%]
...
e2e/test_mission_reason.py::test_mission_reason_ai PASSED                [ 62%]
...
============================= 48 passed in 35.34s ==============================
```

All 48 e2e tests pass (46 pre-existing + 2 new), confirming no regression from the two new tests
sharing the worker queue with the rest of the suite. Captures inspected verbatim (not just
asserted programmatically):
- `today_before_drain.json` — 3 templated task reasons.
- `today_after_drain.json` — task `order: 0`'s reason is `"[stub-llm] reason"`; the other two
  tasks unchanged (expected stub behavior — its `complete_json` returns one array item per call;
  see the FE guide's inline note).
- `overview_after_complete.json` — `GET /health-score` after the lazy-read recompute; 5
  dimensions, 3 `top_recommendations`, all still catalog-default bodies (captured *before* the
  drain, proving the recompute alone doesn't personalize anything without the worker running).
- `recommendations_after_drain.json` — 9 pending recommendations; `legal.founder_agreement`'s
  `body` is `"[stub-llm] body"`, the rest unchanged (same one-item-stub behavior).

This is the full CI-relevant reproduction for the app-code slices (Tasks 1–7); this task (8) is
docs-only and introduces no `app`/`test` changes, so no additional lint/type/test run is needed
for it — see the plan's own "Final: full local CI reproduction" step, which runs once across the
whole branch before the PR opens.

## Operate / roll back

**New deploy-time requirement: none.** Both new job types run inside the existing `worker`
process (Module 20 Slice 2) — no new container, no new health check, no new config beyond what
Module 03 Slice 1 already introduced (`LLM_PROVIDER`/`LLM_API_KEY`/`LLM_MODEL`/`LLM_BASE_URL`/
`LLM_TIMEOUT`/`LLM_MAX_TOKENS`).

**Rollback:** revert this slice's commits as a unit (`35b6ff2..95087ce`, plus this docs commit).
No migration to downgrade. The only persisted side effect is `MissionTask.reason` /
`HealthRecommendation.body` values already overwritten by a successful job while this slice was
live — those are not automatically reverted by rolling back the code (there is no "undo AI
upgrade" operation), the same judgment call every prior Module 03 slice's SOP has made: the
AI-written text is real content, not a distinct piece of state a rollback needs to undo. Reverting
the code simply means both fields go back to templated/catalog-default-only on the next
generation/recompute (existing already-upgraded rows are untouched either way, since neither
handler re-runs against a row it already personalized).

## Follow-ups

**Dashboard AI briefing, onboarding AI panel, and roadmap replan rationale remain unbuilt.** This
slice closes two of the five Module-03-deferred AI consumers named across Modules 02/04/05/06's
own SOPs; Module 03 stays **open**. Each of the three remaining consumers needs its own
prompt/schema/trigger design, not a mechanical copy of this slice's pattern.

**The `complete_assessment` same-transaction recompute no-op is a pre-existing gap, surfaced but
not fixed here.** As documented in "How" above, `POST /assessments/{id}/complete` does not itself
reliably create the `HealthScore`/recommendation rows due to an autoflush-timing quirk in
`app/services/assessment/service.py` — only a subsequent `GET /health-score` does, via its
lazy-on-read fallback. This is self-healing in practice (both FE guides now document the required
`GET /health-score` follow-up call), but a client that completes an assessment and never calls
`GET /health-score` afterward would never get recommendations — or this slice's AI personalization
— materialized at all. Fixing the ordering directly (e.g. an explicit `db.flush()` before the
inline recompute call in `complete_assessment`) is out of scope for this docs-only task and for
Task 7 (test-only); it is a real follow-up against `app/services/assessment/service.py`.

**No structured "AI upgrade pending / still templated" signal.** Same gap every prior Module 03
slice's SOP has flagged: neither `MissionTask` nor `HealthRecommendation` carries a field the FE
can check to know whether a given `reason`/`body` is still templated/catalog-default or has
already been AI-personalized, short of comparing text or polling with a fixed delay.

**Stub-only proof of the "rewrites more than one row" path.** The live e2e run only exercises one
rewritten task / one rewritten recommendation per drain, because `StubLLMClient.complete_json`
deterministically returns a single array item regardless of how many the schema allows
(`maxItems`) — the same limitation Slice 3's SOP noted for records ai-fill. The multi-item path
(a mission with several tasks all rewritten, or several pending recommendations all personalized
in one job) is covered by the unit suite (`tests/worker/test_mission_reason_handler.py`,
`tests/worker/test_health_recommendations_handler.py`) with fixtures that return multi-item stub
payloads, not by this live e2e journey.

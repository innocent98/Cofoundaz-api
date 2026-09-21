# SOP — Roadmap Re-plan Rationale (Module 03 consumer)

**What shipped** — a roadmap re-plan (`RoadmapReplan`) now carries an AI-authored `rationale`: a
single holistic "why we re-planned" paragraph, distinct from the existing per-change `reason`
line. `POST /replan/apply` writes a **templated** rationale synchronously (so the field is never
`null` on a successful apply) and enqueues an `ai.roadmap.rationale` job that overwrites it with
LLM prose moments later; `GET /replan/history` exposes the current value on every row. This closes
the fifth of Module 03's six deferred AI consumers — the roadmap FE guide's own Follow-ups had
reserved "AI-authored rationale" for this slice. As a same-branch cleanup, the dead
`roadmap.replan` job enqueue at assessment-complete (no handler was ever registered for it) was
also removed.

Commits (branch `feat/roadmap-replan-rationale`, off `develop`), oldest to newest:
`dac1f71` (`rationale` column + migration `0028`) → `3240b8e` (rationale prompt builder) →
`78d8712` (`apply_replan` writes templated rationale + enqueues `ai.roadmap.rationale`) →
`4b60ed5` (`ai.roadmap.rationale` worker overwrites the rationale) → `6d0a1d8` (remove the dead
`roadmap.replan` job enqueue) → `c277c98` (live e2e + captures). PR to `develop` to follow.

## Why

Module 05 Slice 3's own SOP (`docs/sop/2026-08-26-roadmap-replan.md`, Follow-ups) shipped
`reason`/`summary` as templated strings and explicitly deferred "AI-authored re-plan rationale" to
Module 03, "same deferral shape as Assessment's narrative and Health Score's recommendation copy."
Module 03's own SOPs (Slices 1–5) have been tracking a running list of deferred consumers — after
Slice 5 (dashboard AI briefing), only two remained: the roadmap re-plan rationale and the
onboarding AI panel. This slice closes the roadmap one, reusing the free-text LLM seam
(`LLMClient.complete`) Slice 1 built rather than inventing anything new.

Separately, `app/services/assessment/service.py` had enqueued a `roadmap.replan` job on every
assessment completion since Module 07 shipped, and **no handler was ever registered to consume
it** — every such enqueued job failed on claim (unregistered job type). It has no relationship to this feature (an
assessment-triggered auto-replan never applies any changes, so no `RoadmapReplan` row exists at
that point), but it was flagged as dead code during this slice's design pass and removed in the
same branch rather than filed as a separate follow-up.

## How

**Same async-upgrade pattern as Module 03 Slices 1–5, applied to a new column on an existing
table.** `apply_replan` (`app/services/roadmap/replan.py`) already builds a `summary` string and
a JSONB `changes` snapshot when it writes a `RoadmapReplan` row; this slice adds one more
synchronous write (`rationale`) and one more enqueue (`ai.roadmap.rationale`) to that same code
path — no new endpoint, no new trigger.

**New nullable `rationale` Text column + migration `0028_roadmap_replan_rationale`.** Chains off
`0027_daily_briefings` (Slice 5's migration, the sole prior head) — `RoadmapReplan.rationale:
Mapped[str | None]`. Nullable because (a) pre-existing rows predate the column and get no
backfill, and (b) an all-stale/empty apply writes no `RoadmapReplan` row at all, same as before.

**`_templated_rationale(summary, snapshot)` (`replan.py`) is the instant value and the permanent
fallback**, mirroring every prior Module 03 slice's templated-first/AI-upgrade-second shape: it's
deterministic prose built from the change count plus up to three shifted milestone titles (`"...
and N more"` beyond that), with no LLM call and no failure mode of its own. `apply_replan` writes
it into the new `RoadmapReplan` row in the same transaction that writes `summary`/`changes`, then
calls `job_dispatcher.enqueue(db, "ai.roadmap.rationale", {"replan_id": ...}, ...)`.

**`handle_roadmap_rationale` (`app/worker/handlers/ai.py`) overwrites, it doesn't append.** It
re-fetches the `RoadmapReplan` row by id (benign no-op if missing), builds a PII-free prompt from
the row's own `changes` snapshot plus the roadmap's startup/stage via
`build_roadmap_rationale_messages` (`app/services/roadmap/ai_rationale.py`, new), calls
`get_llm_client().complete(...)` — **`complete()`, not `complete_json`** — and writes the
stripped result straight over `rationale`. `db.flush()` only, no commit/rollback, same
runner-owns-the-transaction convention as every other handler in this file.

**Free-text `complete()` was chosen over structured `complete_json`, deliberately.** A holistic
"why did this happen" paragraph is prose, not a fixed set of typed fields — the same reasoning
Module 08's AI Business Plan Generator (§08.11) used to pick `complete()` over `complete_json` for
its own free-text sections. `complete_json`'s schema-constrained mode (Slice 2) remains the right
tool for a field-shaped output (canvases, typed records, mission reasons), not for a single prose
paragraph.

**The critical, easy-to-miss behavior: the `apply` response's `rationale` is templated, never
AI-authored.** `POST /replan/apply` returns synchronously, before the `ai.roadmap.rationale` job
has had any chance to run — so `data.rationale` in that response is always the templated fallback,
by construction, not a race that sometimes wins. The AI-authored text only appears on a later
`GET /replan/history` read, once a worker has drained the job. The FE guide (below) calls this out
explicitly so the FE doesn't treat the apply response's `rationale` as already AI-authored.

**Dead `roadmap.replan` enqueue removed at `app/services/assessment/service.py`** (the line
directly above the `ai.assessment.narrative` enqueue it sat next to since Module 03 Slice 1).
Assessment completion still enqueues `ai.assessment.narrative` and `ai.health.recommendations`;
`roadmap.replan` is no longer enqueued anywhere in the codebase. `tests/api/assessment/
test_complete.py` and `tests/services/assessment/test_complete_concurrency.py` (including its
module docstring, which named `roadmap.replan` explicitly) were updated to assert the new
completion-job set (`{"ai.assessment.narrative", "ai.health.recommendations"}`) and to positively
assert `roadmap.replan` is no longer enqueued, rather than just dropping the old assertion.

**PII-free prompt, matching every other Module 03 consumer's convention.**
`build_roadmap_rationale_messages(*, stage, name, industry, changes)` sends only the startup's
name/industry/stage and the shifted milestones' titles/old-due/new-due/reason — no founder PII,
no task-level detail beyond what a milestone title already exposes.

## What's involved

**Migration `0028_roadmap_replan_rationale`** (chains off `0027_daily_briefings`, sole alembic
head) — autogenerated, hand-edited only for the revision id header:
- `roadmap_replans.rationale` — `Text`, nullable, no default, no index.

**Model**
- `app/db/models/roadmap.py::RoadmapReplan.rationale` (new) — `Mapped[str | None]`.

**Prompt builder**
- `app/services/roadmap/ai_rationale.py` (new) — `build_roadmap_rationale_messages(*, stage, name,
  industry, changes) -> list[LLMMessage]`. PII-free; asks for a short (2–3 sentence) reassuring,
  concrete rationale.

**Service wiring** (`app/services/roadmap/replan.py`)
- `_templated_rationale(summary, snapshot) -> str` (new) — deterministic instant fallback.
- `apply_replan` — writes `rationale` on the new `RoadmapReplan` row, enqueues
  `ai.roadmap.rationale` with `{"replan_id": ...}`, returns `rationale` in its result dict
  alongside the pre-existing `applied`/`skipped`/`replan_id`/`summary` keys.

**Worker handler** (`app/worker/handlers/ai.py`)
- `handle_roadmap_rationale(db, job)` (new, ~`ai.py:259-277`) — no-op guard (missing replan row)
  → `build_roadmap_rationale_messages` → `get_llm_client().complete(...)` → overwrite
  `rationale` → `db.flush()`. Registered as `"ai.roadmap.rationale"`.

**API surface** (`app/api/v1/endpoints/roadmap.py`)
- `GET /replan/history` (`replan_history`, ~line 704) — response items gain `"rationale":
  r.rationale`. No new route, no status-code change.

**Dead-code removal** (`app/services/assessment/service.py`)
- Deleted `job_dispatcher.enqueue(db, "roadmap.replan", job_payload, startup.id)`. Completion still
  enqueues `ai.assessment.narrative` and `ai.health.recommendations`.

**Tests**
- `tests/db/test_roadmap_models.py` — `rationale` round-trip + nullable.
- `tests/services/roadmap/test_ai_rationale.py` (new) — builder is PII-free, includes change
  context, handles an empty `changes` list.
- `tests/services/test_roadmap_replan_apply.py` — applying a real drift change writes a non-null
  templated `rationale` and enqueues exactly one `ai.roadmap.rationale`; an all-stale/empty apply
  enqueues none.
- `tests/worker/test_roadmap_rationale_handler.py` (new) — fills `rationale` from a `RoadmapReplan`
  row, stub marks it `[stub-llm] ...`, missing-row no-op, LLM error fails loud.
- `tests/api/assessment/test_complete.py`, `tests/services/assessment/test_complete_concurrency.py`
  — updated to assert the new completion-job set and that `roadmap.replan` is no longer enqueued.
- `e2e/test_roadmap_replan_rationale.py` (new) — full live journey, see Verification.

**Errors / API surface — none new.** `GET /replan/history` and `POST /replan/apply` are the same
pre-existing routes; this slice only adds one field to each, never a new status code or envelope
shape.

## Verification

**Per-task unit verification (green before the e2e task):**
- Task 1 (column + migration) — `tests/db/test_roadmap_models.py` — 3 passed;
  `poetry run alembic heads` → single head `0028_roadmap_replan_rationale`; `alembic check` →
  `No new upgrade operations detected.`
- Task 2 (prompt builder) — `tests/services/roadmap/test_ai_rationale.py` — 2 passed.
- Task 3 (`apply_replan` wiring) — new apply-enqueue tests — 12 passed; full roadmap suite re-run
  as a safety net — 93 passed.
- Task 4 (worker handler) — `tests/worker/test_roadmap_rationale_handler.py` — 4 passed; full
  `tests/worker/` suite re-run — 58 passed (no regression).
- Task 5 (dead-job removal) — `tests/api/assessment/` + `tests/services/assessment/` suites —
  59 passed (includes the two updated tests, plus a new positive assertion that `roadmap.replan`
  is not enqueued).
- Each task's own `black --check` / `isort --check-only` / `ruff check` / `mypy app` passed before
  merge to this branch, against the project's poetry-managed toolchain versions.

**Full non-e2e suite (re-verified for this docs task):** `poetry run pytest -q --no-cov` →
**1,311 passed**, no regression. `poetry run alembic heads` → single head
`0028_roadmap_replan_rationale`; `poetry run alembic check` → no drift.

**Live e2e (Task 6) — `scripts/e2e_run.sh`, full suite:**

```
e2e/test_roadmap_replan_rationale.py::test_roadmap_replan_rationale PASSED
...
50 passed in 36.59s
```

All 50 e2e tests pass (49 pre-existing + 1 new), confirming no regression. The new test walks:
sign up → onboard (stage `validation`) → `PATCH` a milestone's `due_on` 10 days into the past to
force a slip → `POST /replan/preview` → `POST /replan/apply` (asserts `data.rationale` is the
**templated** fallback, captures `apply.json`) → drain the worker in-process → `GET
/replan/history` (asserts the newest row's `rationale` is the `[stub-llm]`-marked AI-authored
text, captures `history_after_drain.json`).

This is the full CI-relevant reproduction for the app-code tasks (1–6); this task (7) is docs-only
and introduces no `app`/`test` changes.

## Operate / roll back

**New deploy-time requirement: none.** The `ai.roadmap.rationale` job type runs inside the
existing `worker` process (Module 20 Slice 2) — no new container, no new health check, no new
config beyond what Module 03 Slice 1 already introduced
(`LLM_PROVIDER`/`LLM_API_KEY`/`LLM_MODEL`/`LLM_BASE_URL`/`LLM_TIMEOUT`/`LLM_MAX_TOKENS`).

**Rollback:** revert this slice's commits as a unit (`dac1f71..c277c98`, plus this docs commit)
and downgrade the migration (`poetry run alembic downgrade 0027_daily_briefings`) to drop
`roadmap_replans.rationale`. Rolling back the migration is safe — no other feature reads that
column. If the code is reverted without downgrading the migration, `apply_replan`/`GET
/replan/history` simply stop writing/reading a column that still exists but is no longer
referenced; if the migration is reverted first, in front of not-yet-rolled-back code, the next
`apply`/`history` call raises (the column no longer exists) — **downgrade the migration only
after the app code is already rolled back**, not before. Reverting `6d0a1d8` alone (restoring the
`roadmap.replan` enqueue) is independently safe and requires no migration change — it's a
one-line addition with no schema dependency.

## Follow-ups

**Only one Module-03-deferred AI consumer remains: the onboarding AI panel.** This slice closes
the fifth of six named across Modules 02/04/05/06/08's own SOPs (the last unconsumed `ai_fill` job
closed in Slice 3; mission reason + health recommendations closed in Slice 4; dashboard briefing
closed in Slice 5; roadmap re-plan rationale closes here). Module 03 stays **open** until the
onboarding AI panel ships. It needs its own prompt/schema/trigger design, not a mechanical copy of
this slice's pattern (there's no existing "apply" moment to piggyback on the way this slice
piggybacked on `apply_replan`).

**Per-change `reason` strings are still templated, not AI-upgraded.** This slice deliberately adds
a holistic record-level `rationale` rather than rewriting each `changes[]` entry's own `reason`
line — upgrading those individually (own-slip / cascade / both) is a separate, smaller-grained
follow-up, not started here.

**The `roadmap.replan` job type itself is gone, not repurposed.** Removing its dead enqueue closes
the "no handler, fails on claim" gap, but it does not revisit the original product idea an
auto-drift advisory notification might have used that job type for — Module 05 Slice 3's own SOP
had already ruled out ever auto-draining it "by design," and this slice doesn't change that
verdict, it just stops writing rows nobody will ever read. Repurposing the job type for a
different, real feature is a separate product/design pass.

**No structured "AI enrichment failed / still templated" signal.** Same class of gap every prior
Module 03 slice has flagged: if the `ai.roadmap.rationale` job's retries are exhausted, `rationale`
simply stays on the templated value forever, with nothing telling the FE or an operator that the
upgrade never landed.

**Stub-only proof of the happy path.** The live e2e proves the single-replan, single-LLM-call path
against the stub provider (`StubLLMClient`). Multi-change re-plans (more than 3 shifted
milestones, exercising the `_templated_rationale` "and N more" branch) are unit-tested only, not
proven live.

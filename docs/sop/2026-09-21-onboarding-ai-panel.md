# SOP — Onboarding AI Panel (Module 03, last core AI consumer)

**What shipped** — the onboarding flow's calibration panel (a short "AI co-founder" welcome
message shown once the founder's industry, stage, and goals are all set) now has a real backing
field. `StartupProfile.ai_panel` (nullable `Text`) is written **synchronously with a templated
value** the moment `PATCH /onboarding/state` completes the last of the three signals — so it is
never a spinner, always real copy — and a new `ai.onboarding.panel` worker job then overwrites it
with LLM prose within seconds. `GET`/`PATCH /onboarding/state` both expose the current value flat
under `data.ai_panel` (they share one `serialize_state` body). This is a **one-shot, generate-once**
trigger: once `ai_panel` is non-null it is never touched again by this code path. As a same-branch
cleanup, the dead, never-consumed `AIPanel`/`StubAIPanel` platform seam (`app/platform/ai.py`) was
retired — its wording lives on as the new `_templated_panel()` helper.

**This closes the Module 03 AI-consumer set** — the sixth and last of the AI-authored consumers
Module 03 was scoped to deliver, shipped across seven build slices (assessment narrative,
canvas/records `ai_fill`, mission reason + health recommendations, dashboard briefing, roadmap
rationale, and now the onboarding panel). See Follow-ups for what "closes the consumer set" does
and does not mean for Module 03's overall status.

Commits (branch `feat/onboarding-ai-panel`, off `develop`), oldest to newest:
`bba01ff` (`ai_panel` column + migration `0029`) → `6d297fe` (prompt builder + templated fallback)
→ `1ed6a40` (generate-on-signals-complete trigger + `serialize_state` exposure) → `a70fd42`
(`ai.onboarding.panel` worker) → `2ca17a8` (remove the dead `app/platform/ai.py` seam) → `a1ff692`
(live e2e + captures) → `e4e8c41` (FE guide, SOP, checklist). Merged into `develop` via **PR #92**
(merge commit `30d7ed2`).

## Why

Module 01.6's own onboarding SOP shipped the calibration panel as a `501`/stub-free but
AI-unbacked concept and explicitly deferred it to Module 03 (`docs/checklist/PROJECT_CHECKLIST.md`,
Module 01.6's Deferred line: "onboarding AI panel (Module 03)"). Module 03's own running list of
deferred consumers (tracked across Slices 1–6's SOPs) named it as the last one standing after the
roadmap re-plan rationale shipped (Slice 6, `docs/sop/2026-09-21-roadmap-replan-rationale.md`,
Follow-ups: "Only one Module-03-deferred AI consumer remains: the onboarding AI panel"). Unlike
every prior slice, there was no existing "apply"-style write path to piggyback an extra column
onto — onboarding's `apply_step` already existed, but nothing about it previously touched an AI
field, so this slice designs its own trigger condition from scratch (see How).

Separately, the original `app/platform/ai.py` (`AIPanel` Protocol + `StubAIPanel`, from the
Foundation/Tenancy spine's original platform-seams pass) had sat unconsumed since day one — no
endpoint, worker, or service ever called it. It was flagged as dead code during this slice's design
pass (the real implementation the slice needed looks nothing like that seam's shape — no
LLM-provider abstraction is needed at the platform layer, `app/platform/llm.py`'s existing
`LLMClient` already covers it) and retired in the same branch rather than filed as a separate
follow-up.

## How + key decisions

**New nullable `ai_panel` Text column on `StartupProfile` + migration
`0029_startup_profile_ai_panel`.** Chains off `0028_roadmap_replan_rationale` (Slice 6's migration,
sole prior head). Nullable because (a) pre-existing workspaces that already finished onboarding get
no backfill, and (b) a workspace can sit indefinitely below step 4 without ever setting all three
signals.

**One-shot generate-once trigger lives in `apply_step`, gated on "was null."**
`_maybe_generate_ai_panel(db, startup)` (`app/services/onboarding/steps.py`) runs at the end of
every `apply_step` call (i.e. every `PATCH /onboarding/state`). It no-ops immediately if
`profile.ai_panel is not None` (already generated — this is what makes it one-shot, not a
"regenerate on every edit" trigger) or if industry/stage/goals aren't *all* truthy yet. Only when
both guards pass does it write the templated value and enqueue the job — so a `PATCH` that merely
edits, say, the startup name after the panel already exists is a complete no-op for this feature,
and a `PATCH` that sets the third-of-three signals is the one and only request that ever writes or
enqueues anything.

**Write templated → enqueue, in that order, same transaction.** Mirrors every prior Module 03
slice's templated-first/AI-upgrade-second shape: `_templated_panel(industry, stage)` is a pure,
deterministic string ("Got it — a {industry} startup at the {stage} stage. Let's calibrate your
workspace.") with no LLM call and no failure mode of its own, written via `db.flush()` (the caller —
the `PATCH /onboarding/state` endpoint — still owns the commit), then
`job_dispatcher.enqueue(db, "ai.onboarding.panel", {"startup_id": ...}, startup.id)`.

**`handle_onboarding_panel` (`app/worker/handlers/ai.py`) overwrites, it doesn't append.** Re-fetches
the `Startup` by id (benign no-op if the startup or its profile is missing — covers a stale job
against a deleted workspace), builds a PII-free prompt via `build_onboarding_panel_messages`
(`app/services/onboarding/ai_panel.py`, new), calls `get_llm_client().complete(...)` — **`complete()`,
not `complete_json`** — and writes the stripped result straight over `ai_panel`. `db.flush()` only,
no commit/rollback, same runner-owns-the-transaction convention as every other handler in this file.

**Free-text `complete()` chosen over structured `complete_json`, same reasoning as every
prose-shaped Module 03 consumer.** A 2–3 sentence calibration greeting is prose, not a fixed set of
typed fields — the same choice §08.11's plan generator and Slice 6's roadmap rationale both made.
`complete_json`'s schema-constrained mode remains the right tool for field-shaped output (canvases,
typed records, mission reasons), not a single conversational paragraph.

**PII-free prompt, matching every other Module 03 consumer's convention.**
`build_onboarding_panel_messages(*, industry, stage, goals)` sends only the startup's
industry/stage/goals — no founder name, role, country, phone, or `how_heard`, even though all of
those are readily available on the same `apply_step` call.

**Dead `app/platform/ai.py` seam retired** (`AIPanel` Protocol + `StubAIPanel` + its test file
`tests/platform/test_ai.py`) — confirmed zero consumers repo-wide via
`grep -rn 'app.platform.ai\|from app.platform import ai\|StubAIPanel\|AIPanel' app tests e2e`
before deletion. No functionality was lost: the seam's templated wording was already ported into
`_templated_panel()` during this same slice's Task 2, before the seam was deleted in Task 5.

## What's involved

**Migration `0029_startup_profile_ai_panel`** (chains off `0028_roadmap_replan_rationale`, sole
alembic head) — autogenerated, hand-edited only for the revision id header:
- `startup_profiles.ai_panel` — `Text`, nullable, no default, no index.

**Model**
- `app/db/models/startup.py::StartupProfile.ai_panel` (new) — `Mapped[str | None]`.

**Prompt builder + templated fallback**
- `app/services/onboarding/ai_panel.py` (new) — `_templated_panel(industry, stage) -> str`
  (deterministic instant value) · `build_onboarding_panel_messages(*, industry, stage, goals) ->
  list[LLMMessage]` — PII-free, asks for a short (2–3 sentence) warm, concrete calibration message.

**Trigger + state exposure** (`app/services/onboarding/`)
- `steps.py::_maybe_generate_ai_panel(db, startup)` (new) — the one-shot generate-once guard,
  called at the end of `apply_step`.
- `workspace.py::serialize_state` — gains `"ai_panel": profile.ai_panel` next to `"goals"`. Shared
  by both `GET` and `PATCH /onboarding/state`, so the field is flat and identically shaped on both
  routes — no field-nesting asymmetry, unlike the assessment narrative.

**Worker handler** (`app/worker/handlers/ai.py`)
- `handle_onboarding_panel(db, job)` (new, ~line 284) — no-op guard (missing startup/profile) →
  `build_onboarding_panel_messages` → `get_llm_client().complete(...)` → overwrite `ai_panel` →
  `db.flush()`. Registered as `"ai.onboarding.panel"`.

**Dead-code removal**
- Deleted `app/platform/ai.py` (`AIPanel` Protocol, `StubAIPanel`, singleton) and
  `tests/platform/test_ai.py` — 2 files, 23 lines removed, zero remaining references.

**API surface — none new.** `GET`/`PATCH /onboarding/state` are the same pre-existing routes; this
slice only adds one field to their shared response body, never a new route or status code.

**Tests**
- `tests/db/test_startup_profile_ai_panel.py` (new) — column round-trip + nullable default.
- `tests/services/onboarding/test_ai_panel_builder.py` (new) — templated wording is exact, the
  message builder is PII-free and includes signals, handles missing/`None` signals without crashing.
- `tests/services/onboarding/test_ai_panel_trigger.py` (new) — generates + enqueues exactly once
  when signals complete, no enqueue when signals are incomplete, `serialize_state` includes
  `ai_panel`.
- `tests/worker/test_onboarding_panel_handler.py` (new) — overwrites `ai_panel` via a fake LLM,
  no-op on a missing startup, `StubLLMClient` marks the panel with `[stub-llm]`, fails loud
  (`RuntimeError`) when `LLM_PROVIDER=openai` with an empty API key.
- `e2e/test_onboarding_ai_panel.py` (new) — full live journey, see Verification.

## Verification

**Per-task unit verification (green before the e2e task):**
- Task 1 (column + migration) — `tests/db/test_startup_profile_ai_panel.py` — 1 passed;
  `poetry run alembic heads` → single head `0029_startup_profile_ai_panel`; `alembic check` →
  `No new upgrade operations detected.`
- Task 2 (prompt builder) — `tests/services/onboarding/test_ai_panel_builder.py` — 3 passed, 100%
  module coverage.
- Task 3 (trigger + state exposure) — `tests/services/onboarding/ tests/api/onboarding/` — 37
  passed (no regression against pre-existing onboarding suites).
- Task 4 (worker handler) — `tests/worker/test_onboarding_panel_handler.py` — 4 passed; full
  `tests/worker/` suite re-run — 62 passed (no regression).
- Task 5 (dead-seam removal) — `tests/platform/ tests/worker/ tests/services/onboarding/` — 120
  passed, no import errors.
- Each task's own `black --check` / `isort --check-only` / `ruff check` / `mypy app` passed before
  merge to this branch, against the project's poetry-managed toolchain versions.

**Live e2e (Task 6) — `scripts/e2e_run.sh`, full suite:**

```
e2e/test_onboarding_ai_panel.py::test_onboarding_ai_panel PASSED
...
51 passed in 37.02s
```

All 51 e2e tests pass (50 pre-existing + 1 new), confirming no regression — including
`e2e/test_onboarding.py` and `e2e/test_records_ai_fill.py`. The new test walks: sign up → `PATCH`
industry/stage/goals (the completing `PATCH` returns the templated `ai_panel`, asserted non-null
and NOT containing `[stub-llm]`, capture `state_templated.json`) → drain the worker in-process →
`GET /onboarding/state` (asserts `ai_panel` now contains `[stub-llm]`, capture
`state_after_drain.json`), over real HTTP.

This is the full CI-relevant reproduction for the app-code tasks (1–6); this task (7) is docs-only
and introduces no `app`/`test` changes.

**Migration round-trip / drift:** `poetry run alembic upgrade head` (0028 → 0029) succeeded;
`poetry run alembic check` → no drift; `poetry run alembic heads` → single head
`0029_startup_profile_ai_panel`.

## Operate / roll back

**New deploy-time requirement: none.** The `ai.onboarding.panel` job type runs inside the existing
`worker` process (Module 20 Slice 2) — no new container, no new health check, no new config beyond
what Module 03 Slice 1 already introduced
(`LLM_PROVIDER`/`LLM_API_KEY`/`LLM_MODEL`/`LLM_BASE_URL`/`LLM_TIMEOUT`/`LLM_MAX_TOKENS`).

**Rollback:** revert this slice's commits as a unit (`bba01ff..a1ff692`, plus this docs commit) and
downgrade the migration (`poetry run alembic downgrade 0028_roadmap_replan_rationale`) to drop
`startup_profiles.ai_panel`. Rolling back the migration is safe — no other feature reads that
column. If the code is reverted without downgrading the migration, `apply_step`/`serialize_state`
simply stop writing/reading a column that still exists but is no longer referenced; if the
migration is reverted first, in front of not-yet-rolled-back code, the next `PATCH
/onboarding/state` call raises (the column no longer exists) — **downgrade the migration only
after the app code is already rolled back**, not before. Reverting `2ca17a8` alone (restoring the
dead `app/platform/ai.py` seam) is independently safe and requires no migration change — it was
already unconsumed before this slice touched it.

## Follow-ups

**This closes Module 03's AI-consumer set — it does not close Module 03 itself.** All six core
consumers named across Modules 01.6/02/04/05/06/07/08's own SOPs are now built, across the seven
build slices: assessment narrative (Slice 1), canvas/records `ai_fill` (Slices 2–3), mission reason
+ health recommendations (Slice 4), dashboard briefing (Slice 5), roadmap re-plan rationale
(Slice 6), and now the onboarding panel (Slice 7, this one). Module 03's own checklist section
(Slice 1's Deferred
list) still separately and explicitly carries **no Anthropic (or other non-OpenAI-compatible)
provider implementation** and **no per-workspace LLM budget/rate limiting** as unbuilt, in-scope
infrastructure — neither is a consumer gap, but both remain open items under Module 03 as the
checklist itself defines it, so Module 03 stays **open** for that reason alone, not for any missing
AI-authored field. See `docs/checklist/PROJECT_CHECKLIST.md` for the reconciled framing.

**No regenerate-on-edit.** If a founder changes industry/stage/goals after the panel has already
been generated once, `ai_panel` does not update — the one-shot guard (`profile.ai_panel is not
None`) is permanent, by design, for this slice. A "recalibrate" trigger (either automatic on
signal change, or an explicit founder-facing action) is a real, separate follow-up, not started
here.

**No interactive chat panel.** This slice ships a single, one-time, non-conversational greeting —
not the two-way "AI co-founder chat" the panel's name evokes. Turning it into an actual
conversation (multi-turn, founder-initiated follow-up questions) is a materially larger feature,
out of scope here.

**No structured "AI enrichment failed / still templated" signal.** Same class of gap every prior
Module 03 slice has flagged: if the `ai.onboarding.panel` job's retries are exhausted, `ai_panel`
simply stays on the templated value forever, with nothing telling the FE or an operator that the
upgrade never landed.

**Stub-only proof of the happy path.** The live e2e proves the single-panel, single-LLM-call path
against the stub provider (`StubLLMClient`). The no-op paths (missing startup, LLM failure) are
unit-tested only, not proven live — same class of gap as every prior slice's e2e, and for the same
reason (forcing those conditions live would either break the shared e2e process's
`LLM_PROVIDER=stub` guarantee or require fabricating an orphaned job row).

# SOP — Two Module 03 Deferred AI Upgrades (Learning Shelf Reason + Journal Prompt)

**What shipped** — two independent, small AI-upgrade slices closing the last two named
Module-03-deferred AI consumers: the Learning Academy's (Module 17) shelf-level
`recommendation_reason` on `GET /learning/recommendations`, and the Founder Journal's
(Module 21) daily `prompt` on `GET /journal/prompts/today`. Both follow the same
lazy-generate-then-upgrade pattern every other Module 03 slice established (dashboard briefing,
mission reason, health recommendations, roadmap rationale, onboarding panel): a templated/static
value is returned synchronously on first read, a background job then overwrites it in place with
LLM-authored text, and the FE picks up the upgrade on its next poll of the same endpoint — no new
endpoint, no new field, no status code change.

Commits (branch `feat/module-03-deferred-ai-upgrades`, off `develop`; not yet merged, no PR
opened), oldest to newest:
`00c9e73` (design spec) → `c6a7195` (implementation plan) →
`5035f56` (`learning_recommendations` table + `EnrichmentStatus` enum, migration `0031`) →
`d74ae21` (learning AI reason prompt builder) →
`2cc1e35` (serve `recommendation_reason` with lazy generating→ready upsert) →
`2fb6501` (`ai.learning.recommendations` worker) →
`f9f9b01` (`journal_prompts` table, migration `0032`) →
`2a08bfc` (operational-only prompt context gatherer + AI prompt builder) →
`d54531b` (lazy generating→ready upsert for today's prompt) →
`51551b7` (unrelated pre-existing test fix, restored a delete-count assertion) →
`1797088` (`ai.journal.prompt` worker) →
`699593a` (live e2e for both + captures) → this docs commit.

## Why

Module 03's own SOP (`docs/sop/2026-09-21-llm-budget-ai-status.md`) and the checklist's
"Module 03 COMPLETE" reconcile (2026-09-21) both named this API's set of AI consumers as fully
shipped. Separately, two existing static fields — the Learning Academy's recommendation shelf
(Module 17, `PR #59`, shipped with a purely deterministic
`"Recommended for your {stage} stage."` line) and the Founder Journal's daily prompt (Module 21,
`PR #37`, shipped with a fixed 7-line rotating pool, no personalization) — had always been
flagged in their own design docs as candidates for the same AI-upgrade treatment once the LLM
seam existed. This slice closes both, using infrastructure that already exists (the LLM seam,
`job_dispatcher`, `metered_complete_json`, the per-workspace budget guard) rather than building
anything new.

## How

**Same lazy-generate-then-upgrade shape as the dashboard briefing and mission reason, reused
deliberately rather than reinvented.** Each feature gets one new table
(`learning_recommendations`, `journal_prompts`), each keyed so there is exactly one live row per
subject (one per startup for the shelf reason; one per `(startup, founder, date)` for the daily
prompt — a founder-private field, matching the journal's founder-only access model). The first
read of the relevant endpoint calls a `get_or_create_*` service function that: checks for an
existing row; if absent, writes one with a templated/static value and `status = generating` inside
`db.begin_nested()` (a `SAVEPOINT`), enqueues the matching `ai.*` job, and returns the just-created
row; on `IntegrityError` (two concurrent first-reads racing the unique constraint), re-selects the
now-committed winner's row instead of erroring — the exact same race-guard shape as
`get_or_create_enrollment` (`app/services/learning/service.py`) and `get_or_generate_briefing`
(Module 03 Slice 5).

**The worker handlers are idempotent by construction, via the `status` column itself** — both
`handle_learning_recommendations` and `handle_journal_prompt`
(`app/worker/handlers/ai.py:351`, `:386`) re-fetch their row and no-op unless it is still
`generating`. A duplicate/retried job against an already-`ready` row, or a row that's since been
deleted, is left untouched. No separate guard column, no comparison against a catalog default —
same simplification the dashboard briefing's SOP already used for the same reason (there's no
pre-existing "default" text to compare against; `status` itself is the signal).

**Both handlers keep the fallback value and flip straight to `ready` (never `failed`) when there
is nothing to personalize, rather than calling the LLM pointlessly.** Learning: if the startup has
no `stage` or the stage yields no recommendable course titles, `recommendation_reason` stays on
its generic templated line — the row is marked `ready` with no LLM call made at all. Journal: if
`gather_prompt_context` (`app/services/journal/ai_prompt.py`) finds neither a shipped roadmap
milestone nor a mission focus task, the static prompt stays as-is, same no-LLM-call fast path.
Over-budget (`metered_complete_json` returning `None`, Module 03's `LLM_DAILY_TOKEN_BUDGET` guard)
takes the *other* branch — the row is left on `generating` rather than flipped to `ready`, so a
later drain (after the budget resets) can still pick it up and try again; see Follow-ups for the
gap this leaves.

**The journal prompt is deliberately grounded in operational signals only — never diary content
or mood.** This is the one design decision in this slice that isn't a mechanical copy of an
existing pattern: a naive implementation could have fed the founder's own journal entries or mood
history into the prompt ("you seemed stressed yesterday — want to talk about it?"), which would
have been a meaningful privacy regression for what the journal's own FE guide already calls out as
"private to them, not shared across the workspace." Instead,
`app/services/journal/ai_prompt.py::gather_prompt_context` reads only two things — the founder's
most recently *shipped* (`status: done`) roadmap milestone, and the current mission's first
(lowest-`order`) task title — and the module carries an explicit privacy comment at its top: *"this
module reads ONLY operational signals ... It must never import `JournalEntry` or `MoodLog`."*
Enforced today by a data-level unit test (`test_privacy_journal_and_mood_never_surface`, seeds a
diary entry with distinctive content and a mood/stress pair, then asserts neither string reaches
the built LLM messages) rather than a static-import guard; see Follow-ups.

**The learning shelf reason additionally regenerates in place on a stage change** — a behavior
neither of the two prior analogues (mission reason, dashboard briefing) needed, because a startup's
`stage` can change after onboarding (a later slice may add stage transitions) while a mission or a
daily briefing is naturally re-created fresh each day/mission-cycle.
`get_or_create_recommendation_reason` (`app/services/learning/service.py`) compares the stored
`row.stage` against the caller's current stage on every read; a mismatch resets `reason` to the
newly-templated line for the new stage, flips `status` back to `generating`, and re-enqueues —
so a stage change can never leave a stale AI reason for the old stage on screen.

**One correction to the plan text, made during implementation:** learning's shelf reason is
service-level per-startup, not per-user — `LearningRecommendation` has a plain unique constraint
on `startup_id` alone (`uq_learning_reco_startup`), matching how `recommended_courses` itself
already computes one shared shelf per startup+stage, not per team member. The journal prompt is
the opposite shape by design — unique on `(startup_id, founder_id, date)` — because the journal
itself is founder-private (see `docs/fe-integration-guide-journal.md`'s access-matrix section);
these are two genuinely different keying decisions, not an inconsistency.

## What's involved

**Migration `0031_learning_recommendations`** (chains off `0030_llm_usage_daily`) — new table
`learning_recommendations`: `id` (uuid PK), `startup_id` (uuid FK → `startups.id`, `ondelete=
"CASCADE"`, indexed), `stage` (string(32), nullable), `reason` (string(300), nullable),
`status` (non-native enum, `generating`/`ready`, length 12), `created_at`/`updated_at`
(timestamptz, server default `now()`); unique constraint `uq_learning_reco_startup` on
`startup_id` alone (one row per startup).

**Migration `0032_journal_prompts`** (chains off `0031_learning_recommendations`, sole alembic
head) — new table `journal_prompts`: `id` (uuid PK), `startup_id` (uuid FK → `startups.id`,
`ondelete="CASCADE"`, indexed), `founder_id` (uuid FK → `users.id`, `ondelete="CASCADE"`,
indexed), `date` (date), `prompt` (string(300), **not** nullable — always has at least the static
line), `status` (non-native enum, `generating`/`ready`, length 12), `created_at`/`updated_at`;
unique constraint `uq_journal_prompts_startup_founder_date` on `(startup_id, founder_id, date)`.

**Shared enum**
- `app/db/models/enums.py::EnrichmentStatus(enum.StrEnum)` (new, shared by both tables) —
  `generating` / `ready`. No `failed` member (contrast `BriefingStatus`, which has one, unused —
  see Follow-ups on both).

**Models**
- `app/db/models/learning.py::LearningRecommendation` (new) — mirrors migration `0031`.
- `app/db/models/journal.py::JournalPrompt` (new) — mirrors migration `0032`.
- `app/db/models/__init__.py` — both registered for `create_all`-built test DBs and Alembic
  autogenerate.

**Prompt builders + schemas**
- `app/services/learning/ai_reason.py` (new) — `learning_reason_schema()` (strict, single
  `reason` string) · `build_learning_reason_messages(*, stage, course_titles) -> list[LLMMessage]`
  (stage + shelf titles only, no PII).
- `app/services/journal/ai_prompt.py` (new) — `journal_prompt_schema()` (strict, single `prompt`
  string) · `gather_prompt_context(db, startup_id) -> tuple[str | None, str | None]` (operational
  signals only — see How) · `build_journal_prompt_messages(*, milestone_title, mission_focus) ->
  list[LLMMessage]`.

**Service wiring**
- `app/services/learning/service.py::_templated_reason`, `_enqueue_learning_reason`,
  `get_or_create_recommendation_reason` (new) — the lazy-create + stage-change-regenerate +
  enqueue-once + race guard described above.
- `app/services/journal/service.py::JournalService.get_or_create_today_prompt` (new, static
  method) — the lazy-create + enqueue-once + race guard, reusing the existing
  `JournalService.get_prompt` static-pool rotation for the templated seed value.

**Endpoints**
- `app/api/v1/endpoints/learning.py::get_recommendations` — now also calls
  `get_or_create_recommendation_reason` and adds `recommendation_reason` to the response
  alongside the pre-existing `stage`/`recommended`/`continue_watching` keys; `db.commit()` added
  (the lazy-create persists, mirroring `GET /canvases/{type}`'s existing convention).
- `app/api/v1/endpoints/journal.py::get_today_journal_prompt` — now calls
  `JournalService.get_or_create_today_prompt` instead of the bare static `JournalService.
  get_prompt()` call it replaced; response shape (`{"prompt": ...}`) is unchanged.

**Worker handlers** (`app/worker/handlers/ai.py`)
- `handle_learning_recommendations` (new, `:351-380`) — no-op guards (missing startup, missing
  row, row not `generating`) → no-signal fast path (`stage is None or not titles`) → `stage is
  not None` path calls `metered_complete_json` against `learning_reason_schema()` → overwrite
  `reason` (`[:300]`) + `status = ready`. Registered as `"ai.learning.recommendations"`.
- `handle_journal_prompt` (new, `:386-423`) — no-op guards (missing startup, missing founder,
  missing row, row not `generating`) → `gather_prompt_context` → no-signal fast path (both
  `None`) → `metered_complete_json` against `journal_prompt_schema()` → overwrite `prompt`
  (`[:300]`) + `status = ready`. Registered as `"ai.journal.prompt"`. Both `db.flush()` only —
  the job runner owns the transaction, same convention as every other handler in this file.

**Tests**
- `tests/db/test_learning_models.py` (8), `tests/db/test_journal_models.py` (9) +
  `tests/test_journal_migration.py` (2) — round-trip + unique-constraint races + single alembic
  head, no drift.
- `tests/services/learning/test_reco_reason.py` (4) — first-call creates + enqueues,
  second-call returns existing (no duplicate job), stage-change regenerates + re-enqueues,
  `stage: None` generic fallback.
- `tests/services/journal/test_ai_prompt.py` (5) — schema shape, context gathering (with and
  without signals), and the privacy test (`test_privacy_journal_and_mood_never_surface`).
- `tests/services/journal/test_prompt_upsert.py` (2) — lazy-create + idempotent re-read.
- `tests/worker/test_learning_reason_handler.py` (7), `tests/worker/test_journal_prompt_handler.py`
  (7) — fill-generating-row, no-op-already-ready, no-op-missing-row/startup/founder, no-signal
  fast path keeps fallback with no LLM call, stub marks fields, fails loud on LLM error.
- `e2e/test_learning.py::test_learning_journey`, `e2e/test_journal.py::test_journal_journey`
  (extended, not new files) — full live journeys draining the worker in-process; see
  Verification.

**Errors / API surface — none new.** Both endpoints are pre-existing routes; this slice only
changes what value one field holds a few seconds after a read, never a status code or an
envelope shape.

**Docs**
- `docs/fe-integration-guide-learning-recommendations.md` (new — no guide previously existed for
  any part of the Learning Academy module).
- `docs/fe-integration-guide-journal.md` (extended — §1's daily-prompt section now carries the
  AI-upgrade lifecycle and real captures; see that guide's own provenance note for the
  pre-existing, out-of-scope gap in its other sections).
- `docs/checklist/PROJECT_CHECKLIST.md` (reconciled — see below).

## Verification

**Per-task unit verification (green before the e2e task):**
- Learning: `tests/db/test_learning_models.py` (8 passed) → full suite **1347 passed** →
  `tests/services/learning/test_reco_reason.py` + `tests/api/test_learning.py` (91 passed
  combined) → full suite **1355 passed** → `tests/worker/test_learning_reason_handler.py`
  (7 passed) → full suite **1362 passed, 1 pre-existing warning**.
- Journal: `tests/db/test_journal_models.py` + `tests/test_journal_migration.py` (11 passed) →
  full suite **1363 passed** → `tests/services/journal/test_ai_prompt.py` (5 passed) → full suite
  **1368 passed** → `tests/services/journal/test_prompt_upsert.py` (2 passed) +
  `tests/api -k journal` (90 passed) → full suite **1371 passed** →
  `tests/worker/test_journal_prompt_handler.py` (7 passed) → full suite **1378 passed, 1
  pre-existing warning** (the same `StarletteDeprecationWarning` about `httpx`/
  `starlette.testclient` every other recent SOP in this repo notes — present before this
  branch).
- `poetry run ruff check` / `black --check` / `mypy app` clean on every touched file, each task.
- `alembic heads` — single linear head (`0032_journal_prompts`) confirmed after each migration
  task; no drift between the ORM models and the applied migrations.
- Re-ran the full suite at task 10 time (docs-only, no app/test change expected): **1378 passed,
  1 warning in 102s** — unchanged, confirming this docs pass introduced no regression.

**Live e2e (extended `e2e/test_learning.py`, `e2e/test_journal.py`) —
`bash scripts/e2e_run.sh`, full suite:**

```
52 passed in 37.99s
```

All 52 e2e tests pass (50 pre-existing + the 2 extended journeys, no new files), confirming no
regression. The learning journey: onboard (stage `validation`) → `GET /learning/recommendations`
(asserts `recommendation_reason` present, templated; captures `recommendations_before.json`) →
drain the worker in-process → re-fetch (asserts `"[stub-llm]"` in `recommendation_reason`;
captures `recommendations_reason.json`). The journal journey: onboard → `GET
/journal/prompts/today` (asserts the body is exactly `{"prompt": ...}`, static; captures
`prompts_today.json`) → seed a mission-focus signal (`GET /missions/today`, lazily generates
today's mission since this journey has no roadmap-milestone signal available) → drain the worker
in-process → re-fetch (asserts `"[stub-llm]"` in `prompt`; captures `prompt_today_ai.json`).

**Honest gap disclosure, decided live rather than assumed:** Task 9 explicitly checked whether
`scripts/e2e_run.sh` drains the job worker automatically (it does not — individual test files opt
in via a private `_drain()` helper, same pattern `e2e/test_dashboard_ai_briefing.py` already
uses) before capturing the "AI-upgraded" bodies, rather than assuming the drain would happen and
risking an un-exercised capture being presented as live-verified. Both AI paths were confirmed to
actually take the LLM branch (not the no-signal fallback) before capturing — see each FE guide's
own verification table for exactly which sub-behaviors remain unit-only vs. live-verified.

## Operate / roll back

**New deploy-time requirement: none.** Both `ai.learning.recommendations` and `ai.journal.prompt`
run inside the existing `worker` process (Module 20 Slice 2) — no new container, no new health
check, no new config beyond what Module 03 Slice 1 already introduced.

**Rollback:** revert this slice's commits as a unit (`00c9e73..699593a`, plus this docs commit)
and downgrade both migrations in reverse order (`poetry run alembic downgrade
0030_llm_usage_daily`) to drop `journal_prompts` and `learning_recommendations`. Rolling back is
safe — neither table holds data any other feature reads. As with the dashboard briefing's own
rollback note: downgrade the migrations only *after* the app code is already rolled back, not
before (reverting the migration first, in front of not-yet-rolled-back code, would make the next
`GET /learning/recommendations` or `GET /journal/prompts/today` for an affected startup raise —
the table would no longer exist under still-live code that expects it).

## Follow-ups

**No structured "AI generation failed" signal — same gap as every other Module 03 slice.**
Neither `EnrichmentStatus` has a `failed` member (unlike `BriefingStatus`, which has one but never
writes it either). An LLM hard-failure during either handler raises and relies on the job
runner's own retry/backoff; if retries exhaust, the row is left on `generating` indefinitely with
no distinct signal reaching the FE — this endpoint just keeps quietly serving the
templated/static fallback forever in that case, indistinguishable from "hasn't been picked up
yet." Tracked as an open Module-03-wide gap, not newly introduced here.

**Per-course reasons, not just a shelf-level one.** This slice's `recommendation_reason` explains
why the *shelf as a whole* was chosen, not why any individual recommended course fits — a richer
future iteration could add a one-line reason per course in the `recommended` array (mirroring
Today's Mission's per-task `reason`), which would need its own JSON-schema shape (array of
per-course reasons) and its own idempotency design, not a mechanical extension of this slice's
single-string schema.

**Weakest-dimension grounding for the learning shelf.** Today's `recommended_courses` filter and
the AI reason it feeds are grounded only in `stage` — a future iteration could ground the
recommendation (and its AI explanation) in the founder's weakest Health Score dimension (Module
06) instead of or in addition to stage, surfacing "you're behind on X, here's a course for that."
Not built here — would need its own design pass on how `learning_reason_schema`'s prompt inputs
change.

**Mood-aware journal prompts, behind explicit consent.** The privacy boundary this slice draws —
operational signals only, never journal content or mood — is a deliberate, permanent design
choice for the *default* behavior, not a temporary gap to close. A future opt-in feature could let
a founder explicitly consent to mood-aware prompting ("you've logged low mood 3 days running —
want to talk about it?"), but that must be a separate, clearly-consented feature with its own
privacy review — not something this slice should have (or does) do by default.

**No static-import privacy guard test.** The privacy boundary in
`app/services/journal/ai_prompt.py` is enforced today only by a *data-level* test
(`test_privacy_journal_and_mood_never_surface` — seeds `JournalEntry`/`MoodLog` rows with
distinctive content and asserts neither reaches the built LLM messages). It does not guard
against the weaker but still real regression of someone adding `from app.db.models.journal import
JournalEntry` to that module in a later change without necessarily using it to build the
prompt — the module's own docstring comment ("must never import `JournalEntry` or `MoodLog`") is
enforced by convention and code review, not by a test that would fail on the import alone. A
follow-up could add a small AST/static-analysis test asserting `app/services/journal/ai_prompt.py`
never imports either symbol, as a second, independent line of defense.

**`docs/fe-integration-guide-journal.md` §2–§7 remain schema-derived, not live-captured.** A
pre-existing gap (present since Module 21 shipped, `PR #37`) that this slice's docs pass did not
close — only §1 (today's prompt, including this slice's AI upgrade) was regenerated from the now-
available `e2e/_captures/journal/*.json`. The rest of that guide (entries CRUD, list/search, mood
trend, errors) still needs its own regenerate pass from the real captures that now exist. See that
guide's own provenance note.

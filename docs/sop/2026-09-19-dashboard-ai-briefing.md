# SOP — Dashboard AI Briefing (Module 03 Slice 5)

**What shipped** — the fourth Module-03-deferred AI consumer named across Modules 02/04/05/06's
own SOPs: the dashboard's `briefing`/`risks`/`opportunities` panel (previously a permanently
static `{status: "empty", message: "<fixed string>"}` triad) is now backed by a real, lazily
generated `daily_briefings` row. The first `GET /dashboard/summary` read of the day, for a
founder who has completed the kickoff assessment, creates a `generating`-status row and enqueues
an `ai.dashboard.briefing` job; a worker handler then calls the LLM's structured-output method
and flips the row to `ready` with three AI-authored paragraphs. A founder who hasn't completed
the assessment still sees the original static `"empty"` shape — nothing changed for that path.

Commits (branch `feat/dashboard-ai-briefing`, off `develop`), oldest to newest:
`8b6b33c` (`daily_briefings` model + migration `0027` + `BriefingStatus` enum) → `7021f9c` (AI
briefing prompt builder + JSON schema) → `1f5ea89` (lazy generation + `get_summary` wiring) →
`36faacc` (`ai.dashboard.briefing` worker handler) → `065cf8f` (live e2e + captures, plus a fix
to a now-stale assertion in the existing dashboard e2e) → `0e6bef1` (FE guide, SOP, checklist) →
`3c5d79a` (drop a stray unrelated `.env.example` edit). Merged into `develop` via **PR #87**
(merge commit `6f1a29c`).

## Why

Module 02's own SOP (`docs/sop/2026-08-31-dashboard.md`) shipped `briefing`/`risks`/
`opportunities` as an honest, permanently static placeholder, explicitly deferring the real
version to Module 03. Module 03 Slice 4's SOP (`docs/sop/2026-09-19-mission-health-ai.md`) later
named the dashboard AI briefing as one of three still-unbuilt Module-03-deferred consumers (the
other two — the onboarding AI panel and roadmap replan rationale — remain unbuilt; see
Follow-ups). This slice closes that one, reusing the same async-upgrade infrastructure Slices
1–4 already built (`LLMClient.complete_json`, `job_dispatcher`, the worker-runner-owns-the-
transaction convention) rather than inventing anything new.

## How

**Same async-upgrade pattern as Slices 2–4, but with a new table instead of overwriting an
existing column.** Unlike the mission-reason/health-recommendation upgrades (Slice 4), which
personalize a field that already existed, the dashboard briefing has no natural existing row to
overwrite — Module 02 never persisted `briefing`/`risks`/`opportunities` at all, it computed the
static strings on every read. So this slice adds a small `daily_briefings` table
(`app/db/models/dashboard.py::DailyBriefing`, migration `0027_daily_briefings`) keyed
`(startup_id, briefing_date)` and follows the now-familiar shape: a synchronous create-path
writes a `generating` placeholder immediately (so the FE always gets a `{status, message}` shape,
never a null gap), a job is enqueued once per row, and a worker handler fills the real text in
place.

**Lazy-on-read, gated on assessment-complete — not a cron.** `get_or_generate_briefing`
(`app/services/dashboard/service.py`) mirrors `get_or_generate_today`'s (Mission's) lazy-
generation shape almost exactly: check for today's existing row, return it if present; otherwise
create one and enqueue. The one gate this function adds that Mission's doesn't need is
`latest_completed_result(db, startup.id) is None` → return `None` before touching
`DailyBriefing` at all. A founder who hasn't completed the kickoff assessment has no health
score, no meaningful mission history, and nothing worth briefing on — `_briefing_blocks` treats a
`None` result exactly like a caught exception (see below) and falls back to the original static
`"empty"` triad, so **the no-assessment path is byte-for-byte unchanged from Module 02.**

**`generating`/`ready` statuses ride the existing `{status, message}` contract — no FE-breaking
shape change.** Module 02 already returned `{status: "empty", message: "..."}` per section;
`_briefing_blocks` now returns `{status: row.status.value, message: row.<field>}` once a row
exists, so `"generating"` and `"ready"` are just new values for a field the FE contract already
declared as a string. No new top-level key, no new nesting.

**One `complete_json` call writes all three fields together, not three separate calls.**
`dashboard_briefing_schema()` (`app/services/dashboard/ai_briefing.py`) is a single strict object
schema with three required string properties (`briefing`, `risks`, `opportunities`,
`additionalProperties: false`) — one LLM round trip produces a coherent trio instead of three
independent, possibly-contradictory calls. `build_dashboard_briefing_messages` takes 9 keyword
args (name/industry/stage + health score/band + mission counts + upcoming count + weekly task
count) — all business/state context, no PII — matching `gather_briefing_context`'s return dict
key-for-key (verified by the implementer against the source, not assumed).

**`IntegrityError` race guard mirrors the mission path exactly.** Two concurrent first-loads of
the day for the same startup (two summary polls racing) can both pass the `existing is None`
check and both `INSERT` against `uq_briefing_startup_date`; the loser's flush raises
`IntegrityError`. `get_or_generate_briefing` wraps the create in `db.begin_nested()` and, on
`IntegrityError`, re-selects the now-committed winner's row — the same shape as
`_get_or_generate_today_race_safe`, reused deliberately rather than re-invented.

**`_section_isolated`, not `_section`, wraps the briefing — so a briefing failure can't 500 the
whole dashboard.** `_briefing_blocks` runs `get_or_generate_briefing` inside
`_section_isolated(db, ...)`, the SAVEPOINT-wrapped helper every other dashboard section except
`health` already uses (see Module 02's SOP for why `health` is the one exception — it commits
internally and can't sit inside a SAVEPOINT). A DB-level failure inside the briefing gate rolls
back only that SAVEPOINT and returns `{"error": True}`, which `_briefing_blocks` treats the same
as "no assessment" and falls back to the static empty triad — a briefing outage degrades to the
pre-Module-03 experience, it never 500s `GET /dashboard/summary`.

**The worker handler is idempotent by construction, not by a separate guard column.**
`handle_dashboard_briefing` (`app/worker/handlers/ai.py`) re-fetches the `DailyBriefing` row by
`(startup_id, briefing_date)` and no-ops unless `row.status == BriefingStatus.generating` — a
row already `ready` (a duplicate/retried job) or missing (deleted between enqueue and drain) is
left untouched. This is simpler than Slice 4's health-recommendation idempotency guard (which
compares `body` against a catalog default) because there's no pre-existing "default" text to
compare against — `status` itself is the idempotency signal.

**One correction to the plan text, made during implementation (Task 3):** the plan's
`gather_briefing_context` snippet referenced `MissionTaskStatus.complete`, which does not exist
on the enum (`app/db/models/enums.py` only defines `todo`/`done`/`snoozed`/`rejected` — the
file's own pre-existing `_tasks_done_this_week` already compares against `.done`). The
implementer used `MissionTaskStatus.done` instead; mypy caught the invalid reference immediately
during TDD. Logged in the SDD ledger (`.superpowers/sdd/2026-09-19-dashboard-ai-briefing/
progress.md`, Task 3 ruling) as a zero-cost correction — `.done` is the real completed-task
member and no other interpretation was plausible.

## What's involved

**Migration `0027_daily_briefings`** (chains off `0026_business_plans`, sole alembic head) —
autogenerated, hand-edited only for the revision id header (no op bodies hand-written):
- New table `daily_briefings`: `id` (uuid PK), `startup_id` (uuid FK → `startups.id`,
  `ondelete="CASCADE"`, indexed), `briefing_date` (date), `status` (non-native enum,
  `generating`/`ready`/`failed`, length 12), `briefing`/`risks`/`opportunities` (text, not
  nullable), `created_at`/`updated_at` (timestamptz, server default `now()`).
- `uq_briefing_startup_date` unique constraint on `(startup_id, briefing_date)` — the same
  one-row-per-startup-per-day shape as `Mission`'s `uq_mission_startup_date`.

**Model + enum**
- `app/db/models/dashboard.py::DailyBriefing` (new) — `UUIDMixin` + `TimestampMixin`, mirrors the
  migration exactly; `status` defaults to `BriefingStatus.generating` at the ORM level too.
- `app/db/models/enums.py::BriefingStatus(enum.StrEnum)` (new) — `generating` / `ready` /
  `failed`. `failed` is defined but never written by any code path yet (see Follow-ups).
- `app/db/models/__init__.py` — `DailyBriefing` registered so `create_all`-built test DBs and
  Alembic autogenerate both see it.

**Prompt + schema**
- `app/services/dashboard/ai_briefing.py` (new) — `dashboard_briefing_schema()` (strict, 3
  required string fields, `additionalProperties: false`) · `build_dashboard_briefing_messages(*,
  name, industry, stage, health_score, health_band, mission_total, mission_done, upcoming_count,
  tasks_done_week) -> list[LLMMessage]` (PII-free business/state context only).

**Service wiring** (`app/services/dashboard/service.py`)
- `_BRIEFING_GENERATING` constant (`"Putting together your briefing…"`) alongside the existing
  `_BRIEFING_EMPTY`/`_RISKS_EMPTY`/`_OPPS_EMPTY`.
- `gather_briefing_context(db, startup) -> dict[str, Any]` — read-only, keys match
  `build_dashboard_briefing_messages`'s kwargs exactly.
- `get_or_generate_briefing(db, startup) -> DailyBriefing | None` — the gate + lazy-create +
  enqueue-once + race guard described above.
- `_briefing_blocks(db, startup) -> dict[str, Any]` — `_section_isolated`-wrapped caller that
  maps a `DailyBriefing` row (or its absence/error) to the three `{status, message}` dicts.
- `get_summary` — the three static `briefing`/`risks`/`opportunities` keys replaced with
  `**_briefing_blocks(db, startup)`.

**Worker handler** (`app/worker/handlers/ai.py`)
- `handle_dashboard_briefing(db, job)` (new, ~`ai.py:222-254`) — no-op guards (missing startup,
  missing row, row not `generating`) → `gather_briefing_context` → `complete_json` against
  `dashboard_briefing_schema()` → overwrite `briefing`/`risks`/`opportunities` + `status =
  BriefingStatus.ready` → `db.flush()` only (no commit/rollback — the runner owns the
  transaction, same convention as every other handler in this file). Registered as
  `"ai.dashboard.briefing"`.

**Tests**
- `tests/db/test_dashboard_models.py` — round-trip + unique-per-startup-day (`IntegrityError`).
- `tests/services/dashboard/test_ai_briefing.py` — schema shape, stub-fillability, PII-free
  prompt (3 tests).
- `tests/services/dashboard/test_briefing_generation.py` — no-briefing-without-assessment,
  generate-and-enqueue-once, `get_summary` reflects state across empty → generating → ready
  (3 tests).
- `tests/worker/test_dashboard_briefing_handler.py` — fill-generating-row, no-op-already-ready,
  no-op-startup-missing, stub-marks-fields, fails-loud-on-LLM-error (5 tests).
- `e2e/test_dashboard_ai_briefing.py` (new) — full live journey, see Verification.

**Errors / API surface — none new.** `GET /api/v1/dashboard/summary` is the same pre-existing
route; this slice only changes what value three of its fields hold a few seconds after a read,
never a status code or an envelope shape.

## Verification

**Per-task unit verification (green before the e2e task):**
- `tests/db/test_dashboard_models.py` — 2 passed; `poetry run alembic upgrade head` /
  `alembic check` / `alembic heads` confirmed a single linear head (`0027_daily_briefings`), no
  drift between the ORM model and the applied migration.
- `tests/services/dashboard/test_ai_briefing.py` — 3 passed, 100% coverage of the new module.
- `tests/services/dashboard/test_briefing_generation.py` — 3 passed; existing dashboard suite
  (`tests/api/test_dashboard_activity.py`, `tests/api/test_dashboard_summary.py`,
  `tests/services/test_dashboard_concurrency.py`, `tests/services/test_dashboard_summary.py`,
  `tests/db/test_dashboard_models.py`, `tests/services/dashboard/`) — 23 passed; full non-e2e
  suite re-run as a safety net — 1297 passed.
- `tests/worker/test_dashboard_briefing_handler.py` — 5 passed; full `tests/worker/` suite
  re-run — 54 passed (no regression).
- Each task's own `black --check` / `isort --check-only` / `ruff check` / `mypy app` passed
  before merge to this branch (task reports 1–4), all against the project's poetry-managed
  toolchain versions.

**Live e2e (Task 5) — `scripts/e2e_run.sh`, full suite:**

```
e2e/test_dashboard_ai_briefing.py::test_dashboard_ai_briefing PASSED
...
49 passed in 33.59s
```

All 49 e2e tests pass (48 pre-existing + 1 new), confirming no regression. The new test walks:
sign up → onboard (stage `validation`) → complete the kickoff assessment (`_LOW_ANSWERS`) → apply
the weekend-missions guard → `GET /dashboard/summary` (asserts `briefing`/`risks`/`opportunities`
all `"generating"`, captures `summary_generating.json`) → drain the worker → `GET
/dashboard/summary` again (asserts all three `"ready"` with `"[stub-llm]"` markers, captures
`summary_ready.json`).

**Incidental drain note:** the e2e's worker drain is broad (it processes every pending job for
the test's startup, not only `ai.dashboard.briefing`), so `summary_ready.json` also shows
`[stub-llm]` text in `health.top_recommendations[].body` and `mission.tasks[].reason` — those
come from Module 03 Slice 4's already-shipped `ai.health.recommendations` and `ai.mission.reason`
workers being drained in the same pass, not from this slice. The FE guide update explicitly
calls this out so the two features aren't conflated (see
`docs/fe-integration-guide-dashboard.md`, "AI daily briefing" subsection).

**Necessary downstream fix, in scope per Task 5's "both e2e suites must pass" requirement:**
`e2e/test_dashboard.py::test_dashboard_journey` had a stale assertion
(`briefing.status == "empty"`) left over from before Task 3's lazy-generation wiring shipped —
once the kickoff assessment completes, the very next `GET /dashboard/summary` now opens the
briefing gate immediately, so `"empty"` can no longer be observed at that point in that journey.
Fixed the three assertions to `"generating"` and regenerated
`e2e/_captures/dashboard/summary.json` from a real run. This was a stale-oracle fix, not a
weakened assertion — verified via `git log` that the assertion predated Task 3's change.

This is the full CI-relevant reproduction for the app-code tasks (1–5); this task (6) is
docs-only and introduces no `app`/`test` changes.

## Operate / roll back

**New deploy-time requirement: none.** The `ai.dashboard.briefing` job type runs inside the
existing `worker` process (Module 20 Slice 2) — no new container, no new health check, no new
config beyond what Module 03 Slice 1 already introduced
(`LLM_PROVIDER`/`LLM_API_KEY`/`LLM_MODEL`/`LLM_BASE_URL`/`LLM_TIMEOUT`/`LLM_MAX_TOKENS`).

**Rollback:** revert this slice's commits as a unit (`8b6b33c..065cf8f`, plus this docs commit)
and downgrade the migration (`poetry run alembic downgrade 0026_business_plans`) to drop
`daily_briefings`. Rolling back the migration is safe — the table holds no data any other
feature reads. If the code is reverted without downgrading the migration, `GET /dashboard/summary`
simply stops writing to a table that still exists but is no longer referenced; if the migration
is reverted first, in front of not-yet-rolled-back code, the next dashboard read for an assessed
founder raises (the table no longer exists) — **downgrade the migration only after the app code
is already rolled back**, not before.

## Follow-ups

**Onboarding AI panel and roadmap re-plan rationale remain unbuilt.** This slice closes the
third of five Module-03-deferred AI consumers named across Modules 02/04/05/06's own SOPs (the
last unconsumed ai_fill job type closed in Slice 3; mission reason + health recommendations
closed in Slice 4; dashboard briefing closes here). **Only two remain: the onboarding AI panel
and the roadmap re-plan rationale.** Module 03 stays **open** until both ship. Each needs its own
prompt/schema/trigger design, not a mechanical copy of this slice's pattern.

**`briefing`/`risks`/`opportunities` are single prose strings, not structured lists.** The spec
deliberately kept the shape as one string per section (matching Module 02's original static
shape) rather than introducing a list-of-risk-items or list-of-opportunity-items structure. A
future iteration may want `risks`/`opportunities` as arrays of `{title, detail}` items for
richer FE rendering — that's a breaking shape change and out of scope here.

**No 06:00 prewarm / scheduled regeneration.** Generation is purely lazy-on-read, gated on the
first `GET /dashboard/summary` of the day for an assessed founder — a founder who doesn't open
the dashboard until midday sees `"generating"` at that point, not a briefing that was already
ready at 06:00. A prewarm cron (mirroring Module 20 Slice 3's scheduler infrastructure) is a
plausible follow-up but wasn't part of this slice's scope.

**No intra-day regeneration.** Once a `daily_briefings` row exists for `(startup_id, today)`,
`get_or_generate_briefing` always returns the cached row for the rest of the day, even if the
founder's health score or mission state changes materially (e.g. completes every mission task
by noon). This matches Module 02's original "once-a-day" framing but means the briefing can go
stale within the same day — not addressed by this slice.

**`BriefingStatus.failed` is defined but never written.** The enum has a `failed` member for a
future "the LLM call failed and we're giving up" signal, but `handle_dashboard_briefing` doesn't
set it — an LLM error today raises and relies on the job runner's own retry, leaving the row on
`generating` indefinitely if all retries are exhausted. Same class of gap Slice 4's SOP flagged
for `MissionTask`/`HealthRecommendation`: no structured "AI generation failed, stop waiting"
signal reaches the FE.

**Stub-only proof of the happy path.** The live e2e proves the single-row, single-LLM-call path
against the stub provider (`StubLLMClient`). The race-guard path
(`get_or_generate_briefing`'s `IntegrityError` branch) is not exercised under real concurrency by
any test — same pre-existing gap the mission original (`_get_or_generate_today_race_safe`) has
always had.

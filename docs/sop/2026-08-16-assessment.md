# SOP — Startup Assessment (Plan 4)

**What shipped** — The adaptive Startup Assessment: a versioned question bank (v1, 11
questions — `product_stage`, `product_confidence`, `market_clarity`, `market_research`,
`has_revenue`, `mrr`, `runway_confidence`, `incorporated`, `ip_assigned`, `team_size`,
`team_confidence` — across 5 dimensions), a `show_if`-driven adaptive engine that walks a
founder through only the applicable questions, deterministic 0–100 scoring per dimension
with a templated narrative, and 7 endpoints (`start`/`resume`, `next-question`, `answers`,
`complete`, `list`, `detail`, `compare`). Completion flips `assessment_pending` off (initial
assessments only), enqueues two stub recalculation jobs, and publishes an
`assessment.completed` event. New `assessments` / `assessment_answers` /
`assessment_results` tables (migration `0004_assessment`). This is the **single
consolidated SOP for the whole assessment feature** — per-task SOPs were intentionally
deferred here (branch `feat/assessment`, 10 implementation tasks + this one).

Commits: `20fb79a`..`0b5a919` (Tasks 1–10) + this task's `e2e/test_assessment.py` /
`docs/sop/2026-08-16-assessment.md` commit.

## Why

Plan 3 (onboarding) set `assessment_pending=True` on a founder's startup at
`POST /onboarding/complete` but nothing ever cleared it — the flag existed purely as a
forward signal for this module. Downstream modules (Health Score, Roadmap) need a
structured, scored read on where a startup actually stands across product/market/money/
legal/team before they can generate anything meaningful. Plan 4's goal: let a founder walk
a short, adaptive questionnaire once, get a provisional 0–100 score per dimension plus an
overall figure and a one-line narrative, and fire the two downstream recalculation jobs —
without hardcoding one giant static form that asks fintech founders about restaurant
inventory.

## How

**Versioned, static question bank.** `ASSESSMENT_BANK` (`app/services/assessment/bank.py`)
is a frozen dataclass structure — `Bank(version="v1", questions=[...])` — not a DB table.
Each `Question` carries its `dimension` (product/market/money/legal/team), `qtype`
(`single_choice` / `multi_choice` / `scale_1_5` / `numeric_currency` / `short_text`), a
`scoring` dict (per-option point values + `max`), optional `options`, and an optional
`show_if` gate. `Assessment.bank_version` is stamped at start so a founder's answers always
score against the bank version they were actually asked, even if a later version ships
mid-assessment.

**`show_if` grammar and the adaptive engine** (`app/services/assessment/engine.py`).
Four node shapes, recursively composable: `{"answer": key, "eq"|"in": ...}` (gate on a
prior answer in *this* assessment), `{"field": attr, "eq"|"in": ...}` (gate on a `Startup`
attribute — resolves enums via `.value`), `{"all": [...]}` / `{"any": [...]}` (boolean
combinators). `next_question()` walks the bank in declaration order and returns the first
unanswered question whose `show_if` currently evaluates true — e.g. `product_confidence`
only appears once `product_stage` is answered `mvp`/`live`; `mrr` only appears once
`has_revenue` is answered `yes`. A `None` return means "no more applicable questions,"
which both `GET /next-question` and the `complete` gate below key off directly — there is
no separate "is this assessment done" flag to keep in sync.

**Deterministic scoring, not AI** (`app/services/assessment/scoring.py`). Per dimension:
sum `earned` points across answered, currently-applicable, scoreable questions
(`scoring["max"] > 0` — one question, `product_stage`, is informational-only and
contributes no points) over `maxsum` of those same questions' `max` values, `round(100 *
earned / maxsum)`, clamped `0–100`. **A dimension with zero answered scoreable questions
defaults to neutral 50**, not 0 — an unanswered dimension isn't evidence of weakness, and
scoring it 0 would unfairly tank the overall. `overall_provisional` is the plain average of
the 5 dimension scores. The narrative is a single templated sentence naming the
highest-scoring and lowest-scoring dimension (`"Your strongest area is {top} ({score}).
Focus next on {low} ({score})."`) — no LLM call in v1.

**Start/resume, not always-create.** `start_or_resume()` (`app/services/assessment/
service.py`) first looks for an `in_progress` assessment for the startup and returns it
unchanged if found (`POST /assessments` is idempotent while a session is open) — this is
also what the partial unique index (`uq_assessments_startup_in_progress`, see migration
below) protects at the DB layer against a concurrent double-create. If none is in progress,
type is `initial` for a startup's first assessment ever, `quarterly` for every subsequent
one (checked via "does a completed assessment already exist").

**Answers are forward-only, atomically upserted.** `submit_answer()` re-derives the
*current* question server-side (never trusts the client's `question_key` blindly) and 422s
`INVALID_ANSWER` if the submitted key isn't the one the adaptive engine currently expects —
a founder cannot answer out of order or revise an already-answered question in v1. The
actual write is `INSERT ... ON CONFLICT (assessment_id, question_key) DO UPDATE` (Postgres
`ON CONFLICT`, not query-then-write) specifically to close a TOCTOU race: two concurrent
submits of the same current question would otherwise both pass the "is this the current
question" check, then race on the `uq_answer_assessment_question` unique constraint, and
the loser would get an unhandled `IntegrityError` (500) instead of resolving cleanly.

**Completion: atomic claim, not read-then-write.** `complete_assessment()` first re-runs
`next_question()` — if it returns non-`None`, 422 `ASSESSMENT_INCOMPLETE` naming the
question still needed. Otherwise it claims the assessment with a single conditional
`UPDATE ... WHERE id = :id AND status = 'in_progress'` (mirrors the `rotate_refresh`
pattern in `app/services/auth/sessions.py`): only the caller whose `UPDATE` actually matches
a row is the one that scores, writes the `AssessmentResult`, flips `assessment_pending`,
enqueues jobs, and publishes the event — a concurrent second call (or a retried request)
sees `claimed == 0`, and returns the already-stored result instead of re-scoring or
double-firing side effects. `assessment_pending` is only flipped for `type == initial`
assessments — a `quarterly` re-assessment completing doesn't re-trigger the "you have a
pending assessment" onboarding signal.

**Jobs and events.** On a winning completion: two `Job` rows via the existing v1
`JobDispatcher` stub (`app/platform/jobs.py`) — `healthscore.recalculate` and
`roadmap.replan`, both `payload={"startup_id", "assessment_id"}`, persisted as `queued`
rows (drained by a worker in Modules 05/06, same as onboarding's jobs — no worker exists
yet) — plus an `assessment.completed` event on the log-only `event_bus` carrying the
dimension scores. **Unlike onboarding**, the completion response does not currently return
these jobs' ids (see "Known gaps" below) — they exist and are individually pollable at
`GET /jobs/{id}` *if you already have the id* (e.g. from a DB query), but a caller who only
has the HTTP response from `POST /complete` cannot discover them.

## What's involved

**Data model / migration**
- `alembic/versions/0004_assessment.py` — three new tables, no lock on any existing table
  (metadata-only from every other table's perspective; see the migration's own docstring
  for the full lock-duration reasoning).
  - `assessments` (+ `ix_assessments_startup_id`, and the partial unique index
    `uq_assessments_startup_in_progress` — `UNIQUE(startup_id) WHERE status =
    'in_progress'`, so a startup may have at most one in-progress assessment at a time
    while any number of completed/abandoned ones are unrestricted).
  - `assessment_answers` (+ `ix_assessment_answers_assessment_id`,
    `uq_answer_assessment_question UNIQUE(assessment_id, question_key)`).
  - `assessment_results` (`assessment_id` is both PK and FK — 1:1 with `assessments`, no
    separate identity column).
- `app/db/models/assessment.py` — `Assessment`, `AssessmentAnswer`, `AssessmentResult`.
- `app/db/models/enums.py` — `AssessmentType` (`initial`/`quarterly`), `AssessmentStatus`
  (`in_progress`/`completed`/`abandoned`), `Dimension` (`product`/`market`/`money`/`legal`/
  `team`).

**Endpoints** (all under `/api/v1/assessments`)

| Method | Path | Auth | File |
|---|---|---|---|
| POST | `/api/v1/assessments` | founder + `X-Workspace-Id` | `app/api/v1/endpoints/assessments.py` |
| GET | `/api/v1/assessments/{id}/next-question` | founder + `X-Workspace-Id` | same |
| POST | `/api/v1/assessments/{id}/answers` | founder + `X-Workspace-Id` | same |
| POST | `/api/v1/assessments/{id}/complete` | founder + `X-Workspace-Id` | same |
| GET | `/api/v1/assessments` | any active member + `X-Workspace-Id` | same |
| GET | `/api/v1/assessments/compare?ids=` | any active member + `X-Workspace-Id` | same |
| GET | `/api/v1/assessments/{id}` | any active member + `X-Workspace-Id` | same |

Write endpoints gate on `require_role(founder)`; read endpoints gate on `require_workspace`
(any active member). **Route ordering matters**: the literal `/compare` route is registered
*before* `GET /{assessment_id}` — otherwise FastAPI would try to parse `"compare"` as a
UUID path param and fail before the compare handler ever runs.

**Services / schemas / errors**
- `app/services/assessment/bank.py` — `ASSESSMENT_BANK` (v1), `Question`, `Bank`,
  `question_by_key`.
- `app/services/assessment/engine.py` — `is_applicable`, `next_question`,
  `validate_answer`.
- `app/services/assessment/scoring.py` — `score` (dimension scores + overall + narrative).
- `app/services/assessment/service.py` — `answered_map`, `serialize_question`,
  `start_or_resume`, `submit_answer`, `complete_assessment`.
- `app/schemas/assessment.py` — `AnswerRequest`.
- Errors (ad-hoc `AppError` codes, not dedicated subclasses): `INVALID_ANSWER` (422 — wrong
  answer type/option, or answering a question that isn't the current one) and
  `ASSESSMENT_INCOMPLETE` (422 — completing before every applicable question is answered,
  carries `field_errors` naming the missing question). `NotFound` (404, existing) covers
  unknown/cross-tenant assessment ids everywhere, including `compare`.

**Tests**
- `tests/api/assessment/`, `tests/services/assessment/`, `tests/db/
  test_assessment_models.py`, `tests/test_assessment_migration.py` — 58 unit tests (real DB,
  rolled back per test; concurrency tests for the answer-upsert race and the
  completion-claim race).
- `e2e/test_assessment.py` (this task) — one live end-to-end journey against a real running
  server.

## Verification

- **Live E2E: 22 passed** (`make e2e`) — 21 prior (auth journeys + onboarding + smoke) +
  the new `test_assessment_journey`: founder signup→verify→login→onboard (4 steps)→
  complete→`/auth/me` for the workspace id→`POST /assessments` (start)→adaptive loop of
  `GET next-question` / `POST answers`, answering each `single_choice`/`multi_choice`
  question with its **last** option (not the first) so the "yes"/most-advanced branch is
  taken — this is what actually exercises the `show_if` gate over HTTP: `product_stage`→
  `live` reveals `product_confidence`, `has_revenue`→`yes` reveals `mrr`, `incorporated`→
  `yes` reveals `ip_assigned`. The test asserts more than the 8 unconditional questions were
  answered and that the gated keys were among them, proving the adaptive engine's reveal
  end-to-end rather than only ever walking the ungated path. Loop continues until
  `next_question` is `null`→`POST complete` (asserts all 5 dimension keys present, an int
  `overall_provisional`, a non-empty `narrative`)→`GET /onboarding/state` confirms
  `assessment_pending` flipped to `false`→`GET /assessments` shows the row `completed` with
  the matching `overall_provisional`→`GET /assessments/{id}` returns the grouped
  answers-by-dimension (count matches what was answered) and the same stored result.
- **Unit suite: 205 passed, 98% coverage** (`poetry run pytest -q`; 58 of those are
  assessment-specific, `-k assessment`); `make lint` clean (black, isort, ruff, mypy all
  pass on `app`/`tests`).
- Migration round-trip verified in Task 1/2 (`alembic upgrade head && downgrade -1 &&
  upgrade head`) and again implicitly by every `make e2e` run (fresh `cofoundaz_e2e` DB
  migrated from zero each time).

**Environment note (not a code issue):** this task's first `make e2e` run failed almost
across the board with mixed 404s on real routes and a 503 on `/api/v1/health`. Root cause
was local, not the app: an unrelated process from a different project (`luran`, bound to
`*:8010`) was already squatting on the e2e harness's default port, plus my own truncated
first run (piped through `head`) left an orphaned uvicorn instance also bound to `:8010` —
requests were routing unpredictably between three processes on the same port. Killed the
orphan, left the unrelated foreign process alone (not mine to kill without asking), and
reran with `E2E_PORT=8111`: clean 22/22. No app or test code was at fault.

## Operate

- No new env vars or deploy steps beyond the existing `make e2e` / `alembic upgrade head`
  flow. Jobs table already exists from Plan 1; `healthscore.recalculate` and
  `roadmap.replan` are new job *types* but need no new worker registration since nothing
  drains the queue yet (same as onboarding's jobs).
- **Rollback:** `alembic downgrade -1` drops `assessment_results`, `assessment_answers`,
  `assessments` in that FK-safe order. This is **lossy** — any assessment data written while
  `0004` was applied is destroyed on downgrade, same as any brand-new-table migration.
  There is no `NOT NULL` backfill risk here (unlike `0003`'s `startups.name` tightening) —
  downgrade is a straight drop.

## Follow-ups

**Deferred to later modules (by design, not oversights):**
- **Quarterly re-assessment cron** — `AssessmentType.quarterly` is fully modeled and scored
  (a startup's second-and-later assessment is automatically typed `quarterly`, and
  `assessment_pending` is deliberately *not* touched by a quarterly completion), but nothing
  yet schedules a founder to be prompted quarterly. That's a scheduling/notification concern
  for a later module, not a scoring concern.
- **AI-assisted narrative** (Module 03/06) — the current narrative is one deterministic
  templated sentence. Module 03 (AI panel) / Module 06 (Health Score) are expected to
  replace or augment it with an LLM-generated writeup once those modules exist; v1
  deliberately ships the deterministic version so scoring has a stable, testable baseline
  independent of any model call.
- **Overall company Health Score** (Module 06) — `overall_provisional` is exactly what its
  name says: a provisional, assessment-only average. The real Health Score (combining
  assessment, roadmap progress, and other signals) is Module 06's job; the two
  `healthscore.recalculate` / `roadmap.replan` job stubs enqueued on completion are the
  hand-off point, not yet drained by any worker.
- **Admin-editable question bank** (Module 25) — `ASSESSMENT_BANK` is a static, versioned
  Python structure. An admin UI to add/retire questions or ship a new bank version without a
  code deploy is out of scope for v1.
- **Revising an already-answered question** — `submit_answer` only accepts the *current*
  question per the adaptive engine; there is no "go back and change an earlier answer" path.
  A founder who wants to change `has_revenue` after already answering `mrr` has to complete
  and (if a mechanism to abandon existed) start a fresh assessment. Deliberately deferred —
  revisiting past answers interacts with the adaptive graph (an earlier answer's change can
  make/unmake later questions applicable) and needs its own design pass, not a bolt-on.

**Known gaps, tracked not fixed (flagged during implementation, each with a documented
reason for not fixing in-scope):**
- **`assessments.created_by` has no supporting index** (migration `0004` docstring) — a
  future user-delete/audit query filtering by `created_by` would seq-scan `assessments`.
  Not added speculatively in this migration because an index unbacked by the SQLAlchemy
  model would make the next `alembic revision --autogenerate` immediately propose dropping
  it (schema drift); needs a paired model + migration change together.
- **`AssessmentStatus.abandoned` is modeled but unreachable** — the enum value exists and
  the partial unique index's `WHERE status = 'in_progress'` clause is written to correctly
  exempt abandoned assessments from the one-in-progress-at-a-time rule, but nothing in v1
  ever transitions an assessment to `abandoned` (no timeout, no explicit abandon action). So
  there's currently no dedicated error message for "this assessment was abandoned" — that
  UX doesn't exist yet because the state itself is never reached. Whoever adds an abandon
  path (explicit action or a staleness timeout) should also decide what founders see when
  they try to resume or view an abandoned assessment.
- **`GET /assessments/compare` returns a uniform 404** for an unknown id, a cross-tenant id,
  and a not-yet-completed id alike (`app/api/v1/endpoints/assessments.py` — joining
  `AssessmentResult` to `Assessment` and filtering by `startup_id` gets "must exist, must be
  this tenant's, must be completed" all from one query with no separate status check). This
  is **intentional**, not a gap to close — it avoids leaking which of those three states
  applies to an id a caller has no business probing. Documented here so a future reviewer
  doesn't "fix" it into three distinct error codes.
- **`complete_assessment`'s response doesn't return `job_ids`** — an asymmetry with
  `complete_onboarding` (`app/services/onboarding/complete.py`), which does return
  `job_ids: [str(j1.id), str(j2.id)]`. `complete_assessment`'s `_result_dict`
  (`app/services/assessment/service.py`) enqueues `healthscore.recalculate` and
  `roadmap.replan` the same way but never surfaces their ids in the HTTP response, and there
  is no list-jobs-by-startup route either — so an HTTP-only caller (a frontend, or this
  task's e2e harness) cannot discover those two job ids to poll `GET /jobs/{id}` after
  completing an assessment. Follow-up: add `job_ids` to `_result_dict` for parity with
  onboarding, so the FE can poll the recalibrate jobs the same way it polls onboarding's.
- **`healthscore.recalculate` stub retired (Module 06)** — as of Module 06, the
  `healthscore.recalculate` / `healthscore.initialize` stub jobs are retired; the Health
  Score is recomputed inline at assessment-complete instead (see
  `docs/sop/2026-08-19-health-score.md`).

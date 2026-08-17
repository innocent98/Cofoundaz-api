# Design — Module 07 Startup Assessment

> **Status:** Approved (brainstorm) · **Date:** 2026-08-16 · **Repo:** `cofoundaz-api`
> **Sources of truth:** `../Cofoundaz_Technical_PRD.md` (Module 07, §0, §2.2), the merged
> Foundation + Auth + Onboarding work, and the onboarding spec
> `docs/superpowers/specs/2026-08-15-onboarding-design.md`.
>
> Design spec for the first module after Module 01. Precedes the implementation
> plan (writing-plans) and the post-ship SOP (`docs/sop/`).
> Built on `feat/onboarding` (onboarding provides the workspace + `assessment_pending`).

---

## 1. Scope

**In scope** — the adaptive Startup Assessment: a resumable, server-driven single-question
runner across 5 dimensions (Product · Market · Money · Legal · Team) with per-answer
autosave; a **static, versioned question bank** with declarative skip logic; **deterministic
per-dimension scoring** (0–100) computed at completion into `assessment_results`; `initial`
and `quarterly` assessment types; the onboarding linkage (completing the `initial`
assessment flips `assessment_pending = False`); Past Results (list + detail) and Comparison
(up to 3). Completion emits `assessment.completed` and enqueues health-score/roadmap recalc
**stub jobs**.

**Deferred (seam/stub, unchanged from platform decisions):**

| Deferred | Handled now as | Built in |
|---|---|---|
| Quarterly **cron** trigger | `quarterly` type + manual start endpoint; no scheduler | Notifications / scheduler later |
| **AI narrative** at complete | templated deterministic string | Module 03 (AI Co-Founder) |
| Overall **Health Score** 0–100 | `overall_provisional` = mean of dimensions | Module 06 (Health Score) |
| Async worker draining recalc jobs | `JobDispatcher` enqueues `queued` rows | Modules 05 / 06 |
| **Admin-editable** question bank | static in-repo versioned config | Module 25 (Admin Portal) |
| Radar/diff **rendering** | API returns the numbers | Frontend |

## 2. Decisions (locked in brainstorming)

| # | Decision | Choice |
|---|---|---|
| 1 | Scoring | **Deterministic per-dimension now** (0–100 each), stored in `assessment_results` with a templated narrative and a provisional overall = mean. Overall Health Score + AI narrative deferred (Modules 06/03). |
| 2 | Question bank | **Static, versioned in-repo config** (`ASSESSMENT_BANK`), adaptivity as declarative data. No DB question table; admin-editable bank deferred to Module 25. |
| 3 | Access | **Workspace-scoped** via `X-Workspace-Id` + `memberships`. **Mutations (start/answer/complete) require Founder** (`require_role(founder)`); **reads (results/detail/compare) allow any active member** (`require_workspace`). |
| 4 | One in-progress per workspace | Starting when an `in_progress` assessment exists **resumes it** (returns it) rather than creating a second. First-ever assessment is `initial`; subsequent are `quarterly`. |
| 5 | Adaptivity | Each question carries a declarative `show_if` over prior answers + startup `stage`/`business_model`. The engine returns the next **unanswered applicable** question in bank order; skipped questions never count toward scoring. |
| 6 | Versioning | `assessments.bank_version` records the bank version used, so historical results stay interpretable when the bank evolves. |

## 3. Data model (migration `0004_assessment`)

**New enums** (`app/db/models/enums.py`):
- `AssessmentType(initial | quarterly)`
- `AssessmentStatus(in_progress | completed | abandoned)`
- `Dimension(product | market | money | legal | team)`

**`assessments`** (UUIDMixin + TimestampMixin):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `startup_id` | UUID FK→startups | CASCADE, indexed |
| `type` | `AssessmentType` (Enum native_enum=False) | |
| `status` | `AssessmentStatus` | default `in_progress` |
| `bank_version` | String | the bank version used |
| `created_by` | UUID FK→users | who started it |
| `started_at` | timestamptz | default now |
| `completed_at` | timestamptz? | set at complete |

Partial-unique index: **at most one `in_progress` per `startup_id`** — `UNIQUE(startup_id) WHERE status = 'in_progress'`.

**`assessment_answers`** (UUIDMixin + TimestampMixin):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `assessment_id` | UUID FK→assessments | CASCADE, indexed |
| `question_key` | String | |
| `value_json` | JSONB | the answer payload |
| `answered_at` | timestamptz | default now |

**`UNIQUE(assessment_id, question_key)`** — re-answering upserts (autosave-friendly).

**`assessment_results`** (TimestampMixin, `assessment_id` PK):

| Column | Type | Notes |
|---|---|---|
| `assessment_id` | UUID PK/FK→assessments | CASCADE (1:1) |
| `dimension_scores` | JSONB | `{product,market,money,legal,team: 0–100}` |
| `overall_provisional` | Integer | mean of dimensions (Module 06 refines) |
| `narrative` | Text | templated summary |

## 4. Adaptive engine + question bank

- **`app/services/assessment/bank.py`** — `ASSESSMENT_BANK`: a `version` string + an ordered list of `Question` dataclasses. Each `Question`:
  - `key` (stable str), `dimension` (`Dimension`), `section` (display name), `qtype` (`single_choice | multi_choice | scale_1_5 | numeric_currency | short_text`), `options` (for choice types), `scoring` (map from answer → points, plus a `max` used as the denominator), `show_if` (a small declarative condition; `None` = always shown).
  - `show_if` grammar (minimal, testable): a dict like `{"field": "stage", "in": ["growth", "scale"]}` (against `startup.stage`/`business_model`) or `{"answer": "<question_key>", "eq": <value>}` / `{"answer": "<key>", "in": [...]}` (against a prior answer). Combine with `{"all": [ ... ]}` / `{"any": [ ... ]}`.
- **`app/services/assessment/engine.py`**:
  - `is_applicable(question, answers, startup) -> bool` — evaluates `show_if`.
  - `next_question(bank, assessment, answers, startup) -> Question | None` — first applicable + unanswered question in bank order; `None` when the applicable set is fully answered.
  - `validate_answer(question, value) -> None` — type/options/range checks; raises `AppError("INVALID_ANSWER", …, 422)` on mismatch (unknown option, scale out of 1–5, non-numeric currency, wrong multi/single shape).
  - The bank ships with a v1 covering all 5 dimensions with representative adaptive rules (e.g. Money "current MRR" only shown when a prior "do you have revenue?" answer is yes; Legal "which entity type" only when "are you incorporated?" is yes).

## 5. Scoring (at complete)

`app/services/assessment/scoring.py::score(bank, assessment, answers, startup) -> {dimension_scores, overall_provisional, narrative}`:
- For each `Dimension`: sum `scoring` points earned across that dimension's **applicable, answered** questions ÷ sum of their `max` → scaled to 0–100 (integer). Dimensions with no applicable questions default to a neutral 50 with a note (rare given the bank).
- `overall_provisional` = round(mean of the five dimension scores).
- `narrative` = templated, e.g. *"Your strongest area is {top_dim} ({top}). Focus next on {low_dim} ({low})."*
- Completion side-effects (in the `complete` service): write `assessment_results`; set `status=completed`, `completed_at=now`; if `type == initial`, set `startup_profiles.assessment_pending = False`; `event_bus.publish("assessment.completed", {assessment_id, startup_id, dimension_scores})`; `job_dispatcher.enqueue("healthscore.recalculate", …)` and `("roadmap.replan", …)`.

## 6. Endpoints (`/api/v1/assessments`, workspace-scoped)

All resolve the workspace via `X-Workspace-Id` + membership (Foundation `require_workspace`/`require_role`). Standard envelope.

| Endpoint | Access | Behavior |
|---|---|---|
| `POST /assessments` | Founder | If an `in_progress` assessment exists for the workspace → return it (resume). Else create one (`type=initial` if none ever completed for the workspace, else `quarterly`), `bank_version` = current. Returns `{assessment_id, type, status, next_question}`. |
| `GET /assessments/{id}/next-question` | Founder | The current next question (for resume). `{next_question | null}`. Cross-workspace / non-existent → 404. |
| `POST /assessments/{id}/answers` | Founder | Body `{question_key, value}`. 404 if not this workspace's in_progress assessment; `INVALID_ANSWER` (422) if `question_key` isn't the **current applicable `next_question`** or the value fails validation; **upsert** the answer (a resubmit of the current question overwrites); return `{next_question | null}`. Forward-flow only — editing an already-answered prior question (whose change could invalidate downstream `show_if`) is deferred; the FE runner is single-question-forward with "Save & exit"/resume. |
| `POST /assessments/{id}/complete` | Founder | Gate: every applicable question answered, else `422 ASSESSMENT_INCOMPLETE` (with the next unanswered key). Score → results → side-effects (§5). Idempotent: re-complete returns the stored results without re-enqueuing. Returns `{status, dimension_scores, overall_provisional, narrative}`. |
| `GET /assessments?status=` | member | List the workspace's assessments (id, type, status, started/completed, overall). Past Results. |
| `GET /assessments/{id}` | member | Detail: answers grouped by dimension + results. |
| `GET /assessments/compare?ids=a,b,c` | member | `dimension_scores` for up to 3 **completed** assessments of this workspace (radar overlay). >3 or cross-workspace ids → 422/404. |

## 7. Errors

Reuse the shipped `AppError` taxonomy; add:

| Code | HTTP | When |
|---|---|---|
| `INVALID_ANSWER` | 422 | answer value fails type/option/range validation, or isn't the applicable current question |
| `ASSESSMENT_INCOMPLETE` | 422 | `complete` before all applicable questions are answered; body names the next unanswered key |
| `ASSESSMENT_NOT_FOUND` | 404 | id not found / not in this workspace (reuse `NotFound` with this framing) |

Founder-only endpoints hit by a non-founder member reuse `FORBIDDEN` (403, via `require_role`).

## 8. Events / jobs / notifications

- **Events:** `assessment.completed` (consumed later by Health Score + Roadmap).
- **Jobs:** `healthscore.recalculate`, `roadmap.replan` (`JobDispatcher` stub rows; drained in Modules 05/06). Pollable at `GET /api/v1/jobs/{id}`.
- **Notifications:** the quarterly check-in cron is deferred (Notifications module); the `quarterly` type + manual start exist now.

## 9. Testing

- **TDD**, real Postgres + per-test rollback, factory helpers (`create_assessment`, `create_answer`).
- **Engine unit tests:** `show_if` evaluation (field + prior-answer conditions, all/any), `next_question` ordering + skip, `validate_answer` per type.
- **Scoring unit tests:** per-dimension math counts only applicable/answered; provisional overall = mean; narrative picks top/low.
- **Endpoint/integration:** start creates initial / resumes in_progress / later = quarterly; answer autosave + upsert (re-answer overwrites); complete gate (missing → 422 with next key); complete scores + writes results + sets `assessment_pending=False` (initial) + emits event + enqueues exactly 2 jobs + idempotent re-complete; results list + detail + compare (≤3); tenancy (member of workspace A can't touch B's assessment; a non-founder member is read-only — 403 on mutate, 200 on read).
- **Live E2E extension** (`e2e/test_assessment.py`): onboard a founder → complete onboarding → start assessment → answer through to the end (driving the adaptive `next_question` loop) → complete → assert `assessment_pending` is now false (via `/onboarding/state` or `/auth/me`), results returned, and 2 `queued` recalc jobs pollable.

## 10. Plan shape

One implementation plan (`writing-plans`), ~11 TDD tasks, executed subagent-driven, same
rhythm as the prior modules:

1. Enums + `assessments`/`assessment_answers`/`assessment_results` models + factories
2. Alembic migration `0004_assessment` (incl. partial-unique in_progress index)
3. Question bank v1 (`bank.py`) + `Question`/`ASSESSMENT_BANK`
4. Engine: `is_applicable` / `next_question` / `validate_answer`
5. Scoring: `score(...)`
6. `POST /assessments` (start/resume) + workspace/role wiring + router mount
7. `GET /{id}/next-question`
8. `POST /{id}/answers` (validate + upsert autosave)
9. `POST /{id}/complete` (gate + score + results + events + jobs + assessment_pending + idempotency)
10. `GET /assessments`, `GET /{id}`, `GET /compare`
11. Live E2E assessment journey + consolidated SOP

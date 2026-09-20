# Module 09 — Validation Hub

**Date:** 2026-09-20
**Module:** 09 Validation Hub (single slice)
**Status:** Approved design (all checkpoints answered by the lead) → implementation plan next
**Base branch / PR target:** `develop`
**Depends on:** nothing new. Reads nothing from other modules; feeds Module 08 personas later.

---

## 1. Context & Scope

### Sources

1. **Technical PRD** — `C:\Users\User\Desktop\Cofoundaz_Technical_PRD.md` (the repo docs reference
   `../Cofoundaz_Technical_PRD.md`; that path does not exist on this machine).
   - Module 09 — **lines 455–476** (09.1 overview through the API/entities/events block)
2. Handoff brief — `docs/handoff/module-09-validation-hub.md`
3. Design decisions — agreed with the lead on GitHub (the Module 09 design issue: v1 cut, public
   survey-response shape, question schema, status transitions, data-model details)

The brief is an orientation document, not the spec, by its own header. Where it disagrees with the
PRD, the PRD wins (see §9, waivers).

### What this module is

The "prove it before you build it" workspace. A founder records the **assumptions** their business
rests on, runs **experiments** to test them, captures what customers said in **interviews**, and
collects answers from the public through **surveys**. It is the evidence layer that Module 08's
persona work will later draw on.

### This slice delivers

- **Assumptions** — statement, risk level, and a status that moves freely around a four-column
  board: untested, testing, validated, invalidated. Evidence count is derived, never stored.
- **Experiments (including smoke tests)** — the record and its numbers: type, free-form config,
  status, and metrics. Plus a stats read that derives the funnel from those metrics.
- **Interviews** — who was interviewed, their segment, the date, notes, key quotes, a verdict of
  supports / contradicts / neutral, and links to the assumptions they bear on.
- **Surveys** — a question set stored as validated JSON, an unguessable public link, public
  response submission, and per-question analytics with a completion rate.
- **Two public routes** — read the survey form, and submit an answer set. Neither requires a login,
  and neither returns any workspace data.
- **Job stubs** — `POST /validation/synthesize` and `POST /validation/scripts/generate` enqueue a
  job and return its metadata. Nothing runs them yet.
- **Events** — `validation.assumption.validated` and `validation.assumption.invalidated`, published
  through `event_bus.publish(db, event, payload)`. No consumer yet.

### Non-goals (this slice)

- **MVP feedback (PRD 09.7)** — cut from v1 entirely, agreed with the lead. No table, no routes.
- **The AI Insight Synthesizer and AI interview-script generation** — need Module 03. v1 enqueues
  the job and returns metadata, the same seam as Module 17's certificate job.
- **`validation.insight.detected`** — the PRD lists this event, but nothing in v1 detects an
  insight; it arrives with the synthesizer.
- **Hosted smoke-test pages** at `{slug}.cofoundaz.site` (PRD 09.2) — front-end and hosting work.
  v1 stores the record and accepts its metrics; nothing serves a page.
- **Charts, QR codes and funnel drawings** — the API returns counts and rates; drawing is the
  front end's job.
- **Notifications** (*"New pattern detected…"*, *"{test} just passed {n} signups"*) — Module 20.
- **Rich text** — notes and quotes are plain text columns. No editor format is imposed.

---

## 2. Access

**Founders and team members only** — PRD line 457 (*"Access: F, TM (granted)"*), confirmed by the
lead.

- Every member route uses `require_role(MembershipRole.founder, MembershipRole.team_member)` plus
  `get_verified_user`, the same pairing Module 17 uses.
- Mentor, accountant, legal advisor, business consultant and investor receive **403** on every
  member route.
- Not a member of the workspace → **403**. Unauthenticated → **401**. Email not verified → **403**.

### Everything is scoped to one workspace

Every read and write filters on `startup_id`, taken from the caller's membership, never from the
request body. A record that exists but belongs to another workspace returns the same **404** as a
record that does not exist, so ids cannot be probed.

Unlike Module 17, records here are **shared within the workspace**, not private to one member: any
founder or team member of the workspace can read and edit its assumptions, experiments, interviews
and surveys. Only survey responses come from outside, and they belong to the survey.

### The public exception

Two routes carry **no authentication at all** (§4):

- `GET /api/v1/validation/surveys/{token}` — the form a respondent fills in
- `POST /api/v1/validation/surveys/{token}/responses` — their answers

They are addressed by an unguessable token, not by a survey id, are rate limited, and return
nothing about the workspace, the owner, or anyone else's answers.


---

## 3. Data model — five tables (+ migration)

All five use `UUIDMixin` + `TimestampMixin`, following `app/db/models/business.py`. Every foreign
key column carries its own `index=True`, the convention every migration in this project follows.

JSONB is used where the shape is free-form or list-like (`config`, `metrics`, `questions`,
`answers`, `key_quotes`, `assumption_ids`), matching `business_records.data`.

### `assumptions`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `UUIDMixin` |
| `startup_id` | UUID FK → `startups` | `ondelete=CASCADE`, indexed |
| `statement` | Text | what the founder believes |
| `risk` | `RiskLevel` enum | `native_enum=False`, length 20 |
| `status` | `AssumptionStatus` enum | defaults to `untested` |
| `created_at`, `updated_at` | timestamptz | `TimestampMixin` |

**No `evidence_count` column.** The PRD lists one; it is derived on read instead (§5), so it cannot
drift from the rows it counts — the same reasoning as deriving course progress in Module 17
(§9, W2).

### `experiments`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `startup_id` | UUID FK → `startups` | `ondelete=CASCADE`, indexed |
| `name` | String(255) | |
| `type` | `ExperimentType` enum | smoke tests are `smoke_test` |
| `config` | JSONB | free-form, defaults to `{}` |
| `status` | `ExperimentStatus` enum | defaults to `draft` |
| `metrics` | JSONB | defaults to `{}`; `visits`, `signups` and any other counters |
| `assumption_ids` | JSONB | list of assumption ids, defaults to `[]` |
| `created_at`, `updated_at` | timestamptz | |

### `interviews`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `startup_id` | UUID FK → `startups` | `ondelete=CASCADE`, indexed |
| `interviewee` | String(255) | |
| `segment` | String(120), nullable | free-text tag, used by the list filter |
| `held_on` | Date | |
| `notes` | Text | plain text, defaults to `''` |
| `key_quotes` | JSONB | list of strings, defaults to `[]` |
| `verdict` | `InterviewVerdict` enum | supports / contradicts / neutral |
| `assumption_ids` | JSONB | list of assumption ids, defaults to `[]` |
| `created_at`, `updated_at` | timestamptz | |

### `surveys`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `startup_id` | UUID FK → `startups` | `ondelete=CASCADE`, indexed |
| `title` | String(255) | |
| `questions` | JSONB | validated question list (§4), defaults to `[]` |
| `status` | `SurveyStatus` enum | draft / open / closed, defaults to `draft` |
| `token_hash` | String(64), nullable | SHA-256 of the public token; unique, indexed |
| `created_at`, `updated_at` | timestamptz | |

`token_hash` is null until the survey is first opened (§4). Only the hash is ever stored.

### `survey_responses`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `survey_id` | UUID FK → `surveys` | `ondelete=CASCADE`, indexed |
| `startup_id` | UUID FK → `startups` | `ondelete=CASCADE`, indexed; copied from the survey |
| `answers` | JSONB | `{question_id: value}` |
| `submitted_at` | timestamptz | |
| `created_at`, `updated_at` | timestamptz | |

**No `user_id`.** Respondents are anonymous members of the public, and nothing about them is
recorded: no IP address, no browser details (§9, D3). `startup_id` is carried so every read stays
workspace-scoped like the rest of the codebase; it is always copied from the survey, never supplied
by the respondent (§9, D4).

### Enums

In `app/db/models/enums.py`, all `enum.StrEnum`:

- `RiskLevel(low | medium | high)`
- `AssumptionStatus(untested | testing | validated | invalidated)`
- `ExperimentType(smoke_test | landing_page | ad_test | other)`
- `ExperimentStatus(draft | live | ended)`
- `InterviewVerdict(supports | contradicts | neutral)`
- `SurveyStatus(draft | open | closed)`

### Migration

- **`0026_validation`**, chaining off the live `develop` head at build time. The head today is
  `0025_roadmap_milestone_due_idx`. Heads move as other modules merge, so this is re-checked and
  re-pointed immediately before pushing, and renumbered if something merges first.
- `created_at` / `updated_at` carry `server_default=sa.text("now()")`.
- `CHECK` constraints, if any, pass a short name and let `app/db/base.py`'s naming convention build
  the full one.
- `alembic heads` shows exactly one head before pushing.

---

## 4. The public survey surface

This is the security-sensitive part of the module. It copies the Module 18 public share/sign
pattern exactly (`app/services/documents/shares.py`, `create_share` and `open_shared`), and the
lead signed the shape off before implementation.

### The token

- Generated with `secrets.token_urlsafe(32)`, the same call the document share and signing links
  use.
- **Only the SHA-256 hash is stored**, via `hash_token` from `app/services/auth/sessions.py`.
- It is created the first time the survey moves to `open`, and the raw value is returned **once**,
  in that response. It is never returned again and cannot be recovered from the database.
- Re-opening a closed survey reuses the existing token. Rotation is not in v1 (§9, follow-ups).

### The two public routes

| Method | Path | Returns |
|---|---|---|
| GET | `/api/v1/validation/surveys/{token}` | `{"title": …, "questions": [...]}` — nothing else |
| POST | `/api/v1/validation/surveys/{token}/responses` | `{"received": true}` — nothing else |

Both have **no authentication dependency at all**, exactly like `GET /sign/{token}`.

### Uniform 404

A single, identical 404 answers every one of: unknown token, a survey still in `draft`, and a
`closed` survey. Nothing distinguishes "no such survey" from "not accepting answers", so tokens
cannot be probed.

### What the response may never contain

The survey id, the workspace, the owner, any member's name or email, response counts, other
people's answers, or timestamps of other activity. The GET returns the title and the question list
only; the POST returns an acknowledgement only.

### Answer validation

Answers are checked against the survey's own question list before being stored, in the service, the
way `validate_sections` checks document sections:

- an answer for an unknown question id → **422**
- a missing answer for a question marked required → **422**
- a value of the wrong shape for the question type → **422**
  - `choice` — must be one of that question's options
  - `scale` — an integer 1–5
  - `nps` — an integer 0–10
  - `open` — text, at most **4,000 characters**
- more answers than the survey has questions → **422**

The 422 body carries the standard envelope with per-question field errors. Those name only the
respondent's own answers, never anything about the workspace.

### Limits (agreed with the lead)

- at most **50 questions** per survey, enforced when the survey is created or updated
- at most **20 options** per `choice` question
- at most **4,000 characters** per `open` answer

### Rate limiting

`POST /validation/surveys/{token}/responses` carries **`@limiter.limit("20/minute")`** — the first
per-route limit in the project. Everything else keeps the global default
(`RATE_LIMIT_PER_MINUTE`, 120/minute) applied by `SlowAPIMiddleware`.

The app's key function (`app/main.py`) falls back to the caller's IP address when there is no
signed-in user, so an anonymous respondent is limited per address, which is what the lead asked
for.

**One mechanical change this requires** (§9, D5): `limiter` is currently created in `app/main.py`,
which imports the endpoint modules, so an endpoint module cannot import it back without a circular
import. The `Limiter(...)` construction moves to `app/core/rate_limit.py`, and `app/main.py`
imports it from there. Behaviour, key function and default limit are unchanged.

### Repeat submissions

Allowed. Nothing de-duplicates responses in v1; spam control is the rate limit. De-duplication can
come later if it is ever needed (§9, follow-ups).


---

## 5. API — `/api/v1/validation`

Every route below is member-only: `require_role(founder, team_member)` + `get_verified_user`, the
standard envelope, and everything scoped to the caller's workspace. The two public routes in §4 are
the only exceptions.

| Method | Path | Does |
|---|---|---|
| GET | `/validation/assumptions` | List, newest first; optional `status` and `risk` filters. Each carries a derived `evidence_count` |
| POST | `/validation/assumptions` | Create (201). Starts as `untested` unless a status is given |
| PATCH | `/validation/assumptions/{id}` | Edit statement, risk or status. A move into validated/invalidated emits an event |
| GET | `/validation/experiments` | List, newest first; optional `type` and `status` filters |
| POST | `/validation/experiments` | Create (201) |
| PATCH | `/validation/experiments/{id}` | Edit name, type, config, status, metrics or assumption links |
| GET | `/validation/smoke-tests/{id}/stats` | Funnel read derived from `metrics`; 404 unless it is a `smoke_test` in this workspace |
| GET | `/validation/interviews` | List, most recent `held_on` first; optional `segment`, `verdict` and `assumption_id` filters |
| POST | `/validation/interviews` | Create (201) |
| PATCH | `/validation/interviews/{id}` | Edit any field |
| GET | `/validation/surveys` | List, newest first, each with its response count |
| POST | `/validation/surveys` | Create (201), as a `draft` |
| PATCH | `/validation/surveys/{id}` | Edit title, questions or status. Opening returns the public token **once** |
| GET | `/validation/surveys/{id}/analytics` | Per-question counts and the completion rate |
| POST | `/validation/synthesize` | **Stub** (202): enqueue `validation.synthesize`, return job metadata |
| POST | `/validation/scripts/generate` | **Stub** (202): enqueue `validation.scripts.generate`, return job metadata |

Unknown ids, and ids belonging to another workspace, both return the same **404**.

### No delete routes in v1

The brief's endpoint table lists reads, creates and edits only, and this spec follows it. An
experiment is `ended`, a survey is `closed`, and an assumption is `invalidated` rather than
removed, so history survives. Deletion is a follow-up (§9).

### `evidence_count` is derived, never stored

For one assumption it is the number of experiments **plus** interviews in the same workspace whose
`assumption_ids` contain that assumption's id. It is computed when assumptions are read, so it can
never disagree with the rows it counts (§9, D2).

### Assumption status transitions

Any status may move to any other: the board lets a card be dragged back, and forcing a fixed path
would fight it.

`validation.assumption.validated` and `validation.assumption.invalidated` fire **only** when the
status actually changes **into** that state. Saving the same status again emits nothing, so a
client retrying a request cannot double-fire. Payload:
`{startup_id, assumption_id, status, actor_id}`.

### Smoke-test stats

Derived from `metrics`, never stored separately:

- `visits` and `signups` — read from `metrics`, each 0 when absent
- `conversion` — `round(100 × signups ÷ visits, 1)`, and `0.0` when there are no visits, so an
  empty test never divides by zero

### Survey analytics

For the workspace's own view of a survey:

- `responses` — how many response rows exist
- `completion_rate` — `round(100 × responses answering every required question ÷ responses)`, and
  `0` when there are none
- `questions` — one entry per question, carrying its id, type and prompt, plus:
  - `choice` — a count per option, including options nobody picked
  - `scale` and `nps` — a count per value, plus the average to one decimal place
  - `open` — how many answered it, and no text (raw answers are a follow-up, §9)

### Job stubs

Both mirror `POST /roadmap/generate`: **202**, `job_dispatcher.enqueue(db, type, payload,
startup_id)`, and a body of `{"job_id": …, "status": …}`. The job stays `queued`; nothing drains it
until Module 03, exactly like Module 17's certificate job.

- `validation.synthesize` — payload `{startup_id}`
- `validation.scripts.generate` — payload `{startup_id, assumption_ids}`

### Committing

`get_db()` does not commit. **Every write handler calls `db.commit()`** after the service call,
including the public response endpoint. Unit tests cannot catch a missing commit because they run
inside one rolled-back transaction; the live e2e is what catches it.

---

## 6. Cross-cutting

- **Service layer** — `app/services/validation/`. Services `flush()`; they never commit.
- **Request models** — `app/schemas/validation.py`, Pydantic, as in `app/schemas/learning.py`.
  Responses are plain dicts built by the service, the same as Learning and Business Builder.
- **Errors** — reuse `NotFound` (404), `Forbidden` (403) and `VALIDATION_ERROR` (422). No new error
  codes.
- **Events** — `event_bus.publish(db, event, payload)`, with `db` as the first argument. No
  consumer subscribes to validation events yet, which is expected.
- **Jobs** — `job_dispatcher.enqueue(db, type, payload, startup_id)`; enqueue-only.
- **Rate limiting** — the `Limiter(...)` construction moves from `app/main.py` to
  `app/core/rate_limit.py` so endpoint modules can import it; `app/main.py` imports it from there.
  Same key function, same default limit, no behaviour change. The only per-route limit is
  `20/minute` on the public response route (§4).
- **Config / secrets** — none. No new environment variables.
- **Tenancy** — `startup_id` comes from the caller's membership on every member route, and from the
  survey on the public one. It is never read from a request body.


---

## 7. Testing

**Unit and API (real Postgres, per-test rollback, TDD):**

- **Access matrix** — founder and team member allowed on every member route; mentor, accountant,
  legal advisor, business consultant and investor → 403 on every one; non-member → 403;
  unauthenticated → 401; unverified email → 403.
- **Workspace isolation** — another workspace's assumption, experiment, interview or survey → 404
  on read, edit and analytics; lists never include it.
- **Assumptions** — create defaults to `untested`; filters by status and risk; free transitions in
  both directions; the event fires only on a change **into** validated or invalidated, and not when
  the same status is saved again.
- **`evidence_count`** — counts linked experiments and interviews, is 0 with none, and ignores
  links from another workspace.
- **Experiments** — create, edit, filters; metrics are stored and returned unchanged.
- **Smoke-test stats** — conversion from metrics; `0.0` when there are no visits (no division by
  zero); 404 for an experiment that is not a smoke test.
- **Interviews** — create, edit, filters by segment, verdict and assumption.
- **Surveys** — question validation, the caps (>50 questions, >20 options → 422), opening returns
  the token exactly once, re-opening does not change it, closing stops submissions.
- **The public routes** — the heart of the module:
  - submitting an answer set works with no authentication at all
  - the reply is exactly `{"received": true}` and contains no title, owner, workspace, id or count
  - the public GET returns only the title and questions
  - unknown token, draft survey and closed survey all give the **same** 404 body, byte for byte
  - answers are validated: unknown question id, missing required answer, wrong type, an option
    outside the list, an over-long open answer → 422
  - a second submission from the same person is accepted, since repeats are allowed
  - no row records anything about the respondent
- **Analytics** — per-option counts including unpicked options, per-value counts and averages for
  scale and NPS, answered counts for open questions, and the completion rate, including the
  no-responses case.
- **Job stubs** — each returns 202 with a job id, writes exactly one `jobs` row of the right type,
  and leaves it `queued`.
- **Rate limiting** — the public response route carries the `20/minute` limit, tested the way
  `tests/api/test_rate_limit.py` does it, with a small throwaway app rather than by hammering the
  real route.
- **Migration** — applies cleanly and leaves exactly one alembic head.

**Test-harness rule (from the lead):** unit tests use the `db` fixture, or no database at all. They
never open their own connection to the application's database, which passes locally and fails on
CI's fresh database. The one deliberate exception stays the concurrency pattern, which uses the
session-scoped `engine` fixture, as in `tests/services/learning/test_concurrency.py`.

**Sanity:** the full suite green on a freshly migrated database; coverage at or above 95%.

**Smoke:** validation routes added to `e2e/test_smoke.py`'s route list.

**Live e2e (`e2e/test_validation.py`):** a founder creates an assumption → creates and opens a
survey → **a public response is submitted over HTTP with no authentication** → analytics reflect it
→ the assumption is marked validated. Every body captured to `e2e/_captures/validation/`.

**FE integration guide:** `docs/fe-integration-guide-validation.md`, built from those captures only.
It must state plainly that the public link is the only unauthenticated surface, and what it never
returns.

---

## 8. File structure

| File | Change |
|---|---|
| `app/db/models/enums.py` | add the six new enums |
| `app/db/models/validation.py` | **new** — `Assumption`, `Experiment`, `Interview`, `Survey`, `SurveyResponse` |
| `app/db/models/__init__.py` | register the models |
| `alembic/versions/0026_validation.py` | **new** migration (number settled at build time) |
| `app/services/validation/__init__.py` | **new** package |
| `app/services/validation/questions.py` | **new** — question and answer validation |
| `app/services/validation/service.py` | **new** — assumptions, experiments, interviews, surveys |
| `app/services/validation/public.py` | **new** — token lookup and public response submission |
| `app/schemas/validation.py` | **new** — request models |
| `app/api/v1/endpoints/validation.py` | **new** — member routes + the two public routes |
| `app/api/v1/api.py` | register the router at `prefix="/validation"` |
| `app/core/rate_limit.py` | hold the `Limiter` instance (moved from `app/main.py`) |
| `app/main.py` | import `limiter` from `app/core/rate_limit.py` |
| `tests/…/validation/` | unit tests per §7 |
| `e2e/test_validation.py` (+ `e2e/_captures/validation/`) | live journey |
| `e2e/test_smoke.py` | add validation routes |
| `docs/fe-integration-guide-validation.md` | verified guide |
| `docs/sop/<date>-validation-hub.md` | SOP |
| `docs/checklist/PROJECT_CHECKLIST.md` | tick Module 09 |

---

## 9. Decisions & waivers

All of the following were agreed with the lead on GitHub before implementation.

- **D1 — The public response endpoint copies Module 18 exactly.** Unguessable token, only its hash
  stored, uniform 404, acknowledgement-only reply, no authentication dependency. No new pattern is
  invented for it.
- **D2 — `evidence_count` is derived on read**, not stored, so it cannot drift from the experiments
  and interviews it counts.
- **D3 — Respondents are anonymous.** No `user_id`, no IP address, no browser details; only
  `submitted_at`. Repeat submissions are accepted, and the rate limit is the spam control.
- **D4 — `survey_responses` also carries `startup_id`**, copied from the survey, so every read is
  workspace-scoped like the rest of the codebase. The respondent never supplies it.
- **D5 — The `Limiter` instance moves to `app/core/rate_limit.py`**, because `app/main.py` cannot be
  imported from an endpoint module. Behaviour is unchanged. The public response route carries the
  project's first per-route limit, `20/minute`.
- **D6 — A public read is included** (`GET /validation/surveys/{token}`), under the same no-leak
  rules, because the front end cannot render the form without the questions.
- **D7 — Questions are JSONB with server-assigned ids**, validated in the service like document
  sections, capped at 50 questions, 20 options per choice question, and 4,000 characters per open
  answer.
- **D8 — Assumption transitions are free in any direction**, because the board allows dragging a
  card back. Events fire only on an actual change into validated or invalidated.
- **D9 — Assumption links are JSONB arrays of ids**, validated to exist in the same workspace. A
  join table is premature; it can be migrated to later if querying by experiment becomes common.
- **D10 — MVP feedback (PRD 09.7) is cut from v1 entirely** — no table, no routes, no clustering.

### Waivers

- **W1 — The PRD gives the public route as `POST /validation/surveys/{id}/responses`.** v1
  addresses it by an unguessable token instead, because a survey id is enumerable and this route is
  unauthenticated. Approved by the lead.
- **W2 — The PRD's entity list has `evidence_count` on `assumptions`.** It is derived on read here
  rather than stored (D2).
- **W3 — The PRD lists `validation.insight.detected`.** Nothing in v1 detects an insight, so nothing
  emits it. It arrives with the synthesizer.

### Follow-ups

- Replace the two job stubs with real work when Module 03 lands (`validation.synthesize`,
  `validation.scripts.generate`).
- Hosted smoke-test pages at `{slug}.cofoundaz.site`, and the live stats they would feed.
- MVP feedback and theme clustering (PRD 09.7).
- Subscribe Module 20 to the validation events, once someone decides what those notifications say.
- A members-only route to read raw responses, so open answers can be read as well as counted.
- Delete routes, if the team decides records should be removable rather than ended or invalidated.
- Survey token rotation or expiry, and response de-duplication, if link abuse ever appears.
- A join table for assumption links, if "everything linked to experiment X" becomes a common query.

### Settled by the PRD or house rules

- **Access is founders and team members** — PRD line 457.
- **`startup_id` on every table** — house tenancy rule; the PRD entity list omits it.
- **Notes and quotes are plain text** — the PRD calls for rich text in the UI, which is a front-end
  concern; the API stores and returns text unchanged.

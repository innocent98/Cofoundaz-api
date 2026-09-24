# SOP — Validation Hub (Module 09)

**What shipped** — The "prove it before you build it" workspace: assumptions on a four-column
board, experiments and smoke tests with their metrics, interview notes, and surveys that collect
answers **from the public**. Sixteen member routes under `/api/v1/validation` for founders and team
members only, plus **two routes with no authentication at all** — reading a survey form by token,
and submitting an answer set. Five new tables (`assumptions`, `experiments`, `interviews`,
`surveys`, `survey_responses`) in migration `0035_validation`.

Commits (branch `feat/validation-hub`, PR into `develop`):

- `feat(validation): assumptions, experiments, interviews, surveys and responses tables` — Task 1
- `feat(validation): survey question and answer validation` — Task 2
- `feat(validation): assumptions and experiments service` — Task 3
- `feat(validation): interviews, surveys, public token and analytics` — Task 4
- `feat(validation): public survey token surface; move Limiter for per-route limits` — Task 5
- `feat(validation): /validation endpoints, public survey routes and router registration` — Task 6
- `test(validation): smoke routes and live validation journey` — Task 7
- two `fix(validation): renumber migration …` commits, as `develop` moved twice during the build

Design: `docs/superpowers/specs/2026-09-20-validation-hub-design.md` (decisions D1–D10, waivers
W1–W3, agreed with the lead on GitHub). Plan:
`docs/superpowers/plans/2026-09-20-validation-hub.md`.

## Why

PRD Module 09 asks for a place where a founder tests what their business rests on before building
it: assumptions with a risk level and a status, experiments that produce evidence, interviews, and
surveys answered by people outside the product. Nothing in the codebase held any of that before.

v1 is deliberately narrow: no AI synthesizer, no interview-script generation (both stubbed as
enqueued jobs until Module 03), no hosted smoke-test pages, no MVP feedback, and no notifications.

## How

**The public surface is the security-sensitive part, and it copies Module 18 exactly.**
`app/services/validation/public.py` mirrors `app/services/documents/shares.py`:

- A survey is addressed by an **unguessable token** (`secrets.token_urlsafe(32)`), never by its id,
  because an id is enumerable and this route has no authentication.
- **Only the SHA-256 hash is stored** (`hash_token`, the same helper the share and signing links
  use). The raw token is returned **once**, in the response that first opens the survey, and cannot
  be recovered afterwards.
- **Unknown token, draft survey and closed survey all raise the same `NotFound`**, so a token
  cannot be probed.
- **The replies carry nothing else.** The public read returns the title and questions; the
  submission returns `{"received": true}`. No survey id, no workspace, no owner, no counts, no
  other answers. The live e2e asserts the workspace id and survey id do not appear in the body.
- **Nothing about the respondent is recorded** (spec D3): no `user_id`, no IP address, no browser
  details — only `submitted_at`. Repeat submissions are accepted; the rate limit is the spam
  control.

**The `Limiter` moved so a route can carry its own limit.** `POST /validation/surveys/{token}/
responses` carries `@limiter.limit("20/minute")`, the first per-route limit in the project. That
decorator needs the `Limiter` object at import time, and it lived in `app/main.py`, which imports
the endpoint modules — importing it back would have been circular. The instance and its key
function now live in `app/core/rate_limit.py`, and `app/main.py` imports `limiter` from there.
Behaviour, key function and the 120/minute default are unchanged. `tests/api/test_rate_limit_key.py`
(added on `develop` during this build) had its import updated to the new home.

**Question and answer validation is service-side, not modelled as tables** (spec D7).
`app/services/validation/questions.py` validates a question list (types `choice`, `scale`, `nps`,
`open`; server-assigned ids kept across edits; at most 50 questions and 20 options) and an answer
set against that survey's own questions (required answers present, values of the right shape,
choices from the offered options, open answers at most 4,000 characters). Every message it raises
is safe to show a stranger, because the public endpoint surfaces them.

**Evidence counts are derived, never stored** (spec D2). `evidence_counts` counts the experiments
and interviews in the same workspace whose `assumption_ids` include a given assumption, computed on
each read, so the number cannot drift from the rows behind it — the same reasoning as deriving
course progress in Module 17.

**Assumption transitions are free; events are not.** Any status may follow any other, because the
board must allow dragging a card back. `validation.assumption.validated` and
`validation.assumption.invalidated` fire **only** on an actual change into those states, so a
client re-saving the same status cannot double-fire them. Published via
`event_bus.publish(db, event, payload)`; no consumer subscribes yet.

**Links are validated, JSONB arrays.** `link_ids` rejects any assumption id that is not in the
caller's workspace (422), which is what stops one workspace referencing another's records. A join
table was judged premature (spec D9).

**Derived numbers never divide by zero.** Smoke-test conversion is `round(100 × signups ÷
visits, 1)` and `0.0` with no visits; survey `completion_rate` is `0` with no responses; a scale or
NPS average is `0.0` when nobody answered.

## What's involved

**Data model / migration**

- `alembic/versions/0035_validation.py` — five brand-new tables, no lock on any existing table.
  Autogenerated from `app/db/models/validation.py`; only the revision id, `down_revision`, Create
  Date and docstring hand-edited. Renumbered twice (0026 → 0034 → 0035) as Modules 03, 08 and 10
  merged ahead of it; chains off `0034_campaigns_segments`, single head, `alembic check` clean.
  - `assumptions` — statement, risk, status (defaults `untested`); `ix_assumptions_startup_id`.
  - `experiments` — name, type, JSONB `config` / `metrics` / `assumption_ids`, status (defaults
    `draft`); `ix_experiments_startup_id`.
  - `interviews` — interviewee, segment, `held_on`, notes, JSONB `key_quotes` and
    `assumption_ids`, verdict; `ix_interviews_startup_id`.
  - `surveys` — title, JSONB `questions`, status, nullable unique `token_hash`;
    `ix_surveys_startup_id`, `uq_surveys_token_hash`.
  - `survey_responses` — `survey_id`, `startup_id`, JSONB `answers`, `submitted_at`;
    `ix_survey_responses_survey_id`, `ix_survey_responses_startup_id`. **No `user_id`.**
  - Every foreign key `ON DELETE CASCADE`; `created_at` / `updated_at` with `server_default now()`.
- `app/db/models/enums.py` — `RiskLevel`, `AssumptionStatus`, `ExperimentType`, `ExperimentStatus`,
  `InterviewVerdict`, `SurveyStatus`.

**Endpoints** — `app/api/v1/endpoints/validation.py`, registered in `app/api/v1/api.py` at
`prefix="/validation"`. Request models in `app/schemas/validation.py`.

| Method | Path | Access |
|---|---|---|
| GET / POST | `/validation/assumptions` | member |
| PATCH | `/validation/assumptions/{id}` | member |
| GET / POST | `/validation/experiments` | member |
| PATCH | `/validation/experiments/{id}` | member |
| GET | `/validation/smoke-tests/{id}/stats` | member |
| GET / POST | `/validation/interviews` | member |
| PATCH | `/validation/interviews/{id}` | member |
| GET / POST | `/validation/surveys` | member |
| PATCH | `/validation/surveys/{id}` | member; opening returns `public_token` once |
| GET | `/validation/surveys/{id}/analytics` | member |
| **GET** | **`/validation/surveys/{token}`** | **public, no auth** |
| **POST** | **`/validation/surveys/{token}/responses`** | **public, no auth, 20/minute** |
| POST | `/validation/synthesize`, `/validation/scripts/generate` | member; 202 stubs |

All member routes bind `require_role(founder, team_member)` + `get_verified_user` and scope every
query to `membership.startup_id`. The two write endpoints among them, and the public submission,
call `db.commit()`.

**Service** — `app/services/validation/`
- `questions.py` — `validate_questions`, `validate_answers`, and the caps.
- `service.py` — assumptions, experiments (incl. `smoke_test_stats`), interviews, surveys (incl.
  the token lifecycle and `survey_analytics`), `link_ids`, `evidence_counts`, and the `serialize_*`
  views.
- `public.py` — `open_survey`, `public_view`, `submit_response`.

**Tests**
- `tests/db/test_validation_models.py` — defaults, the unique token rule, cascade delete, and that
  responses carry no `user_id`.
- `tests/test_validation_migration.py` — the migration applies with its named constraints; exactly
  one alembic head.
- `tests/services/validation/` — `test_questions.py`, `test_assumptions.py`, `test_experiments.py`,
  `test_interviews.py`, `test_surveys.py`, `test_public.py`.
- `tests/api/test_validation.py` — the access matrix across roles and routes, workspace isolation,
  the 201/404/422 cases, the public routes with no login, analytics, the job stubs, and that the
  public submit route carries its own rate limit.
- `e2e/test_smoke.py` — the fourteen validation route shapes.
- `e2e/test_validation.py` — the live journey, capturing twelve bodies to
  `e2e/_captures/validation/`.

## Verification

- **Validation tests: 144 passed** — models 6, migration 2, questions 8, assumptions 8,
  experiments 8, interviews 6, surveys 7, public 7, API 92 (15 test functions, parametrised across
  roles and routes).
- **Full project suite: 1,616 passed**, coverage **97.20%** (floor 95), run in the Python 3.11 test
  container with `REDIS_URL=redis://host.docker.internal:6379/0` so the Redis-backed auth tests can
  reach Redis.
- `ruff check app tests e2e`, `black --check app tests`, `mypy app` — all clean.
- Migration: single head `0035_validation`; `alembic upgrade head` applied on a database rebuilt
  from zero through `0034_campaigns_segments`; `alembic check` reports no drift.
- **Pending — live e2e.** `e2e/test_validation.py` and the smoke routes are written but have not
  been run on this machine; the e2e runner boots the app with Poetry on the host, which is not set
  up here. CI runs it on the PR, and the FE guide is regenerated from those captures.

## Operate / roll back

- No new environment variables, secrets or configuration.
- **Roll back** with `alembic downgrade 0034_campaigns_segments`, which drops `survey_responses`,
  `surveys`, `interviews`, `experiments` and `assumptions` with their indexes. **Lossy** — every
  assumption, experiment, interview, survey and public response is destroyed. Roll the code back
  together with the migration: with the tables gone, every validation route fails.
- **A survey's public link cannot be recovered or reissued.** Only the token's hash is stored, and
  v1 has no rotation. If a link is lost, create a new survey; if a link leaks, close the survey.
- `validation.synthesize` and `validation.scripts.generate` rows accumulate in `jobs` as `queued`;
  nothing drains them until Module 03. `jobs` has no foreign key to `startups`, so a downgrade does
  not remove them.
- The public submit route is limited to **20 requests a minute per IP address**. Tighten it in
  `app/api/v1/endpoints/validation.py` if abuse appears; the global default stays 120/minute.

## Follow-ups

- **Replace the AI stubs with real work when Module 03 lands** — the Insight Synthesizer and
  interview-script generation.
- **Regenerate `docs/fe-integration-guide-validation.md` from the real e2e captures** and delete
  its provenance note. Its values are currently illustrative.
- A members-only route to read raw survey responses, so open-text answers can be read, not only
  counted.
- Delete routes, if the team decides records should be removable rather than ended, closed or
  invalidated.
- Survey token rotation or expiry, and response de-duplication, if link abuse appears.
- A join table for assumption links, if "everything linked to experiment X" becomes a common query.
- Hosted smoke-test pages, MVP feedback and theme clustering (PRD 09.2, 09.7).
- Subscribe Module 20 to `validation.assumption.validated` / `.invalidated` once someone decides
  what those notifications say.

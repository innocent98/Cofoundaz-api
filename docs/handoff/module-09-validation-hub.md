# Handoff Brief — Module 09: Validation Hub

> **For:** Victoria, your second module on `cofoundaz-api` (Module 17 Learning Academy shipped 🎉).
> **Status:** not started. **Spec (authority):** `../Cofoundaz_Technical_PRD.md` → Module 09.
> **This brief is a starting point, not the spec.** It orients you, scopes v1, points you at the
> patterns to copy, and flags what to stub or check with your lead. You still run the normal loop:
> **brainstorm → spec → plan → build (TDD) → review → verify → document.**

---

## 1. What you're building

A **validation hub** — the "prove it before you build it" workspace. Founders track **assumptions**
(a kanban: Untested → Testing → Validated/Invalidated), run **experiments** (smoke tests), capture
**interviews** and run **surveys** that collect responses from the public, and review **MVP
feedback**. It's the evidence layer that feeds Business Builder personas (Module 08).

**Why this is a good second module:** it's CRUD-rich with a clear, self-contained data model (much
like Learning Academy), it re-uses two patterns you already know — **status-transition records**
(like enrollments/certificates) and a **public token endpoint** (like the document share/sign links
from Module 18) — and its AI pieces are cleanly **deferrable**, so you practice the "seam it, defer
it" discipline again without depending on anything unbuilt.

## 2. Access model

**Founder + Team Member (granted)** — standard `require_workspace` + `get_verified_user`, everything
scoped to the active workspace. **One exception:** submitting a **survey response is public**
(`POST /validation/surveys/{id}/responses`, no auth) — a respondent isn't a workspace member. Treat
that endpoint exactly like the public `POST /sign/{token}` from Module 18: unauthenticated,
addressed by an unguessable id/token, rate-limited, and it must **never leak** workspace data back to
the respondent (return only a thank-you/acknowledgement, not the survey owner or other responses).

## 3. Scope for v1

**In scope (all CRUD, per workspace):**
- **Assumptions tracker** — statement, `risk` (High/Med/Low), `status` (Untested/Testing/Validated/
  Invalidated), evidence count (derived), links to experiments. The kanban is just a filtered list
  by status; status changes emit events (below).
- **Experiments / smoke tests** — the **record + metrics**, not the hosted page: name, type, config
  (JSONB), status (Draft/Live/Ended), metrics (visits/signups/conversion, JSONB), plus a
  `GET /validation/smoke-tests/{id}/stats` read.
- **Interviews** — interviewee, segment tag, date, notes, key quotes, assumption links, verdict
  chips (Supports/Contradicts/Neutral). List + filters.
- **Surveys** — builder (question types: choice/scale/NPS/open, stored as JSONB), a **public**
  response-submission endpoint, and per-question response analytics (counts/completion — computed in
  the service, no charting).

**Defer / stub (write each into your spec's Deferred table with the reason):**
- **AI Insight Synthesizer** (`POST /validation/synthesize`) and **AI interview-script generation**
  (`POST /validation/scripts/generate`) — both are jobs needing Module 03 (AI). v1 **enqueues the
  job via the job dispatcher and returns metadata**; nothing renders yet (same as your certificate
  PDF stub).
- **Hosted smoke-test pages** at `{slug}.cofoundaz.site` — that's front-end + hosting infra, not
  this service. v1 stores the smoke-test **record** and accepts its **stats**; the live page is later.
- **MVP-feedback AI theme clustering** — defer the clustering; a plain feedback list/CRUD is fine for
  v1 if you include it at all (you may cut MVP feedback from v1 entirely — agree with your lead).
- **Notifications** ("new pattern detected", "smoke test passed N signups") — Module 20.

## 4. Data model (new migration — number settled at build time)

⚠️ **Migration coordination (you hit this on Module 17):** develop's head moves as modules merge.
Do **not** hard-code a number early — take the next free number against the **live head at build
time**, and whoever merges second renumbers to keep a single alembic head. (Right now the head is
`0023_learning`, your own module, once it merges — so yours will likely be `0024+`.)

From the PRD, mapped to our conventions (`UUIDMixin` + `TimestampMixin`, `Enum(..., native_enum=
False)`, JSONB for config/metrics/question-schemas; standalone `index=True` on every FK):

- **`assumptions`** — `id` · `startup_id` FK · `statement` · `risk` (enum) · `status` (enum) ·
  `evidence_count` (int, derived/maintained) · timestamps.
- **`experiments`** — `id` · `startup_id` FK · `type` (enum) · `config` (JSONB) · `status` (enum) ·
  `metrics` (JSONB) · timestamps. (Smoke tests are `type='smoke_test'`.)
- **`interviews`** — `id` · `startup_id` FK · `interviewee` · `segment` · `held_on` · `notes` ·
  `key_quotes` · `verdict` (enum) · `assumption_ids` (JSONB or a join) · timestamps.
- **`surveys`** — `id` · `startup_id` FK · `title` · `questions` (JSONB schema) · `status` (enum) ·
  a public `token`/slug (unguessable) · timestamps.
- **`survey_responses`** — `id` · `survey_id` FK · `answers` (JSONB) · `submitted_at`. (No
  `user_id` — respondents are anonymous public visitors.)

New enums (as the PRD implies): `RiskLevel(low|medium|high)`, `AssumptionStatus(untested|testing|
validated|invalidated)`, `ExperimentType(smoke_test|…)`, `ExperimentStatus(draft|live|ended)`,
`InterviewVerdict(supports|contradicts|neutral)`, `SurveyStatus(draft|open|closed)`.

## 5. Endpoints (`/api/v1/validation`)

Envelope + tenancy + `get_verified_user` throughout, **except** the public responses endpoint.

| Route | Access | Does |
|---|---|---|
| `GET/POST /validation/assumptions` · `PATCH /validation/assumptions/{id}` | member | Kanban CRUD; status change emits `validation.assumption.validated\|invalidated`. |
| `GET/POST /validation/experiments` · `PATCH …/{id}` | member | Smoke-test/experiment CRUD. |
| `GET /validation/smoke-tests/{id}/stats` | member | Funnel/conversion read from `metrics`. |
| `GET/POST /validation/interviews` · `PATCH …/{id}` | member | Interview notes CRUD + filters. |
| `GET/POST /validation/surveys` · `PATCH …/{id}` | member | Survey builder CRUD; `PATCH` to close. |
| `POST /validation/surveys/{id}/responses` | **public** | Submit a response (unauth, rate-limited, no data leak). |
| `GET /validation/surveys/{id}/analytics` | member | Per-question counts + completion rate. |
| `POST /validation/synthesize` · `POST /validation/scripts/generate` | member | **Stub:** enqueue a job, return metadata. |

**Events:** `validation.assumption.validated`, `validation.assumption.invalidated`,
`validation.insight.detected` (emit via `event_bus.publish(db, event, payload)` — note the **`db`
first arg**, the signature you fixed on Module 17; no consumer yet, that's fine).
**Jobs (stub):** `validation.synthesize`, `validation.scripts.generate` via the job dispatcher.

## 6. Mirror this shipped code

- **Kanban-ish CRUD with kinds + status + JSONB config:** `app/services/business/records.py` +
  `app/db/models/business.py` (the records registry — closest match to assumptions/experiments).
- **Your own Module 17:** `app/services/learning/` for service/endpoint/enum/test shape you already
  know.
- **Public token endpoint (for the survey-response route):** `app/services/documents/shares.py` +
  the public `POST /sign/{token}` in `app/api/v1/endpoints/documents.py` — copy the unguessable-token
  + uniform-404 + no-leak handling **exactly**.
- **202-job stub pattern:** `POST /roadmap/generate` (for synthesize/script-gen).
- **Tests:** `tests/api/test_business_*.py` and your own `tests/…/learning/` (the `_member` helper,
  envelope, real Postgres per-test rollback).

## 7. ⚠️ Senior checkpoints (settle in brainstorming)

1. **The public survey-response endpoint** — this is the one security-sensitive piece. Unauth +
   token-addressed + rate-limited + **no workspace data in the response**. Walk the shape past your
   lead before building. Copy the Module 18 public-sign pattern; don't invent your own.
2. **v1 surface** — assumptions + experiments + interviews + surveys is plenty. **MVP feedback**
   (09.7) can be cut from v1 or kept as plain CRUD — agree the cut. Don't build the AI synthesizer.
3. **Survey question schema** — store questions as JSONB with a small validated shape; don't
   over-model question types into tables.
4. **Assumption status transitions** — decide whether any transition is allowed or only a defined
   flow; emit the event only on validated/invalidated.

## 8. How to work (the standard loop)

1. **Brainstorm** with your lead — settle the v1 cut, the public-endpoint shape, the survey schema.
   Write a short spec in `docs/superpowers/specs/`.
2. **Plan** it into small TDD tasks in `docs/superpowers/plans/`.
3. **Build test-first**, small commits.
4. **Verify** with the four layers (§9).
5. **Document:** SOP (`docs/sop/`), captured-live FE guide
   (`docs/fe-integration-guide-validation.md` — **real captured responses, never schema guesses**),
   tick the checklist.
6. **PR into `develop`** (not `main` — that's the team convention; your Module 17 PR was retargeted
   for this). Rebase onto current `develop` before you push, and settle the migration number against
   the live head.

## 9. Definition of done (certified)

- **Unit/integration:** every endpoint + edge case (status transitions, public-response accepted +
  no-leak, survey analytics counts, assumption↔experiment links), real Postgres, per-test rollback,
  TDD.
- **Sanity:** full `make test` green on a fresh migrated DB; single alembic head.
- **Smoke:** validation routes added to `e2e/test_smoke.py`'s surface assertion.
- **Live E2E** (`make e2e`): a member creates an assumption → runs an experiment/survey → a **public
  response** is submitted over HTTP → analytics reflect it → the assumption is marked validated and
  emits its event — **real data over HTTP**, responses captured to `e2e/_captures/`.
- Gates green: `black`/`isort`/`ruff`/`mypy`/`pylint`/`bandit`; coverage ≥ 95%.
- SOP + FE guide + checklist updated.

## 10. Gotchas

- **The public survey-response endpoint is the risk.** Unauth + token + rate-limit + no data leak —
  return only an acknowledgement. This is the same discipline as the public document-sign link; a
  reviewer *will* check it leaks nothing.
- **Respondents are anonymous** — `survey_responses` has no `user_id`; everything else is scoped to
  `startup_id` (and member reads to the active workspace).
- **Event bus signature is `publish(db, event, payload)`** — the `db` first arg (the exact thing you
  fixed on Module 17). Emit your events; no consumer exists yet and that's fine.
- **Migration numbering** — take the live head's next number at build time; renumber if you merge
  second. You've done this once now.
- **GitGuardian** may flag the e2e signup password (advisory, non-required) — same known
  false-positive as before; not a blocker.

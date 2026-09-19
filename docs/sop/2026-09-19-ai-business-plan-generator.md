# SOP — AI Business Plan Generator (§08.11, Module 08 Slice 4 — final)

**What shipped** — the last open PRD sub-screen of Module 08 (Business Builder): a founder can
trigger `POST /business-builder/plan/generate` and get back a full, section-by-section AI-written
business plan, stored as a Module 18 `Document`. This closes out Module 08 (all 4 slices now
shipped) and clears the last PRD sub-screen that was dependency-blocked on Module 03 (AI
Co-Founder).

Commits (branch `feat/ai-business-plan-generator`, off `develop`):
`785e086` (design) → `070e9c0` (Task 1 — `business_plans` entity + migration `0026`) →
`acaa34f` (Task 2 — plan section defs + context gatherer + prompt builder) → `b1e401d` (Task 3 —
`business.plan.generate` worker) → `0290673` (Task 4 — generate/get endpoints +
`business.plan.generated` notification) → **this commit** (Task 5, final — live e2e + captures +
FE guide + SOP + checklist).

## Why

Module 08 Slice 3's checklist entry flagged the AI Business Plan Generator as "the ONLY Module 08
PRD sub-screen not yet shippable" — it needed an LLM integration that did not exist yet anywhere in
the codebase. Module 03 Slice 1 (`docs/sop/2026-09-19-llm-seam-assessment-narrative.md`) built that
integration (the provider-agnostic `LLMClient` seam) against a single, simple consumer (the
assessment narrative — one call, one field to overwrite). This slice is the first consumer that
needed something structurally bigger: **ten** LLM calls assembled into one coherent document, not
one. Rather than invent a new seam or a new document-storage shape, this slice reuses two
already-shipped pieces of infrastructure end to end for the first time together: the LLM seam
(Module 03) and the generic `Document` store (Module 18 Slice 1, whose `create_document(...,
ai_generated=True)` path existed specifically anticipating this use).

## How

**Fixed 10-section outline, not an LLM-decided structure.** `PLAN_SECTIONS`
(`app/services/business/plan_defs.py`) is a hardcoded tuple of `PlanSection{key, heading,
guidance}`: Executive Summary, Problem & Opportunity, Solution & Product, Market & Customers,
Business Model, Go-to-Market, Competition, Team, Financials & Projections, Roadmap & Milestones.
This mirrors every other Business Builder registry pattern in this codebase (`CANVAS_BLOCKS`,
`RECORD_SCHEMAS`) — an in-code, versionable registry rather than a DB table or an LLM-chosen
outline that could drift between runs.

**Section-by-section free-text `complete()` calls, NOT `complete_json`.** Module 03 Slice 2 built
`complete_json` (JSON-Schema-constrained structured output) specifically for the canvas ai-fill
consumer, where every field has a fixed shape (a list or a string per block key). A business-plan
section is prose — there is no fixed schema to constrain against, and forcing one would just mean
asking the LLM to emit `{"body": "..."}` and unwrapping it, adding a schema round-trip for zero
benefit. `handle_plan_generate` (`app/worker/handlers/plan.py`) calls
`client.complete(build_section_messages(s, context), max_tokens=settings.LLM_MAX_TOKENS)` once per
`PLAN_SECTIONS` entry — 10 sequential calls, not one batched call — and takes the `.strip()`'d
string as the section body directly. This is a deliberate, checklist-documented choice: it means
the typed-record `business.{kind}.ai_fill` worker (Module 08 Slice 2's still-deferred item) turned
out NOT to be a dependency of this generator after all.

**Plan stored as a Module 18 `Document`, not a new bespoke content table.** `create_document(db,
startup, created_by_id=..., kind=DocumentKind.business_plan, title=..., sections=[{heading, body},
...], ai_generated=True)` (`app/services/documents/service.py`) is the SAME function every other
document-creating path in the codebase calls (template instantiation, manual create). The plan
generator gets full-replace editing, optimistic-concurrency versioning, sharing, and e-signature for
free — nothing about the plan-specific code had to reimplement any of that. `business_plans` itself
stays a thin run-tracker: `status` (`generating`/`complete`/`failed`) and a nullable `document_id`
FK, not a copy of the plan's content.

**Async job, same enqueue-then-drain pattern as every other AI consumer on this seam.**
`POST /business-builder/plan/generate` creates the `BusinessPlan` row at `status=generating`,
enqueues `business.plan.generate` via the existing `job_dispatcher`, and returns 202 immediately —
ten sequential LLM calls (tens of seconds) must never block the request/response cycle, exactly the
same reasoning as Module 03 Slice 1's assessment-narrative job and Slice 2's canvas ai-fill job.

**Fail-loud, no partial write, no separate progress signal.** `handle_plan_generate` builds ALL 10
section bodies in memory before calling `create_document` — if any one `client.complete()` call
raises (network error, empty completion, etc. — see the LLM seam's own fail-loud contract), the
handler raises too, no `Document` is created, and the plan row is left at `status=generating`
forever in this version (see Follow-ups — there is no terminal `failed` transition yet, despite the
enum having the value). The worker's own retry/backoff (`WORKER_MAX_ATTEMPTS`) is the only
recovery path. There is also no incremental "3 of 10 sections done" signal — the FE only ever sees
`generating` → `complete` (or stuck `generating` if it never resolves).

**No-op guards mirror every other handler in `app/worker/handlers/`.** `handle_plan_generate`
returns immediately (no raise, no side effect) if the `BusinessPlan` row is missing or its status is
no longer `generating` (already resolved by an earlier attempt, or the row was hard-deleted) — the
same "benign no-op on a stale/duplicate job" contract as `handle_assessment_narrative` and
`handle_canvas_ai_fill`.

**PII-free context, verified by a dedicated unit test.** `build_plan_context`
(`app/services/business/plan_context.py`) gathers the startup's `profile` (name/industry/
stage/business_model — never a founder's name or email), `canvases` (raw `BusinessCanvas.blocks`
per type), `records` (raw `BusinessRecord.data` per kind), `assessment` (only the latest completed
assessment's `overall`/`dimension_scores`, never raw answers), and `roadmap` (phase/milestone/
status triples). This mirrors Module 03 Slice 1's narrative prompt's data-minimization discipline —
`tests/services/business/test_plan_generation.py::test_build_plan_context_gathers_business_data_no_pii`
asserts no email/user-identifying string appears in the assembled context.

**`business.plan.generated` maps to the existing `business` notification category — no new
category.** `app/services/notifications/registry.py` adds one `SPECS` entry
(`_s(_all_active_members, "Your AI business plan is ready")`), notifying every active workspace
member, the same recipient-resolution function every other Business Builder event
(`business.canvas.completed`, etc.) already uses.
`tests/services/notifications/test_plan_notification.py` pins both the registry entry and its
category mapping.

## What's involved

**Migration `0026_business_plans`** (chains off `0025_roadmap_milestone_due_idx`, sole alembic
head) — one brand-new table, no lock on any existing table (adding FKs to `startups`/`documents`/
`users` only needs a brief `AccessShareLock`, not an `AccessExclusiveLock`):
- `business_plans` — `startup_id` (FK → `startups`, `ON DELETE CASCADE`, indexed), `status`
  (VARCHAR-backed `BusinessPlanStatus` enum, `native_enum=False` — same convention as every other
  StrEnum column in this codebase), `document_id` (FK → `documents`, nullable, `ON DELETE SET
  NULL` — the run row survives the document being deleted, indexed), `created_by_id` (FK →
  `users`), plus the standard `id`/`created_at`/`updated_at` from `UUIDMixin`/`TimestampMixin`.
- Autogenerated from the Task 1 ORM model (`app/db/models/business.py:119`); only the revision id,
  down_revision, Create Date, and docstring were hand-edited.
- `downgrade()` drops the table (lossy — any plan-run data written while this migration was applied
  is destroyed on downgrade; inherent to dropping a brand-new table).

**Domain + service (Tasks 1–3)**
- `app/db/models/business.py:119` — `BusinessPlan(UUIDMixin, TimestampMixin, Base)`.
- `app/db/models/enums.py` — `BusinessPlanStatus` (`generating`/`complete`/`failed`).
- `app/services/business/plan_defs.py` — `PlanSection` dataclass + `PLAN_SECTIONS` (10 entries).
- `app/services/business/plan_context.py` — `build_plan_context(db, startup) -> dict`.
- `app/services/business/plan_prompt.py` — `build_section_messages(section, context) ->
  list[LLMMessage]` — system prompt fixes "prose markdown, body only, no heading, no invented
  facts"; user prompt is `{heading, guidance, JSON-dumped context}`.
- `app/worker/handlers/plan.py` — `handle_plan_generate(db, job)`, registered as
  `"business.plan.generate"`.

**API + notification (Task 4)**
- `app/api/v1/endpoints/business.py:217` — `POST /business-builder/plan/generate` (`_editor`:
  founder/team_member; 202; creates the `BusinessPlan` row, enqueues the job, returns `{plan_id,
  status}`).
- `app/api/v1/endpoints/business.py:239` — `GET /business-builder/plan` (`require_workspace`: any
  active member; returns the LATEST plan by `created_at desc`, or 404 if none exist yet — there is
  no list/history endpoint in v1, see Follow-ups).
- `app/services/notifications/registry.py` — `"business.plan.generated"` SPECS entry, category
  `business`.

**This task (Task 5)**
- `e2e/test_business_plan.py`, new — see Verification below.
- `docs/fe-integration-guide-ai-business-plan.md`, new.
- `docs/checklist/PROJECT_CHECKLIST.md` — Module 08 moved from "open" to "fully complete, all 4
  slices"; new Slice 4 section; Module 03's and Module 18's cross-references to the (formerly
  deferred) §08.11 item updated to point here; snapshot tally moved 08 out of "open" (11 complete,
  1 open — 03).
- Pre-existing formatting drift fixed as part of this task's full CI reproduction (mechanical only,
  no logic change — same "final task cleans up the slice's own drift" precedent as
  `docs/sop/2026-09-19-llm-seam-assessment-narrative.md`'s Task 4): `black` reformatted
  `app/services/business/plan_context.py`, `tests/services/business/test_plan_generation.py`, and
  `tests/worker/test_plan_handler.py` (left unformatted by this branch's own Tasks 1–3, which only
  ran `black`/`ruff` against their own touched files at the time); `tests/worker/test_plan_handler.py`
  also had an import-order violation fixed by `isort`.

**Errors / API surface** — two new routes (`POST /business-builder/plan/generate`,
`GET /business-builder/plan`), no new error `code` (both reuse existing 401/403/404/422 shapes;
`GET` 404s with the standard `NOT_FOUND` envelope when no plan exists yet).

## Verification

**Per-task unit verification (Tasks 1–4, already green before this task):**
- `tests/db/test_business_plan_model.py` — model/enum shape.
- `tests/services/business/test_plan_generation.py` — `PLAN_SECTIONS` shape, `build_plan_context`
  no-PII assertion, `build_section_messages` prompt shape.
- `tests/worker/test_plan_handler.py` — builds the document and completes on success; no-op when
  the plan is missing or already resolved; fails loud when the LLM errors (`LLM_PROVIDER=openai`,
  empty key).
- `tests/api/test_business_plan.py` — `POST`/`GET` auth/role/404 shapes.
- `tests/services/notifications/test_plan_notification.py` — registry entry + category mapping.

**Task 5 (this commit) — full local CI reproduction, run fresh at the end:**

| Gate | Command | Result |
|---|---|---|
| Format | `poetry run black --check app tests e2e` | ✅ pass (3 pre-existing files reformatted first — see "What's involved") |
| Import order | `poetry run isort --check-only app tests e2e` | ✅ pass (1 pre-existing violation fixed — see "What's involved") |
| Lint | `poetry run ruff check app tests e2e` | ✅ pass |
| Types | `poetry run mypy app` | ✅ pass — no issues in 161 source files |
| Pylint | `poetry run pylint app --fail-under=9.5` | ✅ pass — 9.89/10 (unchanged; no new findings in business/plan code) |
| Security | `poetry run bandit -r app/ --quiet` | ✅ pass, exit 0, no findings (only informational nosec/comment-parser warnings, pre-existing) |
| Unit + coverage | `poetry run pytest --cov=app --cov-fail-under=95 -q` | ✅ pass — **1257 passed, 97.45% coverage** (≥ 95% floor) |
| Migration heads | `poetry run alembic heads` | ✅ exactly one — `0026_business_plans (head)` |
| Live E2E | `./scripts/e2e_run.sh` | ✅ **45 passed** (44 pre-existing + 1 new) |

**`e2e/test_business_plan.py::test_business_plan_generation`** — a founder onboards (steps 1–4 +
complete, no roadmap/assessment walk needed — mirrors `e2e/test_canvas_ai_fill.py`'s `_onboard`) →
`POST /business-builder/plan/generate` returns 202 `{plan_id, status: "generating"}` (captured,
`generate_enqueued.json`) → the worker is drained in-process (`runner.run_once` looped, mirrors
`e2e/test_canvas_ai_fill.py::_drain`, with `app.worker.handlers.plan` imported inside the drain
function so the handler is registered in the TEST process) → `GET /business-builder/plan` returns
`status: "complete"` + a non-null `document_id` (captured, `plan_complete.json`) →
`GET /documents/{document_id}` returns `kind: "business_plan"`, `ai_generated: true`, and all 10
sections with `[stub-llm]` bodies and the expected first heading "Executive Summary" (captured,
`plan_document.json`) — proving enqueue → job claim → 10 sequential LLM (stub) calls → document
assembly → persisted link → read-back end to end over real HTTP with a real Postgres-backed worker
drain, zero network calls.

**Exact JSON paths, confirmed live (not guessed):** `POST .../plan/generate` → `data.plan_id`,
`data.status`. `GET .../plan` → `data.id`, `data.status`, `data.document_id` (`null` while
generating), `data.created_at`. `GET /documents/{id}` → `data.kind`, `data.ai_generated`,
`data.sections` (array of `{id, body, heading}` — `id` is a per-section UUID assigned by
`create_document`, not present on the request side). All captured verbatim in
`e2e/_captures/business_plan/*.json` and documented in the FE guide.

## Operate / roll back

**New deploy-time requirement: none.** The `business.plan.generate` job runs inside the existing
`worker` process (Module 20 Slice 2) — no new container, no new health check. Reuses the same
`LLM_*` config Module 03 Slice 1 introduced (`LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`,
`LLM_BASE_URL`, `LLM_TIMEOUT`, `LLM_MAX_TOKENS`) — see that slice's SOP for production requirements
(a real `LLM_API_KEY` must be set, or every `business.plan.generate` job fails loud and exhausts
its retries, leaving the plan stuck at `status: "generating"` forever in v1 — see Follow-ups).

**No new secret, no new config key.** This slice adds no settings of its own.

**Rollback:** revert this slice's commit (or the whole branch if not yet merged) and
`alembic downgrade -1` off `0026_business_plans` — drops the table. Lossy: any plan-run rows and
their linked `documents` written while this slice was live are NOT automatically cleaned up by the
downgrade (the `documents` rows are untouched by dropping `business_plans` — `document_id` is a
nullable FK with `ON DELETE SET NULL`, not a cascade); those `Document` rows remain as orphaned
`kind=business_plan` documents unless separately cleaned up. Judged acceptable — the same
"a brand-new table's downgrade is lossy by nature" reasoning as every other new-table migration in
this codebase (e.g. `0026`'s own docstring).

## Follow-ups

**No plan history/list endpoint.** `GET /business-builder/plan` only ever returns the LATEST plan
by `created_at desc`. Regenerating (a fresh `POST /plan/generate`) creates a new `BusinessPlan` row
and a new `Document`; the previous run's row and document are not deleted, but nothing surfaces
them to the FE — a founder who regenerates loses visibility into their prior plan version. A future
slice needs `GET /business-builder/plans` (list) if "compare my last two plans" becomes a real
product need.

**No terminal `failed` transition.** `BusinessPlanStatus.failed` exists in the enum but nothing in
this slice ever sets it — if `handle_plan_generate` exhausts the worker's retries, the row is left
at `status: "generating"` forever, indistinguishable (to the FE) from "still working, check back
later." This is the same class of gap the LLM-seam SOP flagged for the assessment narrative (no
structured "AI enrichment failed" signal), now repeated here with a slightly worse consequence: the
narrative has a templated fallback that's still useful; a stuck-generating plan has nothing to show
at all. Closing this needs the runner to call back into application code on terminal job failure —
infrastructure that does not exist yet anywhere in this codebase (see the notifications-scheduler
SOP's Follow-ups on worker DLQ/alerting, the same underlying gap).

**No incremental progress signal.** The FE sees only `generating` → `complete` (or stuck
`generating`); there is no "3 of 10 sections done" field, even though the handler naturally
processes sections one at a time. A future version could flush partial progress to the `BusinessPlan`
row between LLM calls if a founder-facing progress bar becomes a real product ask — not built here
to keep this slice's write path simple (build everything in memory, one `create_document` call, no
partial-document states to reason about).

**Free text, not structured financials.** Sections are markdown prose end to end — there is no
numeric revenue/cost/runway field a dashboard could chart or export. If a future need arises for a
structured financials sub-section, that is a `complete_json`-shaped follow-up (Module 03 Slice 2's
seam), deliberately not built here (see "How" above for why this slice chose free text throughout).

**No PDF/export.** The plan is a `Document` like any other — Module 18's existing surface (view,
edit, share, e-sign) applies, but there is no plan-specific PDF export. Falls out of Module 18's own
scope, not this slice's.

**Regenerating has no diff.** A fresh `POST /plan/generate` is indistinguishable from the first
generation — no comparison against the prior version, no changelog. Ties into the "no history/list
endpoint" gap above.

**Remaining Module 03 AI consumers still unbuilt.** The typed-record `business.{kind}.ai_fill`
worker (Module 08 Slice 2's own deferred item — this slice confirms it, since the plan generator
did NOT end up needing it), the onboarding AI panel, Mission's reason line, Roadmap's replan
rationale, Health Score's recommendation reasons, Learning's recommendations, and Validation Hub's
insight synthesizer are all still on their pre-Module-03 fallback behavior — each remains its own
future slice, unblocked on infrastructure but not started.

**Worker throughput + stale-reap safety before scaling workers (whole-branch review, operational).**
Plan generation makes 10 sequential LLM `complete` calls, so a single job holds the worker's
`run_once` loop for its full duration (blocking `scheduler_tick` and other jobs meanwhile) — a
throughput consideration, harmless at current volume with one worker. More important: if the worker
is ever scaled past one replica (today `docker-compose.prod.yml` pins `container_name`, preventing
that), note that `WORKER_STALE_SECONDS` (300) is *below* a long plan job's worst case, so the stale
reaper could re-claim and double-run a still-running plan job → a duplicate Document, a duplicate
`business.plan.generated` notification, and 2× LLM spend. Before scaling workers, add a job-level
heartbeat (or raise the stale window above the max plan-job duration) so long jobs aren't reaped mid-run.

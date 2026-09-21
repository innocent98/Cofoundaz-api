# Module 08 §08.11 — AI Business Plan Generator (design)

**Status:** approved-for-planning
**Date:** 2026-09-19
**Module:** 08 (Business Builder) — §08.11, the last unshipped piece; this **completes Module 08**.
**Depends on:** Module 03 Slice 1 (LLM seam `complete`) + Slice 2 (structured output) — both shipped; Module 18 Documents (`create_document`) — shipped; the job worker.

## Goal

Generate a multi-section **business plan** from the startup's Business Builder data (canvas, records,
assessment, roadmap, profile) using the LLM, asynchronously, and store it as a Module 18 Document.
Founders trigger it; the plan is written **section by section** for depth, then assembled into one
`business_plan` Document linked from a `business_plans` record.

One sentence: *`POST /business-builder/plan/generate` enqueues a job that writes each plan section
with the LLM and saves the assembled plan as a Document.*

## Why

- §08.11 is the **one remaining unshipped piece of Module 08** — it was blocked on the LLM seam
  (Module 03), which is now built (Slice 1 `complete`, Slice 2 structured output). Building it closes
  Module 08.
- A business plan is a high-stakes, founder-facing artifact, so section-by-section generation (one
  focused LLM call per section) is used for depth and per-section resilience, per the design decision.

## Scope

### In scope
1. **`business_plans` entity** + `BusinessPlanStatus` enum + migration `0026_business_plans`.
2. **`POST /business-builder/plan/generate`** (editor) → create a plan row (`generating`) + enqueue
   `business.plan.generate`; **`GET /business-builder/plan`** → the latest plan's status + document link.
3. **`business.plan.generate` worker** (`handle_plan_generate`): gather context, generate sections
   one call each, `create_document(kind=business_plan, …, ai_generated=True)`, link + mark complete,
   publish `business.plan.generated`.
4. **Plan section definitions** (fixed ordered list) + a per-section prompt builder (PII-free).
5. **Notification**: `business.plan.generated` → registry SPEC + `business` category.
6. Tests (unit + one live e2e against the stub), FE guide, SOP, checklist (Module 08 → complete).

### Out of scope (deferred)
- **Plan history/list endpoint** (`GET /business-builder/plans`) — v1 keeps rows (history) but only
  exposes the latest.
- **Incremental per-section progress persistence** — v1 generates all sections in the job and creates
  the Document at the end; a job failure retries from scratch (bounded retries).
- **Exec-summary-from-other-sections** (each section generated independently in v1), structured
  financial tables, PDF export, editing the generated plan (it's a normal Document afterward).
- **Records `business.{kind}.ai_fill` worker** (separate follow-on) and the other Module 03 consumers.

## Architecture

### 1. Data model — `app/db/models/business.py` (extend) + migration `0026_business_plans`

```python
class BusinessPlan(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_plans"
    startup_id:    Mapped[uuid.UUID]  # FK startups.id, index=True
    status:        Mapped[BusinessPlanStatus]  # Enum(native_enum=False, length=20): generating | complete | failed
    document_id:   Mapped[uuid.UUID | None]    # FK documents.id, nullable, index=True
    created_by_id: Mapped[uuid.UUID]           # FK users.id
```
`BusinessPlanStatus` (`app/db/models/enums.py`): `generating`, `complete`, `failed`. Migration
`0026_business_plans` (down_revision = the live head at build time — `0025_roadmap_milestone_due_idx`
today; rebase/renumber if anything merges first).

### 2. Plan section definitions — `app/services/business/plan_defs.py` (new)

A fixed, ordered list — one entry per plan section — easily tuned:

```python
@dataclass(frozen=True)
class PlanSection:
    key: str
    heading: str
    guidance: str   # what this section should cover (goes into the prompt)

PLAN_SECTIONS: tuple[PlanSection, ...] = (
    PlanSection("executive_summary", "Executive Summary", "..."),
    PlanSection("problem",           "Problem & Opportunity", "..."),
    PlanSection("solution",          "Solution & Product", "..."),
    PlanSection("market",            "Market & Customers", "..."),
    PlanSection("business_model",    "Business Model", "..."),
    PlanSection("gtm",               "Go-to-Market", "..."),
    PlanSection("competition",       "Competition", "..."),
    PlanSection("team",              "Team", "..."),
    PlanSection("financials",        "Financials & Projections", "..."),
    PlanSection("milestones",        "Roadmap & Milestones", "..."),
)
```

### 3. Context gathering — `app/services/business/plan_context.py` (new)

`build_plan_context(db, startup) -> PlanContext` — assembles a **PII-free** snapshot from whatever
exists: startup `name/industry/stage/business_model`, canvas blocks (all canvases), business records
(personas/revenue_stream/competitor/pricing via `record_defs`), the latest completed assessment's
dimension scores + overall, and the roadmap's phases/milestones. Missing pieces are simply omitted
(a sparse startup still generates a plan from its profile). No user names/emails.

### 4. Prompt builder — `app/services/business/plan_prompt.py` (new)

`build_section_messages(section: PlanSection, context: PlanContext) -> list[LLMMessage]` — a
system message (concise, investor-facing business-plan writer; markdown body only, no headings) +
a user message with the section's `guidance` and the relevant context. Returns prose markdown for
that one section's body.

### 5. Worker — `app/worker/handlers/plan.py` (new; register in `__main__.py::register()`)

```python
def handle_plan_generate(db, job) -> None:
    plan = db.get(BusinessPlan, job.payload["plan_id"])
    if plan is None or plan.status != BusinessPlanStatus.generating:
        return  # benign no-op (missing / already resolved — idempotent)
    startup = db.get(Startup, plan.startup_id)
    if startup is None:
        return
    context = build_plan_context(db, startup)
    client = get_llm_client()
    sections = [
        {"heading": s.heading, "body": client.complete(build_section_messages(s, context), max_tokens=settings.LLM_MAX_TOKENS).strip()}
        for s in PLAN_SECTIONS
    ]
    doc = create_document(
        db, startup, created_by_id=plan.created_by_id, kind=DocumentKind.business_plan,
        title=f"{startup.name or 'Business'} — Business Plan", sections=sections,
        folder=None, template_key=None, ai_generated=True,
    )
    plan.document_id = doc.id
    plan.status = BusinessPlanStatus.complete
    db.flush()
    event_bus.publish(db, "business.plan.generated",
                      {"startup_id": str(startup.id), "plan_id": str(plan.id), "document_id": str(doc.id)})
```
A new handler module (`handlers/plan.py`), NOT the shared `handlers/ai.py`, to keep it self-contained.
No commit/rollback (runner owns the txn); fail-loud per section → the runner's bounded retry re-runs
the whole job (the plan row stays `generating` on failure; a terminal failure leaves it `generating`
— acceptable for v1, noted; a future improvement flips it to `failed`).

> Note: `create_document` publishes `document.created` (existing) in addition to our
> `business.plan.generated` — both are fine; the plan event is the one the FE/notifications key on.

### 6. Endpoints — `app/api/v1/endpoints/business.py` (extend)

- `POST /business-builder/plan/generate` (`_editor`): create `BusinessPlan(status=generating, created_by_id=user.id)`, flush, `job_dispatcher.enqueue(db, "business.plan.generate", {"startup_id", "plan_id"}, startup.id)`, commit → `202 {"plan_id", "status": "generating"}`.
- `GET /business-builder/plan` (member read): latest `BusinessPlan` for the startup → `{"id", "status", "document_id"|null, "created_at"}` or `404` if none. The FE polls this (or `GET /jobs/{id}`) then fetches the document via the existing documents API.

### 7. Notifications — `registry.py` + `categories.py`

Add `business.plan.generated` → `_s(_all_active_members, "Your AI business plan is ready")`, category
`business`. (System-generated → all active members, no actor.)

## Data flow

```
POST /business-builder/plan/generate  (editor)
  └─ BusinessPlan(status=generating) + enqueue business.plan.generate {startup_id, plan_id}; commit → 202
       … worker …
  handle_plan_generate:
     └─ build_plan_context (canvas/records/assessment/roadmap/profile, PII-free)
     └─ for each PLAN_SECTIONS: body = get_llm_client().complete(build_section_messages(section, ctx))
     └─ create_document(kind=business_plan, sections=[{heading,body}...], ai_generated=True)
     └─ plan.document_id = doc.id; plan.status = complete; publish business.plan.generated
     └─ (runner commits) → notifications: "Your AI business plan is ready"
GET /business-builder/plan → {status: complete, document_id} → FE fetches the document
```

## Error handling

- **Fail-loud per section** (the seam raises on non-2xx/empty) → the job's bounded retry/backoff
  re-runs it; no partial Document is created (the Document is built only after all sections succeed).
- Plan row stays `generating` while the job retries; on terminal failure it remains `generating` in
  v1 (a UI "still working / try again" state) — flipping to `failed` on exhaustion is a noted
  follow-up (needs a runner hook or a max-attempt check in the handler).
- **Idempotent**: a re-run of a `complete` plan no-ops (status guard); re-generation is a *new* POST →
  new plan row + new Document.
- No PII in prompts; `LLM_API_KEY` never logged.

## Testing

- **Unit:** `PLAN_SECTIONS` non-empty + unique keys/headings; `build_plan_context` includes the
  business data + excludes PII (no `@`); `build_section_messages` includes the section guidance +
  context; the worker with `LLM_PROVIDER=stub` → a Document with `len(PLAN_SECTIONS)` sections each
  `{heading, body}` (stub `[stub-llm]` bodies), plan `complete` + `document_id` set, event published;
  missing/already-complete plan → no-op; LLM error → fail-loud (plan not marked complete). Tests use
  the `db` fixture (no `SessionLocal()` against the app DB).
- **E2E (stub):** onboard founder → (optionally seed a canvas block) → `POST plan/generate` (202) →
  drain worker in-process (`from app.worker.handlers import plan`) → `GET /business-builder/plan`
  shows `complete` + a `document_id` → `GET /documents/{id}` shows the sections. Capture the 202,
  the plan GET, and the document.
- Coverage ≥ 95%.

## Security & privacy

- The trigger is `_editor`-gated (existing dep). Prompts carry only business context (profile, canvas,
  records, scores, roadmap) — no user names/emails/PII. `LLM_API_KEY` from env, never logged. Data
  leaves to OpenAI as with the other AI features (documented, expected). No new external inbound
  surface beyond the two authenticated routes.

## FE impact (integration guide)

- `POST /business-builder/plan/generate` → `202 {plan_id, status:"generating"}`. The FE polls
  `GET /business-builder/plan` (or `GET /jobs/{id}`) until `status == "complete"`, then fetches the
  Document at `document_id` via the existing documents API to render the plan. Generation takes tens
  of seconds (one LLM call per section). A failed generation stays `generating` in v1 — surface a
  "still generating / retry" affordance. Re-generating is a fresh POST (new plan + new document; prior
  ones remain as documents).

## Global constraints (carried into the plan)

- **No AI attribution** in any commit or PR/issue body.
- **Reproduce every CI check locally and make it green before pushing** (black/isort/ruff/mypy/pylint
  ≥ 9.5/bandit/pytest ≥ 95% cov/**alembic single head — this slice ADDS migration `0026`, so exactly
  one head after it**/e2e), via `poetry run`. Unit tests DB-clean (the `db` fixture; no `SessionLocal()`).
- **Ship the SOP**, reconcile the **checklist** (Module 08 → complete), write the **FE integration
  guide** with payloads verbatim from live e2e captures.
- Response envelope, `AppError`, `_editor`/`require_workspace`, worker no-commit convention, enum
  `native_enum=False`, FK `index=True` conventions unchanged.

## Follow-ups (post-ship)

- Records `business.{kind}.ai_fill` worker (same structured-output pattern).
- Plan history/list endpoint; flip plan → `failed` on terminal job exhaustion; incremental per-section
  progress; exec-summary-from-sections; structured financial tables; PDF export.
- The remaining Module 03 AI consumers (onboarding panel, dashboard briefing, mission reason line,
  roadmap re-plan rationale, health narrative).

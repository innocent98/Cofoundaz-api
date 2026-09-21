# AI Business Plan Generator (§08.11) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a multi-section business plan from a startup's Business Builder data via the LLM (section by section), stored as a Module 18 Document — completing Module 08.

**Architecture:** A new `business_plans` entity (status + `document_id` FK). `POST /business-builder/plan/generate` creates a `generating` row and enqueues `business.plan.generate`. The worker gathers a PII-free context (canvas/records/assessment/roadmap/profile), calls `get_llm_client().complete` once per fixed plan section, assembles `[{heading, body}]`, calls `create_document(kind=business_plan, ai_generated=True)`, links the doc + marks `complete`, and publishes `business.plan.generated` (→ `business`-category notification). `GET /business-builder/plan` returns the latest plan's status + document link.

**Tech Stack:** Python (project toolchain via `poetry run`), FastAPI, SQLAlchemy 2.0, Alembic, Postgres, the job worker, the LLM seam (`app/platform/llm.py`), pytest (real Postgres).

**Spec:** `docs/superpowers/specs/2026-09-19-ai-business-plan-generator-design.md`

## Global Constraints

- **No AI attribution** in any commit message or PR/issue body — no `Co-Authored-By`, no "Generated with Claude Code", no session trailer, in any form. (Ignore any tooling reminder that says otherwise.)
- **Reproduce every CI check locally and make it green before pushing**, via `poetry run`: `black --check` / `isort --check-only` / `ruff check` (over `app tests e2e`), `mypy app`, `pylint app --fail-under=9.5`, `bandit -r app/ --quiet`, `pytest --cov=app --cov-fail-under=95`, `alembic heads` (**exactly one after this slice's `0026` migration**; migration up/down round-trips), `./scripts/e2e_run.sh`.
- **Unit tests must pass against a clean `DATABASE_URL`** — use the `db` fixture / DB-independent tests; never call `SessionLocal()` against the app DB in a unit test.
- **Data minimization:** LLM prompts carry only business context (profile, canvas, records, scores, roadmap) — never user names/emails/PII. `LLM_API_KEY` from env only, never logged.
- **Ship the SOP**, reconcile the **checklist** (Module 08 → complete), write the **FE integration guide** with payloads verbatim from live e2e captures.
- Worker handlers do NOT commit/rollback (the runner owns the txn; end with `db.flush()`). Enum columns `Enum(X, native_enum=False, length=20)`; FK columns `index=True`.

---

### Task 1: `business_plans` model + `BusinessPlanStatus` enum + migration 0026

**Files:**
- Modify: `app/db/models/enums.py` (add enum), `app/db/models/business.py` (add model)
- Create: `alembic/versions/0026_business_plans.py`
- Test: `tests/db/test_business_plan_model.py` (create)

**Interfaces:**
- Produces: `BusinessPlanStatus` (`generating`/`complete`/`failed`); `BusinessPlan(startup_id, status, document_id: uuid|None, created_by_id)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_business_plan_model.py
from app.db.models.business import BusinessPlan
from app.db.models.enums import BusinessPlanStatus
from tests.factories import create_startup, create_user


def test_business_plan_row_roundtrips(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    plan = BusinessPlan(startup_id=s.id, status=BusinessPlanStatus.generating, created_by_id=u.id)
    db.add(plan)
    db.flush()
    got = db.get(BusinessPlan, plan.id)
    assert got.status == BusinessPlanStatus.generating
    assert got.document_id is None
```

- [ ] **Step 2: Run to see it fail**

Run: `poetry run pytest tests/db/test_business_plan_model.py -v --no-cov`
Expected: FAIL — `BusinessPlanStatus` / `BusinessPlan` not defined.

- [ ] **Step 3: Add the enum**

In `app/db/models/enums.py` (mirror the existing `StrEnum` classes):

```python
class BusinessPlanStatus(enum.StrEnum):
    generating = "generating"
    complete = "complete"
    failed = "failed"
```

- [ ] **Step 4: Add the model**

In `app/db/models/business.py` (mirror `BusinessCanvas`; add imports as needed — `BusinessPlanStatus`):

```python
class BusinessPlan(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_plans"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[BusinessPlanStatus] = mapped_column(
        Enum(BusinessPlanStatus, native_enum=False, length=20), nullable=False
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
```

- [ ] **Step 5: Write the migration**

Confirm the current head: `poetry run alembic heads` (expect `0025_roadmap_milestone_due_idx`). Create `alembic/versions/0026_business_plans.py`:

```python
"""business_plans table (AI Business Plan Generator, §08.11)"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0026_business_plans"
down_revision = "0025_roadmap_milestone_due_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "business_plans",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("startup_id", PGUUID(as_uuid=True), sa.ForeignKey("startups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("document_id", PGUUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_id", PGUUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(op.f("ix_business_plans_startup_id"), "business_plans", ["startup_id"])
    op.create_index(op.f("ix_business_plans_document_id"), "business_plans", ["document_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_business_plans_document_id"), table_name="business_plans")
    op.drop_index(op.f("ix_business_plans_startup_id"), table_name="business_plans")
    op.drop_table("business_plans")
```
> Implementer note: match the exact column types/defaults the codebase's `UUIDMixin`/`TimestampMixin` produce (check a recent migration, e.g. `0024_scheduled_runs`, for the `id`/`created_at`/`updated_at` column spec this project uses, and copy that shape so `alembic check` shows no drift). Set `down_revision` to the real head if something merged first.

- [ ] **Step 6: Run test + migration round-trip + drift**

Run: `poetry run pytest tests/db/test_business_plan_model.py -v --no-cov`, then against a scratch DB: `poetry run alembic upgrade head && poetry run alembic check && poetry run alembic downgrade -1 && poetry run alembic upgrade head`
Expected: test PASS; `alembic check` → "No new upgrade operations detected." (no drift); round-trip clean; one head `0026_business_plans`.

- [ ] **Step 7: Commit**

```bash
git add app/db/models/enums.py app/db/models/business.py alembic/versions/0026_business_plans.py tests/db/test_business_plan_model.py
git commit -m "feat(business): business_plans entity + migration 0026"
```

---

### Task 2: Plan section defs + context gatherer + section prompt builder

**Files:**
- Create: `app/services/business/plan_defs.py`, `app/services/business/plan_context.py`, `app/services/business/plan_prompt.py`
- Test: `tests/services/business/test_plan_generation.py` (create)

**Interfaces:**
- Consumes: `LLMMessage` (`app/platform/llm.py`); `BusinessCanvas`/`BusinessRecord` (`app/db/models/business.py`); `Startup`; assessment + roadmap models.
- Produces: `PLAN_SECTIONS: tuple[PlanSection, ...]` (`PlanSection(key, heading, guidance)`); `build_plan_context(db, startup) -> dict`; `build_section_messages(section, context) -> list[LLMMessage]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/business/test_plan_generation.py
from app.db.models.enums import CanvasType
from app.services.business.plan_defs import PLAN_SECTIONS
from app.services.business.plan_context import build_plan_context
from app.services.business.plan_prompt import build_section_messages
from tests.factories import create_startup, create_user


def test_plan_sections_unique_and_nonempty():
    assert len(PLAN_SECTIONS) >= 8
    keys = [s.key for s in PLAN_SECTIONS]
    assert len(keys) == len(set(keys))
    assert all(s.heading and s.guidance for s in PLAN_SECTIONS)


def test_build_plan_context_gathers_business_data_no_pii(db):
    from app.db.models.business import BusinessCanvas
    u = create_user(db)
    s = create_startup(db, owner=u)  # confirm create_startup sets name/industry/stage
    db.add(BusinessCanvas(startup_id=s.id, type=CanvasType.business_model, blocks={"value_propositions": ["Fast"]}))
    db.flush()
    ctx = build_plan_context(db, s)
    blob = str(ctx)
    assert "Fast" in blob            # canvas content present
    assert "@" not in blob           # no emails/PII


def test_build_section_messages_includes_guidance_and_context():
    ctx = {"profile": {"name": "Acme", "industry": "Fintech", "stage": "validation"}, "canvases": {}, "records": {}, "assessment": None, "roadmap": []}
    msgs = build_section_messages(PLAN_SECTIONS[0], ctx)
    assert [m.role for m in msgs] == ["system", "user"]
    assert PLAN_SECTIONS[0].guidance[:12] in msgs[1].content or PLAN_SECTIONS[0].heading in msgs[1].content
    assert "Acme" in msgs[1].content
```

- [ ] **Step 2: Run to see them fail**

Run: `poetry run pytest tests/services/business/test_plan_generation.py -v --no-cov`
Expected: FAIL — modules not defined.

- [ ] **Step 3: Implement `plan_defs.py`**

```python
# app/services/business/plan_defs.py
from dataclasses import dataclass


@dataclass(frozen=True)
class PlanSection:
    key: str
    heading: str
    guidance: str


PLAN_SECTIONS: tuple[PlanSection, ...] = (
    PlanSection("executive_summary", "Executive Summary", "A concise overview of the business, the opportunity, and why it will win."),
    PlanSection("problem", "Problem & Opportunity", "The customer problem, its size and urgency, and the market opportunity."),
    PlanSection("solution", "Solution & Product", "What the product is, how it solves the problem, and its current state."),
    PlanSection("market", "Market & Customers", "Target customers/segments, market size, and demand evidence."),
    PlanSection("business_model", "Business Model", "How the business makes money: revenue streams, pricing, unit economics."),
    PlanSection("gtm", "Go-to-Market", "How the business reaches and acquires customers; channels and motion."),
    PlanSection("competition", "Competition", "Key competitors, alternatives, and this startup's differentiation."),
    PlanSection("team", "Team", "The team, relevant strengths, and gaps to fill."),
    PlanSection("financials", "Financials & Projections", "High-level projections, key assumptions, and funding needs."),
    PlanSection("milestones", "Roadmap & Milestones", "Near-term milestones and the path to the next stage."),
)
```

- [ ] **Step 4: Implement `plan_context.py`**

```python
# app/services/business/plan_context.py
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.assessment import Assessment, AssessmentResult
from app.db.models.business import BusinessCanvas, BusinessRecord
from app.db.models.enums import AssessmentStatus
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.db.models.startup import Startup


def build_plan_context(db: Session, startup: Startup) -> dict[str, Any]:
    """PII-free snapshot of the startup's Business Builder data for plan generation."""
    canvases = {
        c.type.value: c.blocks
        for c in db.query(BusinessCanvas).filter(BusinessCanvas.startup_id == startup.id).all()
    }
    records: dict[str, list[dict[str, Any]]] = {}
    for r in (
        db.query(BusinessRecord)
        .filter(BusinessRecord.startup_id == startup.id)
        .order_by(BusinessRecord.kind, BusinessRecord.position)
        .all()
    ):
        records.setdefault(r.kind.value, []).append(r.data)

    result = (
        db.query(AssessmentResult)
        .join(Assessment, Assessment.id == AssessmentResult.assessment_id)
        .filter(Assessment.startup_id == startup.id, Assessment.status == AssessmentStatus.completed)
        .order_by(Assessment.completed_at.desc())
        .first()
    )
    assessment = (
        {"overall": result.overall_provisional, "dimension_scores": result.dimension_scores}
        if result
        else None
    )

    roadmap_rows = (
        db.query(RoadmapPhase.name, RoadmapMilestone.title, RoadmapMilestone.status)
        .join(Roadmap, Roadmap.id == RoadmapPhase.roadmap_id)
        .join(RoadmapMilestone, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(Roadmap.startup_id == startup.id)
        .order_by(RoadmapPhase.order, RoadmapMilestone.order)
        .all()
    )
    roadmap = [{"phase": p, "milestone": t, "status": (s.value if s else None)} for p, t, s in roadmap_rows]

    return {
        "profile": {
            "name": startup.name,
            "industry": startup.industry,
            "stage": (startup.stage.value if startup.stage else None),
            "business_model": (startup.business_model.value if startup.business_model else None),
        },
        "canvases": canvases,
        "records": records,
        "assessment": assessment,
        "roadmap": roadmap,
    }
```
> Implementer note: confirm the exact attribute/relationship names against the models (`RoadmapPhase.order`, `RoadmapMilestone.order`/`.status`, `Assessment.completed_at`, `Startup.business_model`). Adjust the queries to the real names; keep the returned dict shape (`profile/canvases/records/assessment/roadmap`) stable — Task 2's prompt builder and Task 3's worker depend on it. The context must contain NO PII (names of *people*, emails) — only the startup's own business data.

- [ ] **Step 5: Implement `plan_prompt.py`**

```python
# app/services/business/plan_prompt.py
import json
from typing import Any

from app.platform.llm import LLMMessage
from app.services.business.plan_defs import PlanSection


def build_section_messages(section: PlanSection, context: dict[str, Any]) -> list[LLMMessage]:
    """One plan section's prompt. Returns prose markdown for the body only (no heading)."""
    system = (
        "You are writing one section of a startup's business plan for an investor audience. "
        "Write clear, concrete markdown prose for the section BODY only — no section heading, no "
        "preamble. Use the provided context; do not invent facts not supported by it."
    )
    user = (
        f"Section: {section.heading}\nWhat to cover: {section.guidance}\n\n"
        f"Startup context (JSON):\n{json.dumps(context, ensure_ascii=False)}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
```

- [ ] **Step 6: Run to see them pass**

Run: `poetry run pytest tests/services/business/test_plan_generation.py -v --no-cov` then `poetry run mypy app && poetry run ruff check app`
Expected: PASS; mypy + ruff clean.

- [ ] **Step 7: Commit**

```bash
git add app/services/business/plan_defs.py app/services/business/plan_context.py app/services/business/plan_prompt.py tests/services/business/test_plan_generation.py
git commit -m "feat(business): plan section defs + context gatherer + section prompt builder"
```

---

### Task 3: `business.plan.generate` worker

**Files:**
- Create: `app/worker/handlers/plan.py`
- Modify: `app/worker/__main__.py` (`register()` imports the new handler), `tests/worker/conftest.py` (evict `app.worker.handlers.plan` in the autouse fixture, mirroring `email`/`scheduled`/`ai`)
- Test: `tests/worker/test_plan_handler.py` (create)

**Interfaces:**
- Consumes: `BusinessPlan`, `BusinessPlanStatus` (Task 1); `PLAN_SECTIONS`, `build_plan_context`, `build_section_messages` (Task 2); `get_llm_client` + `settings.LLM_MAX_TOKENS`; `create_document` (`app/services/documents/service.py`); `DocumentKind`; `event_bus`; `register_handler`, `Job`, `Startup`.
- Produces: `handle_plan_generate(db, job)`; job type `"business.plan.generate"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/worker/test_plan_handler.py
import pytest

from app.core.config import settings
from app.db.models.business import BusinessPlan
from app.db.models.enums import BusinessPlanStatus, DocumentKind
from app.db.models.document import Document
from app.db.models.job import Job, JobStatus
from app.platform import events as events_mod
from app.services.business.plan_defs import PLAN_SECTIONS
from app.worker.handlers.plan import handle_plan_generate
from tests.factories import create_startup, create_user


def _job(plan_id, startup_id):
    return Job(type="business.plan.generate", payload={"plan_id": str(plan_id), "startup_id": str(startup_id)}, status=JobStatus.running)


def _plan(db, startup, user):
    p = BusinessPlan(startup_id=startup.id, status=BusinessPlanStatus.generating, created_by_id=user.id)
    db.add(p); db.flush()
    return p


def test_plan_generate_builds_document_and_completes(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u = create_user(db); s = create_startup(db, owner=u); p = _plan(db, s, u)
    handle_plan_generate(db, _job(p.id, s.id))
    db.refresh(p)
    assert p.status == BusinessPlanStatus.complete
    assert p.document_id is not None
    doc = db.get(Document, p.document_id)
    assert doc.kind == DocumentKind.business_plan and doc.ai_generated is True
    assert len(doc.sections) == len(PLAN_SECTIONS)
    assert all(sec["heading"] and "[stub-llm]" in sec["body"] for sec in doc.sections)
    assert any(e == "business.plan.generated" for e, _ in published)


def test_plan_generate_noop_when_missing_or_done(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    import uuid
    handle_plan_generate(db, _job(uuid.uuid4(), uuid.uuid4()))  # missing plan -> no raise
    u = create_user(db); s = create_startup(db, owner=u); p = _plan(db, s, u)
    p.status = BusinessPlanStatus.complete; db.flush()
    handle_plan_generate(db, _job(p.id, s.id))  # already complete -> no-op
    assert p.document_id is None


def test_plan_generate_fails_loud_on_llm_error(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    u = create_user(db); s = create_startup(db, owner=u); p = _plan(db, s, u)
    with pytest.raises(RuntimeError):
        handle_plan_generate(db, _job(p.id, s.id))
```

- [ ] **Step 2: Run to see them fail**

Run: `poetry run pytest tests/worker/test_plan_handler.py -v --no-cov`
Expected: FAIL — `app.worker.handlers.plan` not defined.

- [ ] **Step 3: Implement the handler**

```python
# app/worker/handlers/plan.py
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.business import BusinessPlan
from app.db.models.enums import BusinessPlanStatus, DocumentKind
from app.db.models.job import Job
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.platform.llm import get_llm_client
from app.services.business.plan_context import build_plan_context
from app.services.business.plan_defs import PLAN_SECTIONS
from app.services.business.plan_prompt import build_section_messages
from app.services.documents.service import create_document
from app.worker.runner import register_handler


def handle_plan_generate(db: Session, job: Job) -> None:
    """Generate a business plan section-by-section and store it as a Document. No commit."""
    plan = db.get(BusinessPlan, job.payload["plan_id"])
    if plan is None or plan.status != BusinessPlanStatus.generating:
        return  # benign no-op (missing / already resolved)
    startup = db.get(Startup, plan.startup_id)
    if startup is None:
        return
    context = build_plan_context(db, startup)
    client = get_llm_client()
    sections = [
        {
            "heading": s.heading,
            "body": client.complete(
                build_section_messages(s, context), max_tokens=settings.LLM_MAX_TOKENS
            ).strip(),
        }
        for s in PLAN_SECTIONS
    ]
    doc = create_document(
        db,
        startup,
        created_by_id=plan.created_by_id,
        kind=DocumentKind.business_plan,
        title=f"{startup.name or 'Business'} — Business Plan",
        sections=sections,
        folder=None,
        template_key=None,
        ai_generated=True,
    )
    plan.document_id = doc.id
    plan.status = BusinessPlanStatus.complete
    db.flush()
    event_bus.publish(
        db,
        "business.plan.generated",
        {"startup_id": str(startup.id), "plan_id": str(plan.id), "document_id": str(doc.id)},
    )


register_handler("business.plan.generate", handle_plan_generate)
```

- [ ] **Step 4: Wire register + conftest eviction**

In `app/worker/__main__.py::register()` add `import app.worker.handlers.plan  # noqa: F401`. In `tests/worker/conftest.py` autouse fixture, add `sys.modules.pop("app.worker.handlers.plan", None)` (before and after `yield`, mirroring the existing handler pops).

- [ ] **Step 5: Run tests + worker suite**

Run: `poetry run pytest tests/worker/test_plan_handler.py tests/worker/ -q --no-cov` then `poetry run mypy app && poetry run ruff check app`
Expected: PASS; mypy + ruff clean.

- [ ] **Step 6: Commit**

```bash
git add app/worker/handlers/plan.py app/worker/__main__.py tests/worker/conftest.py tests/worker/test_plan_handler.py
git commit -m "feat(business): business.plan.generate worker assembles the plan document"
```

---

### Task 4: Endpoints + `business.plan.generated` notification

**Files:**
- Modify: `app/api/v1/endpoints/business.py` (two routes), `app/services/notifications/registry.py` (SPEC row), `app/services/notifications/categories.py` (event→category)
- Test: `tests/api/business/test_plan_endpoints.py` (create), `tests/services/notifications/test_plan_notification.py` (create)

**Interfaces:**
- Consumes: `BusinessPlan`/`BusinessPlanStatus` (Task 1); `job_dispatcher`, `_editor`, `_startup`, `require_workspace`/member read dep, `get_verified_user`, `success_response`, `NotFound` (existing in `business.py`); `_all_active_members`, `_s`, `SPECS` (`registry.py`); `EVENT_CATEGORY` (`categories.py`).
- Produces: `POST /business-builder/plan/generate` → `202 {plan_id, status}`; `GET /business-builder/plan` → latest plan or 404; `business.plan.generated` notification (category `business`).

- [ ] **Step 1: Write the failing endpoint tests**

```python
# tests/api/business/test_plan_endpoints.py
# Mirror the auth/onboarding setup used by other business endpoint tests in this dir
# (reuse their helpers/fixtures to get an editor client + workspace header).
from app.db.models.business import BusinessPlan
from app.db.models.enums import BusinessPlanStatus


def test_generate_enqueues_and_creates_plan(editor_client, workspace_header, db):
    r = editor_client.post("/api/v1/business-builder/plan/generate", headers=workspace_header)
    assert r.status_code == 202, r.text
    body = r.json()["data"]
    assert body["status"] == "generating" and body["plan_id"]
    assert db.query(BusinessPlan).count() == 1


def test_get_plan_returns_latest_or_404(editor_client, workspace_header):
    r0 = editor_client.get("/api/v1/business-builder/plan", headers=workspace_header)
    assert r0.status_code == 404
    editor_client.post("/api/v1/business-builder/plan/generate", headers=workspace_header)
    r1 = editor_client.get("/api/v1/business-builder/plan", headers=workspace_header)
    assert r1.status_code == 200
    assert r1.json()["data"]["status"] in ("generating", "complete")
```
> Implementer note: use whatever fixture pattern the sibling files in `tests/api/business/` already use to build an editor-authenticated client + `X-Workspace-Id` header (don't invent a new one). If they build it inline, do the same here.

- [ ] **Step 2: Run to see them fail**

Run: `poetry run pytest tests/api/business/test_plan_endpoints.py -v --no-cov`
Expected: FAIL — routes not defined.

- [ ] **Step 3: Implement the endpoints** (append to `app/api/v1/endpoints/business.py`)

```python
@router.post("/plan/generate", status_code=status.HTTP_202_ACCEPTED)
def generate_plan(
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    plan = BusinessPlan(
        startup_id=startup.id, status=BusinessPlanStatus.generating, created_by_id=user.id
    )
    db.add(plan)
    db.flush()
    job_dispatcher.enqueue(
        db, "business.plan.generate", {"startup_id": str(startup.id), "plan_id": str(plan.id)}, startup.id
    )
    db.commit()
    return success_response({"plan_id": str(plan.id), "status": plan.status.value})


@router.get("/plan")
def get_plan(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    plan = (
        db.query(BusinessPlan)
        .filter(BusinessPlan.startup_id == membership.startup_id)
        .order_by(BusinessPlan.created_at.desc())
        .first()
    )
    if plan is None:
        raise NotFound()
    return success_response(
        {
            "id": str(plan.id),
            "status": plan.status.value,
            "document_id": (str(plan.document_id) if plan.document_id else None),
            "created_at": plan.created_at.isoformat(),
        }
    )
```
Add imports (`BusinessPlan`, `BusinessPlanStatus`, and `require_workspace`/`NotFound` if not already imported in this file — check the top of `business.py` and reuse what's there).

- [ ] **Step 4: Write the failing notification test + wire it**

```python
# tests/services/notifications/test_plan_notification.py
from app.services.notifications.categories import category_for
from app.services.notifications.registry import SPECS


def test_plan_generated_notifies_and_maps_to_business():
    assert "business.plan.generated" in SPECS
    assert category_for("business.plan.generated") == "business"
```
Then add to `registry.py` `SPECS`: `"business.plan.generated": _s(_all_active_members, "Your AI business plan is ready"),` and to `categories.py` `EVENT_CATEGORY`: `"business.plan.generated": "business",`.

- [ ] **Step 5: Run all Task-4 tests + suites**

Run: `poetry run pytest tests/api/business/test_plan_endpoints.py tests/services/notifications/test_plan_notification.py tests/api/business/ tests/services/notifications/ -q --no-cov` then `poetry run mypy app && poetry run ruff check app`
Expected: PASS; mypy + ruff clean.

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/endpoints/business.py app/services/notifications/registry.py app/services/notifications/categories.py tests/api/business/test_plan_endpoints.py tests/services/notifications/test_plan_notification.py
git commit -m "feat(business): plan generate/get endpoints + business.plan.generated notification"
```

---

### Task 5: Live e2e + captures + FE guide + SOP + checklist

**Files:**
- Create: `e2e/test_business_plan.py`
- Create: `docs/fe-integration-guide-ai-business-plan.md`, `docs/sop/2026-09-19-ai-business-plan-generator.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

**Interfaces:** consumes everything above; drives the endpoints + in-process worker drain.

- [ ] **Step 1: Write the e2e journey**

```python
# e2e/test_business_plan.py
"""Live §08.11: AI Business Plan Generator (stub LLM).

Founder onboards, triggers plan generation, we drain the worker in-process (LLM_PROVIDER=stub,
set by scripts/e2e_run.sh), then GET the plan (complete + document_id) and fetch the document.
"""
import httpx


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import plan as _plan  # noqa: F401  (registers business.plan.generate)

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_business_plan_generation(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(access)
        wh = _onboard(c, auth)  # mirror e2e/test_assessment.py -> {**auth, "X-Workspace-Id": <startup_id>}

        gen = c.post("/api/v1/business-builder/plan/generate", headers=wh)
        assert gen.status_code == 202, gen.text
        capture("business_plan", "generate_enqueued", gen)

        _drain()

        got = c.get("/api/v1/business-builder/plan", headers=wh)
        assert got.status_code == 200 and got.json()["data"]["status"] == "complete", got.text
        doc_id = got.json()["data"]["document_id"]
        assert doc_id
        capture("business_plan", "plan_complete", got)

        doc = c.get(f"/api/v1/documents/{doc_id}", headers=wh)  # confirm the documents GET path
        assert doc.status_code == 200, doc.text
        assert doc.json()["data"]["kind"] == "business_plan"
        capture("business_plan", "plan_document", doc)
```
> Implementer note: fill `_onboard` by mirroring `e2e/test_assessment.py` (`/auth/me` → user id at `data.user.id`, workspace at `data.active_workspace_id`). Confirm the documents GET route + the exact JSON paths (`data.status`, `data.document_id`, `data.kind`, `data.sections`) from real responses — read the captures, don't guess. Bounded timeout so a stall fails loudly.

- [ ] **Step 2: Run the e2e suite**

Run: `./scripts/e2e_run.sh`
Expected: all pass incl. the new journey; captures under `e2e/_captures/business_plan/`.

- [ ] **Step 3: Docs (after the run — captures byte-accurate)**

- `docs/fe-integration-guide-ai-business-plan.md` (create): `POST /business-builder/plan/generate` (202, `{plan_id, status}`); poll `GET /business-builder/plan` (or `GET /jobs/{id}`) until `status == "complete"`, then fetch the Document at `document_id` via the documents API to render the plan (sections = `[{heading, body}]`, body is markdown). Generation takes tens of seconds. A failed generation stays `generating` in v1 (surface a retry). Re-generating = a fresh POST (new plan + document). Paste the captured 202 / plan / document bodies VERBATIM. Verification table.
- `docs/sop/2026-09-19-ai-business-plan-generator.md` (create; match existing SOP style): what shipped (§08.11, completes Module 08), why, how (section-by-section via `complete`; context gatherer; stored as a Module 18 Document; `business.plan.generated` event), files/migration (`0026`), config, verification (unit + e2e), deferred follow-ups (history/list endpoint; flip to `failed` on terminal exhaustion; incremental progress; structured financials; PDF; records ai_fill; remaining Module 03 consumers).
- `docs/checklist/PROJECT_CHECKLIST.md`: mark §08.11 shipped → **Module 08 fully complete**; move Module 08 from open → complete in the snapshot + tally (adjust counts: 11 complete, 1 open (03)); flip the §08.11 "deferred" line to done (PR ref at merge).

- [ ] **Step 4: Full local CI reproduction**

Run:
```
poetry run black --check app tests e2e && poetry run isort --check-only app tests e2e && poetry run ruff check app tests e2e
poetry run mypy app && poetry run pylint app --fail-under=9.5 && poetry run bandit -r app/ --quiet
poetry run pytest --cov=app --cov-fail-under=95 -q
poetry run alembic heads   # exactly one: 0026_business_plans
./scripts/e2e_run.sh
```
Expected: all green; one head. After a green e2e, `git checkout -- e2e/_captures/` for everything EXCEPT the new `e2e/_captures/business_plan/`.

- [ ] **Step 5: Commit**

```bash
git add e2e/ docs/
git commit -m "test(business): §08.11 plan generator live e2e + FE guide + SOP + checklist"
```

---

## Self-Review

**Spec coverage:**
- `business_plans` entity + `BusinessPlanStatus` + migration `0026` → Task 1. ✓
- Section defs + context gatherer (PII-free) + prompt builder → Task 2. ✓
- `business.plan.generate` worker (section-by-section `complete`, create_document, link+complete, event, no-op guards, fail-loud) → Task 3. ✓
- `POST /plan/generate` + `GET /plan` → Task 4. ✓
- `business.plan.generated` notification (`business` category) → Task 4. ✓
- e2e + FE guide + SOP + checklist (Module 08 complete) → Task 5. ✓
- Migration single head `0026` + drift-clean → Task 1 Step 6 + Task 5 Step 4. ✓
- DB-clean unit tests (db fixture) → all tasks. ✓

**Placeholder scan:** section `guidance` strings are real content (Task 2). e2e `_onboard` delegates to mirroring `e2e/test_assessment.py` (existing flow) with the novel generate→drain→GET given in full. Query attribute confirmations (roadmap/assessment field names, documents GET path, tests/api/business fixture pattern) are labeled implementer checks against real code, not unwritten logic.

**Type consistency:** `BusinessPlan(startup_id, status, document_id, created_by_id)`; `BusinessPlanStatus.{generating,complete,failed}`; `PlanSection(key, heading, guidance)`; `PLAN_SECTIONS`; `build_plan_context(db, startup) -> dict` (keys profile/canvases/records/assessment/roadmap); `build_section_messages(section, context) -> list[LLMMessage]`; `handle_plan_generate(db, job)`; job type `"business.plan.generate"`; event `"business.plan.generated"`; document section `{heading, body}`; `create_document(..., kind=DocumentKind.business_plan, sections, ai_generated=True)` — consistent across Tasks 1–5. ✓

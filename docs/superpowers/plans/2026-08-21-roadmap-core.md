# Roadmap Core (Module 05 · Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the roadmap core — a stage-template-generated `roadmaps → phases → milestones → tasks` tree with full CRUD, derived progress/overdue, and inline+lazy generation — giving the `roadmap.generate` stub job its first real consumer.

**Architecture:** A modular-monolith service (`app/services/roadmap/`) generates a roadmap from a static versioned stage-template catalog via an idempotent `ON CONFLICT (startup_id) DO NOTHING RETURNING id` claim, runs inline in the caller's transaction (onboarding-complete, the `generate` endpoint, and a lazy `GET`), and stores a denormalized milestone `progress` kept honest by a recompute helper on task changes. Endpoints live in one file (`endpoints/roadmap.py`), reads open to any member, writes gated to founder/team_member via the existing varargs `require_role`.

**Tech Stack:** Python 3.11 · FastAPI · SQLAlchemy 2.0 (typed `Mapped`) · Alembic · PostgreSQL · pytest (real Postgres, per-test rollback).

**Spec:** `docs/superpowers/specs/2026-08-21-roadmap-core-design.md`

## Global Constraints

- **Response envelope:** all success bodies via `success_response(data)` from `app.core.envelope`; errors via `AppError` subclasses / `AppError("VALIDATION_ERROR", msg, 422, field_errors=[…])`.
- **No AI-attribution trailers** in any commit message or PR body (project rule — overrides global CLAUDE.md).
- **Tenancy:** every resource resolves to `roadmap.startup_id == membership.startup_id`; cross-workspace ids raise `NotFound()` (uniform 404 — no enumeration leak).
- **Access:** reads = any active member (`require_workspace`); writes = founder or team_member (`require_role(MembershipRole.founder, MembershipRole.team_member)`); all endpoints also require a verified user (`get_verified_user`). Mentor is read-only.
- **Enums:** `native_enum=False, length=20` (match `Assessment.status` precedent).
- **Table prefix:** `roadmap_` on child tables. **Migration:** `0006_roadmap`, `down_revision = '0005_health_score'`.
- **Config version:** `ROADMAP_TEMPLATE_VERSION = 1`, stamped on `roadmaps.template_version`.
- **Dates:** `Date` columns (calendar), not timestamps. Overdue is derived, never stored.
- **Idempotent create-once:** a startup has at most one roadmap; regeneration returns the existing tree.

---

## File Structure

- `app/db/models/enums.py` — add `RoadmapStatus`, `TaskEffort` (modify).
- `app/db/models/roadmap.py` — 5 models (create).
- `app/db/models/__init__.py` — register the models (modify).
- `alembic/versions/0006_roadmap.py` — migration (create).
- `tests/factories.py` — add `create_roadmap/create_phase/create_milestone/create_task` (modify).
- `app/services/roadmap/__init__.py` — package (create).
- `app/services/roadmap/templates.py` — `ROADMAP_TEMPLATE_VERSION`, `STAGE_TEMPLATES` (create).
- `app/services/roadmap/service.py` — `generate_roadmap`, `recompute_milestone_progress`, `milestone_overdue`, `serialize_tree`, `person_ref` (create).
- `app/schemas/roadmap.py` — request models (create).
- `app/api/v1/endpoints/roadmap.py` — 11 routes + `_roadmap/_phase/_milestone/_task` resolvers (create).
- `app/api/v1/api.py` — register the router at `/roadmap` (modify).
- `app/services/onboarding/complete.py` — replace the `roadmap.generate` stub with inline generation (modify).
- `e2e/test_roadmap.py` — live journey (create).
- `e2e/test_smoke.py` — add roadmap paths to the openapi assertion (modify).
- `docs/sop/2026-08-21-roadmap-core.md`, `docs/fe-integration-guide-roadmap.md` — docs (create).

---

## Task 1: Enums + 5 models + factories

**Files:**
- Modify: `app/db/models/enums.py`
- Create: `app/db/models/roadmap.py`
- Modify: `app/db/models/__init__.py`
- Modify: `tests/factories.py`
- Test: `tests/models/test_roadmap_models.py`

**Interfaces:**
- Produces:
  - `RoadmapStatus(todo|in_progress|done)`, `TaskEffort(small|medium|large)` in `enums`.
  - Models `Roadmap, RoadmapPhase, RoadmapMilestone, RoadmapTask, RoadmapTaskDependency` in `app.db.models.roadmap`.
  - Factories `create_roadmap(db, startup, *, stage=StartupStage.validation, template_key="stage.validation", template_version=1) -> Roadmap`; `create_phase(db, roadmap, *, name="Phase", order=0) -> RoadmapPhase`; `create_milestone(db, phase, *, title="M", status=RoadmapStatus.todo, progress=0, due_on=None, owner=None, order=0) -> RoadmapMilestone`; `create_task(db, milestone, *, title="T", effort=TaskEffort.medium, status=RoadmapStatus.todo, assignee=None, due_on=None, order=0) -> RoadmapTask`.

- [ ] **Step 1: Write the failing test**

```python
# tests/models/test_roadmap_models.py
from datetime import date

from app.db.models.enums import RoadmapStatus, TaskEffort
from tests.factories import (
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_task,
    create_user,
)


def test_roadmap_tree_persists_and_cascades(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)
    roadmap = create_roadmap(db, startup)
    phase = create_phase(db, roadmap, name="Validation", order=0)
    milestone = create_milestone(db, phase, title="Validate demand", due_on=date(2026, 9, 1))
    task = create_task(db, milestone, title="Interviews", effort=TaskEffort.medium)

    assert roadmap.startup_id == startup.id
    assert roadmap.template_version == 1
    assert phase.roadmap_id == roadmap.id
    assert milestone.phase_id == phase.id
    assert milestone.status == RoadmapStatus.todo
    assert milestone.progress == 0
    assert task.milestone_id == milestone.id
    assert task.effort == TaskEffort.medium

    # deleting the roadmap cascades to phases -> milestones -> tasks
    db.delete(roadmap)
    db.flush()
    from app.db.models.roadmap import RoadmapPhase, RoadmapTask

    assert db.query(RoadmapPhase).count() == 0
    assert db.query(RoadmapTask).count() == 0


def test_one_roadmap_per_startup(db):
    import sqlalchemy

    owner = create_user(db)
    startup = create_startup(db, owner=owner)
    create_roadmap(db, startup)
    with pytest_raises_integrity():
        create_roadmap(db, startup)
        db.flush()


import contextlib

import pytest


@contextlib.contextmanager
def pytest_raises_integrity():
    import sqlalchemy

    with pytest.raises(sqlalchemy.exc.IntegrityError):
        yield
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/models/test_roadmap_models.py -v`
Expected: FAIL with `ImportError` / `cannot import name 'RoadmapStatus'`.

- [ ] **Step 3: Add the enums**

```python
# app/db/models/enums.py  (append)
class RoadmapStatus(str, enum.Enum):
    todo = "todo"
    in_progress = "in_progress"
    done = "done"


class TaskEffort(str, enum.Enum):
    small = "small"
    medium = "medium"
    large = "large"
```

- [ ] **Step 4: Create the models**

```python
# app/db/models/roadmap.py
import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import RoadmapStatus, StartupStage, TaskEffort


class Roadmap(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "roadmaps"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    stage: Mapped[StartupStage] = mapped_column(
        Enum(StartupStage, native_enum=False, length=20), nullable=False
    )
    template_key: Mapped[str] = mapped_column(String(60), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RoadmapPhase(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "roadmap_phases"

    roadmap_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("roadmaps.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class RoadmapMilestone(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "roadmap_milestones"

    phase_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("roadmap_phases.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    status: Mapped[RoadmapStatus] = mapped_column(
        Enum(RoadmapStatus, native_enum=False, length=20),
        default=RoadmapStatus.todo, nullable=False,
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RoadmapTask(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "roadmap_tasks"

    milestone_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("roadmap_milestones.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    effort: Mapped[TaskEffort] = mapped_column(
        Enum(TaskEffort, native_enum=False, length=20),
        default=TaskEffort.medium, nullable=False,
    )
    status: Mapped[RoadmapStatus] = mapped_column(
        Enum(RoadmapStatus, native_enum=False, length=20),
        default=RoadmapStatus.todo, nullable=False,
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RoadmapTaskDependency(Base):
    __tablename__ = "roadmap_task_dependencies"

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("roadmap_tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("roadmap_tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    __table_args__ = (
        CheckConstraint("task_id <> depends_on_task_id", name="ck_task_dep_not_self"),
    )
```

- [ ] **Step 5: Register the models**

```python
# app/db/models/__init__.py  (add near the other imports)
from app.db.models.roadmap import (  # noqa: F401
    Roadmap,
    RoadmapMilestone,
    RoadmapPhase,
    RoadmapTask,
    RoadmapTaskDependency,
)
```

- [ ] **Step 6: Add the factories**

```python
# tests/factories.py  (add imports + functions)
from app.db.models.enums import RoadmapStatus, StartupStage, TaskEffort
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase, RoadmapTask


def create_roadmap(db, startup, *, stage=StartupStage.validation,
                   template_key="stage.validation", template_version=1) -> Roadmap:
    r = Roadmap(startup_id=startup.id, stage=stage,
                template_key=template_key, template_version=template_version)
    db.add(r)
    db.flush()
    return r


def create_phase(db, roadmap, *, name="Phase", order=0) -> RoadmapPhase:
    p = RoadmapPhase(roadmap_id=roadmap.id, name=name, order=order)
    db.add(p)
    db.flush()
    return p


def create_milestone(db, phase, *, title="M", status=RoadmapStatus.todo, progress=0,
                     due_on=None, owner=None, order=0) -> RoadmapMilestone:
    m = RoadmapMilestone(phase_id=phase.id, title=title, status=status, progress=progress,
                         due_on=due_on, owner_id=(owner.id if owner else None), order=order)
    db.add(m)
    db.flush()
    return m


def create_task(db, milestone, *, title="T", effort=TaskEffort.medium,
                status=RoadmapStatus.todo, assignee=None, due_on=None, order=0) -> RoadmapTask:
    t = RoadmapTask(milestone_id=milestone.id, title=title, effort=effort, status=status,
                    assignee_id=(assignee.id if assignee else None), due_on=due_on, order=order)
    db.add(t)
    db.flush()
    return t
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/models/test_roadmap_models.py -v`
Expected: PASS (2 tests).

- [ ] **Step 8: Commit**

```bash
git add app/db/models/enums.py app/db/models/roadmap.py app/db/models/__init__.py tests/factories.py tests/models/test_roadmap_models.py
git commit -m "feat(roadmap): enums, tree models, factories"
```

---

## Task 2: Migration `0006_roadmap`

**Files:**
- Create: `alembic/versions/0006_roadmap.py`
- Test: (verified via `alembic upgrade` + a schema round-trip)

**Interfaces:**
- Consumes: models from Task 1.
- Produces: migration revision `0006_roadmap`, `down_revision = '0005_health_score'`.

- [ ] **Step 1: Autogenerate the migration**

Run: `alembic revision --autogenerate -m "roadmap" --rev-id 0006_roadmap`
Then open the generated file and confirm `down_revision = '0005_health_score'`.

- [ ] **Step 2: Verify the migration content**

Confirm `op.create_table` for `roadmaps` (with `UniqueConstraint`/unique index on `startup_id`), `roadmap_phases`, `roadmap_milestones`, `roadmap_tasks`, `roadmap_task_dependencies` (composite PK + `ck_task_dep_not_self` check), and that `downgrade()` drops them in reverse FK order. Remove any unrelated autogen noise (server_default/CHECK-only diffs from earlier tables) — same discipline as `0004`/`0005`.

- [ ] **Step 3: Run the migration up and down**

Run: `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`
Expected: clean up/down/up with no errors.

- [ ] **Step 4: Verify the full suite still migrates**

Run: `pytest tests/models/test_roadmap_models.py -v`
Expected: PASS against the migrated schema.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0006_roadmap.py
git commit -m "feat(roadmap): migration 0006_roadmap"
```

---

## Task 3: Template catalog + `ROADMAP_TEMPLATE_VERSION`

**Files:**
- Create: `app/services/roadmap/__init__.py` (empty)
- Create: `app/services/roadmap/templates.py`
- Test: `tests/services/test_roadmap_templates.py`

**Interfaces:**
- Produces: `ROADMAP_TEMPLATE_VERSION: int`; `STAGE_TEMPLATES: dict[str, dict]` keyed by every `StartupStage.value`. Each template: `{"key": str, "phases": [{"name","start_week","end_week","milestones":[{"title","due_week","tasks":[{"title","effort"}]}]}]}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_roadmap_templates.py
from app.db.models.enums import StartupStage, TaskEffort
from app.services.roadmap.templates import ROADMAP_TEMPLATE_VERSION, STAGE_TEMPLATES


def test_every_stage_has_a_template():
    for stage in StartupStage:
        assert stage.value in STAGE_TEMPLATES, stage.value


def test_templates_are_well_formed():
    assert ROADMAP_TEMPLATE_VERSION >= 1
    valid_efforts = {e.value for e in TaskEffort}
    for stage, tmpl in STAGE_TEMPLATES.items():
        assert tmpl["key"].startswith("stage.")
        assert tmpl["phases"], stage
        for ph in tmpl["phases"]:
            assert ph["end_week"] >= ph["start_week"]
            assert ph["milestones"], (stage, ph["name"])
            for ms in ph["milestones"]:
                assert ms["due_week"] >= 0
                for tk in ms["tasks"]:
                    assert tk["effort"] in valid_efforts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/services/test_roadmap_templates.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the catalog**

Author a real skeleton for all six stages (idea, validation, build, launch, growth, scale). Example (validation shown; author the rest in the same shape, 2–3 phases each, 2–3 milestones/phase, 2–4 tasks/milestone):

```python
# app/services/roadmap/templates.py
ROADMAP_TEMPLATE_VERSION = 1

STAGE_TEMPLATES = {
    "idea": {"key": "stage.idea", "phases": [
        {"name": "Shape the idea", "start_week": 0, "end_week": 4, "milestones": [
            {"title": "Write your problem statement", "due_week": 1, "tasks": [
                {"title": "Describe the problem in one paragraph", "effort": "small"},
                {"title": "List who has this problem", "effort": "small"}]},
            {"title": "Sketch the solution", "due_week": 3, "tasks": [
                {"title": "Draft a one-page concept", "effort": "medium"}]}]},
        {"name": "First signals", "start_week": 4, "end_week": 8, "milestones": [
            {"title": "Talk to 5 potential users", "due_week": 6, "tasks": [
                {"title": "Recruit 5 interviewees", "effort": "medium"},
                {"title": "Run and note the interviews", "effort": "medium"}]}]}]},
    "validation": {"key": "stage.validation", "phases": [
        {"name": "Validation", "start_week": 0, "end_week": 6, "milestones": [
            {"title": "Validate demand", "due_week": 2, "tasks": [
                {"title": "Run 10 customer interviews", "effort": "medium"},
                {"title": "Synthesize problem hypotheses", "effort": "small"}]},
            {"title": "Pricing test", "due_week": 5, "tasks": [
                {"title": "Draft 3 pricing options", "effort": "small"},
                {"title": "Run a willingness-to-pay test", "effort": "medium"}]}]},
        {"name": "Build MVP", "start_week": 6, "end_week": 14, "milestones": [
            {"title": "Core flow", "due_week": 10, "tasks": [
                {"title": "Define the core user journey", "effort": "medium"},
                {"title": "Build the happy path", "effort": "large"}]}]}]},
    "build": {"key": "stage.build", "phases": [
        {"name": "Build MVP", "start_week": 0, "end_week": 10, "milestones": [
            {"title": "Ship the MVP", "due_week": 8, "tasks": [
                {"title": "Finish the core feature set", "effort": "large"},
                {"title": "Set up analytics", "effort": "small"}]}]},
        {"name": "Closed beta", "start_week": 10, "end_week": 16, "milestones": [
            {"title": "Run a closed beta", "due_week": 14, "tasks": [
                {"title": "Recruit 20 beta users", "effort": "medium"},
                {"title": "Collect and triage feedback", "effort": "medium"}]}]}]},
    "launch": {"key": "stage.launch", "phases": [
        {"name": "Launch prep", "start_week": 0, "end_week": 4, "milestones": [
            {"title": "Prepare go-to-market", "due_week": 3, "tasks": [
                {"title": "Write launch messaging", "effort": "medium"},
                {"title": "Line up launch channels", "effort": "medium"}]}]},
        {"name": "Public launch", "start_week": 4, "end_week": 8, "milestones": [
            {"title": "Public launch", "due_week": 6, "tasks": [
                {"title": "Ship the public release", "effort": "large"},
                {"title": "Monitor and respond to issues", "effort": "medium"}]}]}]},
    "growth": {"key": "stage.growth", "phases": [
        {"name": "Acquisition", "start_week": 0, "end_week": 8, "milestones": [
            {"title": "Find a repeatable channel", "due_week": 6, "tasks": [
                {"title": "Test 3 acquisition channels", "effort": "large"},
                {"title": "Double down on the best one", "effort": "medium"}]}]},
        {"name": "Retention", "start_week": 8, "end_week": 16, "milestones": [
            {"title": "Improve retention", "due_week": 12, "tasks": [
                {"title": "Instrument the activation funnel", "effort": "medium"},
                {"title": "Ship one retention improvement", "effort": "large"}]}]}]},
    "scale": {"key": "stage.scale", "phases": [
        {"name": "Scale operations", "start_week": 0, "end_week": 12, "milestones": [
            {"title": "Harden the org", "due_week": 8, "tasks": [
                {"title": "Document core processes", "effort": "medium"},
                {"title": "Hire against the plan", "effort": "large"}]}]},
        {"name": "Expand", "start_week": 12, "end_week": 24, "milestones": [
            {"title": "Open a new segment or market", "due_week": 20, "tasks": [
                {"title": "Validate the new segment", "effort": "large"},
                {"title": "Adapt positioning", "effort": "medium"}]}]}]},
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/services/test_roadmap_templates.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/roadmap/__init__.py app/services/roadmap/templates.py tests/services/test_roadmap_templates.py
git commit -m "feat(roadmap): stage template catalog (v1)"
```

---

## Task 4: `generate_roadmap` service (create-once) + `roadmap.generated`

**Files:**
- Create: `app/services/roadmap/service.py`
- Test: `tests/services/test_roadmap_generate.py`

**Interfaces:**
- Consumes: `STAGE_TEMPLATES`, `ROADMAP_TEMPLATE_VERSION` (Task 3); models (Task 1); `event_bus` (`app.platform.events`).
- Produces: `generate_roadmap(db: Session, startup: Startup, *, actor: User | None = None) -> Roadmap` — idempotent create-once; builds the tree from the startup's stage template; emits `roadmap.generated`; returns the (new or existing) `Roadmap`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_roadmap_generate.py
from datetime import date, timedelta

from app.db.models.enums import StartupStage
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase, RoadmapTask
from app.services.roadmap.service import generate_roadmap
from tests.factories import create_startup, create_user


def test_generate_builds_the_stage_tree(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner, stage=StartupStage.validation)

    r = generate_roadmap(db, startup)
    db.flush()

    assert r.stage == StartupStage.validation
    assert r.template_key == "stage.validation"
    assert r.template_version == 1
    assert db.query(RoadmapPhase).filter_by(roadmap_id=r.id).count() >= 1
    assert db.query(RoadmapMilestone).count() >= 1
    assert db.query(RoadmapTask).count() >= 1

    # dates derive from week offsets off today; first phase starts today
    first_phase = (
        db.query(RoadmapPhase).filter_by(roadmap_id=r.id)
        .order_by(RoadmapPhase.order).first()
    )
    assert first_phase.starts_on == date.today()


def test_generate_is_create_once(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner, stage=StartupStage.validation)

    r1 = generate_roadmap(db, startup)
    db.flush()
    phase_count = db.query(RoadmapPhase).count()

    r2 = generate_roadmap(db, startup)
    db.flush()

    assert r2.id == r1.id
    assert db.query(Roadmap).count() == 1
    assert db.query(RoadmapPhase).count() == phase_count  # no duplication


def test_generate_emits_event(db, monkeypatch):
    events = []
    from app.platform import events as events_mod

    monkeypatch.setattr(events_mod.event_bus, "publish",
                        lambda e, p: events.append((e, p)))
    owner = create_user(db)
    startup = create_startup(db, owner=owner, stage=StartupStage.idea)
    generate_roadmap(db, startup)

    assert any(e == "roadmap.generated" for e, _ in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/services/test_roadmap_generate.py -v`
Expected: FAIL with `ModuleNotFoundError` / `generate_roadmap` undefined.

- [ ] **Step 3: Implement the service**

```python
# app/services/roadmap/service.py
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.db.models.enums import RoadmapStatus, TaskEffort
from app.db.models.roadmap import (
    Roadmap,
    RoadmapMilestone,
    RoadmapPhase,
    RoadmapTask,
    RoadmapTaskDependency,
)
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.events import event_bus
from app.services.roadmap.templates import ROADMAP_TEMPLATE_VERSION, STAGE_TEMPLATES


def _weeks(base: date, n: int) -> date:
    return base + timedelta(weeks=n)


def generate_roadmap(db: Session, startup: Startup, *, actor: User | None = None) -> Roadmap:
    # Create-once claim: race-safe against concurrent onboarding/generate/lazy-GET.
    stmt = (
        insert(Roadmap)
        .values(
            id=uuid.uuid4(),
            startup_id=startup.id,
            stage=startup.stage,
            template_key=STAGE_TEMPLATES[_stage_key(startup)]["key"],
            template_version=ROADMAP_TEMPLATE_VERSION,
            generated_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["startup_id"])
        .returning(Roadmap.id)
    )
    # SQLAlchemy 2.0: use postgresql dialect insert for on_conflict_do_nothing
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(Roadmap)
        .values(
            id=uuid.uuid4(),
            startup_id=startup.id,
            stage=startup.stage,
            template_key=STAGE_TEMPLATES[_stage_key(startup)]["key"],
            template_version=ROADMAP_TEMPLATE_VERSION,
            generated_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["startup_id"])
        .returning(Roadmap.id)
    )
    new_id = db.execute(stmt).scalar_one_or_none()
    if new_id is None:
        # Someone else generated it (their row is committed by the time we read).
        return db.query(Roadmap).filter_by(startup_id=startup.id).one()

    roadmap = db.query(Roadmap).filter_by(id=new_id).one()
    tmpl = STAGE_TEMPLATES[_stage_key(startup)]
    base = date.today()
    milestone_count = 0

    for p_idx, ph in enumerate(tmpl["phases"]):
        phase = RoadmapPhase(
            roadmap_id=roadmap.id, name=ph["name"], order=p_idx,
            starts_on=_weeks(base, ph["start_week"]),
            ends_on=_weeks(base, ph["end_week"]),
        )
        db.add(phase)
        db.flush()
        for m_idx, ms in enumerate(ph["milestones"]):
            milestone = RoadmapMilestone(
                phase_id=phase.id, title=ms["title"], due_on=_weeks(base, ms["due_week"]),
                status=RoadmapStatus.todo, progress=0, order=m_idx,
            )
            db.add(milestone)
            db.flush()
            milestone_count += 1
            for t_idx, tk in enumerate(ms["tasks"]):
                db.add(RoadmapTask(
                    milestone_id=milestone.id, title=tk["title"],
                    effort=TaskEffort(tk["effort"]), status=RoadmapStatus.todo, order=t_idx,
                ))
    db.flush()

    event_bus.publish("roadmap.generated", {
        "startup_id": str(startup.id), "roadmap_id": str(roadmap.id),
        "stage": startup.stage.value, "template_key": roadmap.template_key,
        "milestone_count": milestone_count,
    })
    return roadmap


def _stage_key(startup: Startup) -> str:
    if startup.stage is not None and startup.stage.value in STAGE_TEMPLATES:
        return startup.stage.value
    return "idea"  # defensive fallback; onboarding guarantees a stage
```

Note: keep only the `pg_insert` version — delete the first `stmt` assignment written above (it's shown to make the dialect-import requirement explicit). The final code imports `from sqlalchemy.dialects.postgresql import insert as pg_insert` at module top and uses it once.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/services/test_roadmap_generate.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Write the concurrency (create-once race) test**

```python
# tests/services/test_roadmap_generate.py  (append)
def test_generate_create_once_under_race(db, db_engine):
    """Two independent connections generating concurrently => exactly one roadmap."""
    from sqlalchemy.orm import Session

    owner = create_user(db)
    startup = create_startup(db, owner=owner, stage=StartupStage.validation)
    db.commit()  # make the startup visible to a second connection

    try:
        s1 = Session(bind=db_engine)
        s2 = Session(bind=db_engine)
        st1 = s1.query(type(startup)).get(startup.id)
        st2 = s2.query(type(startup)).get(startup.id)
        generate_roadmap(s1, st1)
        generate_roadmap(s2, st2)  # blocks on s1's uncommitted row, then no-ops
        s1.commit()
        s2.commit()

        assert db.query(Roadmap).filter_by(startup_id=startup.id).count() == 1
    finally:
        s1.close()
        s2.close()
        # cleanup committed rows so the shared DB stays clean for other tests
        db.query(Roadmap).filter_by(startup_id=startup.id).delete()
        db.query(type(startup)).filter_by(id=startup.id).delete()
        db.commit()
```

If `db_engine` is not an existing fixture, use the engine exposed by `tests/conftest.py` (inspect it — the health-score/assessment race tests already open a second `Session`; mirror that fixture name exactly).

- [ ] **Step 6: Run the race test**

Run: `pytest tests/services/test_roadmap_generate.py::test_generate_create_once_under_race -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/roadmap/service.py tests/services/test_roadmap_generate.py
git commit -m "feat(roadmap): generate_roadmap create-once + roadmap.generated event"
```

---

## Task 5: `recompute_milestone_progress` + `GET /roadmap` (serialize + lazy generate)

**Files:**
- Modify: `app/services/roadmap/service.py` (add `recompute_milestone_progress`, `milestone_overdue`, `person_ref`, `serialize_tree`)
- Create: `app/api/v1/endpoints/roadmap.py` (GET + resolvers)
- Modify: `app/api/v1/api.py` (register router)
- Test: `tests/services/test_roadmap_progress.py`, `tests/api/test_roadmap_get.py`

**Interfaces:**
- Produces:
  - `recompute_milestone_progress(db, milestone) -> None` — sets `milestone.progress`.
  - `milestone_overdue(m) -> bool`, `task_overdue(t) -> bool`.
  - `serialize_tree(db, roadmap, startup) -> dict` — the `GET /roadmap` `data` payload.
  - `GET /api/v1/roadmap` → `success_response(serialize_tree(...))`, lazy-generating if absent.
  - Resolver `_roadmap(db, membership) -> Roadmap | None`.

- [ ] **Step 1: Write the failing progress test**

```python
# tests/services/test_roadmap_progress.py
from app.db.models.enums import RoadmapStatus
from app.services.roadmap.service import recompute_milestone_progress
from tests.factories import (
    create_milestone, create_phase, create_roadmap, create_startup, create_task, create_user,
)


def test_progress_is_done_ratio(db):
    startup = create_startup(db, owner=create_user(db))
    roadmap = create_roadmap(db, startup)
    phase = create_phase(db, roadmap)
    m = create_milestone(db, phase)
    create_task(db, m, status=RoadmapStatus.done)
    create_task(db, m, status=RoadmapStatus.done)
    create_task(db, m, status=RoadmapStatus.todo)

    recompute_milestone_progress(db, m)
    assert m.progress == 67  # round(100 * 2/3)


def test_progress_zero_with_no_tasks(db):
    startup = create_startup(db, owner=create_user(db))
    m = create_milestone(db, create_phase(db, create_roadmap(db, startup)))
    recompute_milestone_progress(db, m)
    assert m.progress == 0


def test_progress_100_when_milestone_done_and_no_tasks(db):
    startup = create_startup(db, owner=create_user(db))
    m = create_milestone(db, create_phase(db, create_roadmap(db, startup)),
                         status=RoadmapStatus.done)
    recompute_milestone_progress(db, m)
    assert m.progress == 100
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/services/test_roadmap_progress.py -v`
Expected: FAIL (`recompute_milestone_progress` undefined).

- [ ] **Step 3: Add progress/overdue/serialize helpers**

```python
# app/services/roadmap/service.py  (append)
from datetime import date as _date

from app.core.envelope import success_response  # noqa: F401  (used by endpoints, not here)


def recompute_milestone_progress(db: Session, milestone: RoadmapMilestone) -> None:
    tasks = db.query(RoadmapTask).filter_by(milestone_id=milestone.id).all()
    if not tasks:
        milestone.progress = 100 if milestone.status == RoadmapStatus.done else 0
        return
    done = sum(1 for t in tasks if t.status == RoadmapStatus.done)
    milestone.progress = round(100 * done / len(tasks))


def milestone_overdue(m: RoadmapMilestone) -> bool:
    return m.due_on is not None and m.due_on < _date.today() and m.status != RoadmapStatus.done


def task_overdue(t: RoadmapTask) -> bool:
    return t.due_on is not None and t.due_on < _date.today() and t.status != RoadmapStatus.done


def person_ref(db: Session, user_id) -> dict | None:
    if user_id is None:
        return None
    u = db.query(User).filter_by(id=user_id).first()
    if u is None:
        return None
    name = getattr(getattr(u, "profile", None), "full_name", None)
    return {"id": str(u.id), "name": name}


def serialize_tree(db: Session, roadmap: Roadmap, startup: Startup) -> dict:
    phases = (
        db.query(RoadmapPhase).filter_by(roadmap_id=roadmap.id)
        .order_by(RoadmapPhase.order).all()
    )
    out_phases = []
    for ph in phases:
        milestones = (
            db.query(RoadmapMilestone).filter_by(phase_id=ph.id)
            .order_by(RoadmapMilestone.order).all()
        )
        out_ms = []
        for m in milestones:
            tasks = (
                db.query(RoadmapTask).filter_by(milestone_id=m.id)
                .order_by(RoadmapTask.order).all()
            )
            dep_count = (
                db.query(RoadmapTaskDependency)
                .filter(RoadmapTaskDependency.task_id.in_([t.id for t in tasks]))
                .count() if tasks else 0
            )
            out_ms.append({
                "id": str(m.id), "title": m.title, "description": m.description,
                "due_on": m.due_on.isoformat() if m.due_on else None,
                "owner": person_ref(db, m.owner_id), "status": m.status.value,
                "progress": m.progress, "overdue": milestone_overdue(m),
                "order": m.order, "dependency_count": dep_count,
                "tasks": [{
                    "id": str(t.id), "title": t.title, "description": t.description,
                    "effort": t.effort.value, "status": t.status.value,
                    "assignee": person_ref(db, t.assignee_id),
                    "due_on": t.due_on.isoformat() if t.due_on else None,
                    "overdue": task_overdue(t), "order": t.order, "depends_on": [],
                } for t in tasks],
            })
        out_phases.append({
            "id": str(ph.id), "name": ph.name, "order": ph.order,
            "starts_on": ph.starts_on.isoformat() if ph.starts_on else None,
            "ends_on": ph.ends_on.isoformat() if ph.ends_on else None,
            "milestones": out_ms,
        })
    return {
        "roadmap": {
            "id": str(roadmap.id), "stage": roadmap.stage.value,
            "template_key": roadmap.template_key,
            "generated_at": roadmap.generated_at.isoformat(),
        },
        "current_stage": startup.stage.value if startup.stage else None,
        "phases": out_phases,
    }
```

- [ ] **Step 4: Run to verify the progress tests pass**

Run: `pytest tests/services/test_roadmap_progress.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Write the failing GET test**

```python
# tests/api/test_roadmap_get.py
from app.db.models.enums import MembershipRole, MembershipStatus, StartupStage


def test_get_roadmap_lazy_generates(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.validation)
    r = client.get("/api/v1/roadmap", headers=ctx.headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["current_stage"] == "validation"
    assert data["roadmap"]["template_key"] == "stage.validation"
    assert len(data["phases"]) >= 1
    assert data["phases"][0]["milestones"][0]["tasks"][0]["depends_on"] == []


def test_get_roadmap_requires_auth(client):
    assert client.get("/api/v1/roadmap").status_code == 401
```

`make_member_ctx` is the existing helper used by assessment/health-score API tests to mint a verified user + membership + `X-Workspace-Id`/bearer headers. Inspect `tests/api/` for its real name/signature and mirror it; if it takes `stage`, pass it through to `create_startup`.

- [ ] **Step 6: Run to verify it fails**

Run: `pytest tests/api/test_roadmap_get.py -v`
Expected: FAIL (404 route not found).

- [ ] **Step 7: Create the endpoint file + register**

```python
# app/api/v1/endpoints/roadmap.py
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.membership import Membership
from app.db.models.roadmap import Roadmap
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_workspace
from app.services.roadmap.service import generate_roadmap, serialize_tree

router = APIRouter()


def _startup(db: Session, membership: Membership) -> Startup:
    s = db.query(Startup).filter(Startup.id == membership.startup_id).first()
    if s is None:
        raise NotFound()
    return s


def _roadmap(db: Session, membership: Membership) -> Roadmap | None:
    return db.query(Roadmap).filter(Roadmap.startup_id == membership.startup_id).first()


@router.get("")
def get_roadmap(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    roadmap = _roadmap(db, membership)
    if roadmap is None:
        roadmap = generate_roadmap(db, startup, actor=user)
    db.commit()
    return success_response(serialize_tree(db, roadmap, startup))
```

```python
# app/api/v1/api.py  (add)
from app.api.v1.endpoints import roadmap
api_router.include_router(roadmap.router, prefix="/roadmap", tags=["roadmap"])
```

- [ ] **Step 8: Run to verify it passes**

Run: `pytest tests/api/test_roadmap_get.py -v`
Expected: PASS (2 tests).

- [ ] **Step 9: Commit**

```bash
git add app/services/roadmap/service.py app/api/v1/endpoints/roadmap.py app/api/v1/api.py tests/services/test_roadmap_progress.py tests/api/test_roadmap_get.py
git commit -m "feat(roadmap): GET /roadmap tree + lazy generate + progress helper"
```

---

## Task 6: `POST /roadmap/generate` (202 job) + wire inline into onboarding

**Files:**
- Modify: `app/api/v1/endpoints/roadmap.py` (add the generate route + `require_role` import)
- Modify: `app/services/onboarding/complete.py` (replace the stub enqueue)
- Test: `tests/api/test_roadmap_generate_endpoint.py`, `tests/services/test_onboarding_generates_roadmap.py`

**Interfaces:**
- Consumes: `generate_roadmap` (Task 4), `job_dispatcher` (`app.platform.jobs`), `JobStatus` (`app.db.models.enums`).
- Produces: `POST /api/v1/roadmap/generate` → `202 {"data": {"job_id","status":"succeeded"}}`.

- [ ] **Step 1: Write the failing endpoint test**

```python
# tests/api/test_roadmap_generate_endpoint.py
from app.db.models.enums import MembershipRole, StartupStage


def test_generate_returns_succeeded_job(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.validation)
    r = client.post("/api/v1/roadmap/generate", headers=ctx.headers)
    assert r.status_code == 202
    data = r.json()["data"]
    assert data["status"] == "succeeded"
    assert data["job_id"]


def test_generate_is_create_once_via_endpoint(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.validation)
    client.post("/api/v1/roadmap/generate", headers=ctx.headers)
    client.post("/api/v1/roadmap/generate", headers=ctx.headers)
    from app.db.models.roadmap import Roadmap
    assert db.query(Roadmap).filter_by(startup_id=ctx.startup_id).count() == 1


def test_generate_forbidden_for_mentor(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.mentor, stage=StartupStage.validation)
    assert client.post("/api/v1/roadmap/generate", headers=ctx.headers).status_code == 403
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/api/test_roadmap_generate_endpoint.py -v`
Expected: FAIL (404 / route missing).

- [ ] **Step 3: Add the generate route**

```python
# app/api/v1/endpoints/roadmap.py  (add imports + route)
from app.db.models.enums import JobStatus, MembershipRole
from app.db.tenancy import require_role
from app.platform.jobs import job_dispatcher


@router.post("/generate", status_code=202)
def post_generate(
    membership: Membership = Depends(  # noqa: B008
        require_role(MembershipRole.founder, MembershipRole.team_member)
    ),
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    generate_roadmap(db, startup, actor=user)
    job = job_dispatcher.enqueue(db, "roadmap.generate", {"startup_id": str(startup.id)}, startup.id)
    job.status = JobStatus.succeeded
    db.commit()
    return success_response({"job_id": str(job.id), "status": job.status.value})
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/api/test_roadmap_generate_endpoint.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Write the onboarding-wiring test**

```python
# tests/services/test_onboarding_generates_roadmap.py
from app.db.models.enums import StartupStage
from app.db.models.roadmap import Roadmap
from app.services.onboarding.complete import complete_onboarding
from tests.factories import create_startup, create_user


def test_completing_onboarding_generates_roadmap_inline(db):
    user = create_user(db, email_verified_at=_now())
    startup = create_startup(db, owner=user, name="Acme", industry="fintech",
                             stage=StartupStage.validation)
    startup.profile.goals = ["ship_mvp"]
    startup.profile.full_name = "Amara"  # satisfy the gate as your gate expects
    db.flush()

    result = complete_onboarding(db, startup, user)  # match the real signature
    assert db.query(Roadmap).filter_by(startup_id=startup.id).count() == 1
    assert result["job_ids"]  # roadmap job id still present for FE parity


def _now():
    from datetime import UTC, datetime
    return datetime.now(UTC)
```

Before writing this test, open `app/services/onboarding/complete.py` and match the **exact** `complete_onboarding` signature and the gate's required fields (founder full_name, startup name/industry/stage, ≥1 goal). Adjust the setup so the gate passes.

- [ ] **Step 6: Run to verify it fails**

Run: `pytest tests/services/test_onboarding_generates_roadmap.py -v`
Expected: FAIL (no roadmap generated — still a stub enqueue).

- [ ] **Step 7: Replace the stub enqueue with inline generation**

```python
# app/services/onboarding/complete.py
# add import:
from app.db.models.enums import JobStatus
from app.services.roadmap.service import generate_roadmap

# replace the j1 line:
#   j1 = job_dispatcher.enqueue(db, "roadmap.generate", {"startup_id": str(startup.id)}, startup.id)
# with:
    generate_roadmap(db, startup, actor=user)
    j1 = job_dispatcher.enqueue(
        db, "roadmap.generate", {"startup_id": str(startup.id)}, startup.id
    )
    j1.status = JobStatus.succeeded
```

Leave `j2` (`healthscore.initialize`) untouched — it stays an unconsumed stub by design (Health Score is pending until the assessment).

- [ ] **Step 8: Run to verify it passes + full onboarding suite is green**

Run: `pytest tests/services/test_onboarding_generates_roadmap.py tests/api -k onboarding -v`
Expected: PASS (roadmap generated; existing onboarding tests still green — `job_ids` still returns two ids).

- [ ] **Step 9: Commit**

```bash
git add app/api/v1/endpoints/roadmap.py app/services/onboarding/complete.py tests/api/test_roadmap_generate_endpoint.py tests/services/test_onboarding_generates_roadmap.py
git commit -m "feat(roadmap): POST /generate (202 job) + inline generation on onboarding-complete"
```

---

## Task 7: Phases CRUD

**Files:**
- Create: `app/schemas/roadmap.py` (phase schemas here; milestone/task schemas added in later tasks)
- Modify: `app/api/v1/endpoints/roadmap.py` (add `_phase` resolver + 3 routes)
- Test: `tests/api/test_roadmap_phases.py`

**Interfaces:**
- Produces: `PhaseCreate`, `PhaseUpdate` (Pydantic); routes `POST /roadmap/phases`, `PATCH /roadmap/phases/{id}`, `DELETE /roadmap/phases/{id}`; resolver `_phase(db, membership, phase_id) -> RoadmapPhase`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_roadmap_phases.py
from app.db.models.enums import MembershipRole, StartupStage


def _roadmap(client, ctx):
    client.get("/api/v1/roadmap", headers=ctx.headers)  # ensure generated


def test_create_patch_delete_phase(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    _roadmap(client, ctx)

    r = client.post("/api/v1/roadmap/phases", headers=ctx.headers,
                    json={"name": "Extra phase"})
    assert r.status_code == 201
    pid = r.json()["data"]["id"]

    r = client.patch(f"/api/v1/roadmap/phases/{pid}", headers=ctx.headers,
                     json={"name": "Renamed"})
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "Renamed"

    assert client.delete(f"/api/v1/roadmap/phases/{pid}", headers=ctx.headers).status_code == 200


def test_phase_write_forbidden_for_mentor(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.mentor, stage=StartupStage.idea)
    assert client.post("/api/v1/roadmap/phases", headers=ctx.headers,
                       json={"name": "x"}).status_code == 403


def test_phase_cross_workspace_404(client, db, make_member_ctx):
    a = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    b = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    _roadmap(client, a)
    r = client.post("/api/v1/roadmap/phases", headers=a.headers, json={"name": "p"})
    pid = r.json()["data"]["id"]
    # b tries to patch a's phase
    assert client.patch(f"/api/v1/roadmap/phases/{pid}", headers=b.headers,
                        json={"name": "z"}).status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/api/test_roadmap_phases.py -v`
Expected: FAIL (routes missing).

- [ ] **Step 3: Add the schemas**

```python
# app/schemas/roadmap.py
from datetime import date

from pydantic import BaseModel


class PhaseCreate(BaseModel):
    name: str
    order: int | None = None
    starts_on: date | None = None
    ends_on: date | None = None


class PhaseUpdate(BaseModel):
    name: str | None = None
    order: int | None = None
    starts_on: date | None = None
    ends_on: date | None = None
```

- [ ] **Step 4: Add the resolver + routes**

```python
# app/api/v1/endpoints/roadmap.py  (add)
import uuid

from app.db.models.roadmap import RoadmapPhase
from app.schemas.roadmap import PhaseCreate, PhaseUpdate

_editor = require_role(MembershipRole.founder, MembershipRole.team_member)


def _require_roadmap(db: Session, membership: Membership) -> Roadmap:
    r = _roadmap(db, membership)
    if r is None:
        raise NotFound()
    return r


def _phase(db: Session, membership: Membership, phase_id: uuid.UUID) -> RoadmapPhase:
    roadmap = _require_roadmap(db, membership)
    p = (
        db.query(RoadmapPhase)
        .filter(RoadmapPhase.id == phase_id, RoadmapPhase.roadmap_id == roadmap.id)
        .first()
    )
    if p is None:
        raise NotFound()
    return p


def _next_order(db: Session, model, **filters) -> int:
    from sqlalchemy import func as _func
    val = db.query(_func.max(model.order)).filter_by(**filters).scalar()
    return 0 if val is None else val + 1


def _phase_out(p: RoadmapPhase) -> dict[str, Any]:
    return {"id": str(p.id), "name": p.name, "order": p.order,
            "starts_on": p.starts_on.isoformat() if p.starts_on else None,
            "ends_on": p.ends_on.isoformat() if p.ends_on else None}


@router.post("/phases", status_code=201)
def create_phase(
    body: PhaseCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    roadmap = _require_roadmap(db, membership)
    order = body.order if body.order is not None else _next_order(
        db, RoadmapPhase, roadmap_id=roadmap.id
    )
    p = RoadmapPhase(roadmap_id=roadmap.id, name=body.name, order=order,
                     starts_on=body.starts_on, ends_on=body.ends_on)
    db.add(p)
    db.commit()
    return success_response(_phase_out(p))


@router.patch("/phases/{phase_id}")
def update_phase(
    phase_id: uuid.UUID,
    body: PhaseUpdate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    p = _phase(db, membership, phase_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(p, field, value)
    db.commit()
    return success_response(_phase_out(p))


@router.delete("/phases/{phase_id}")
def delete_phase(
    phase_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    p = _phase(db, membership, phase_id)
    db.delete(p)
    db.commit()
    return success_response({"deleted": True})
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/api/test_roadmap_phases.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/schemas/roadmap.py app/api/v1/endpoints/roadmap.py tests/api/test_roadmap_phases.py
git commit -m "feat(roadmap): phases CRUD"
```

---

## Task 8: Milestones CRUD + mark-complete transition event

**Files:**
- Modify: `app/schemas/roadmap.py` (milestone schemas)
- Modify: `app/api/v1/endpoints/roadmap.py` (`_milestone` resolver + 3 routes + member validation + transition event)
- Test: `tests/api/test_roadmap_milestones.py`

**Interfaces:**
- Produces: `MilestoneCreate`, `MilestoneUpdate`; routes `POST /roadmap/milestones`, `PATCH /roadmap/milestones/{id}`, `DELETE /roadmap/milestones/{id}`; resolver `_milestone(db, membership, id) -> RoadmapMilestone`; helper `_validate_member(db, membership, user_id, field)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_roadmap_milestones.py
from app.db.models.enums import MembershipRole, StartupStage


def _first_phase_id(client, ctx):
    data = client.get("/api/v1/roadmap", headers=ctx.headers).json()["data"]
    return data["phases"][0]["id"]


def test_milestone_create_and_complete_emits_event(client, db, make_member_ctx, capture_events):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    phase_id = _first_phase_id(client, ctx)

    r = client.post("/api/v1/roadmap/milestones", headers=ctx.headers,
                    json={"phase_id": phase_id, "title": "New milestone"})
    assert r.status_code == 201
    mid = r.json()["data"]["id"]

    capture_events.clear()
    r = client.patch(f"/api/v1/roadmap/milestones/{mid}", headers=ctx.headers,
                     json={"status": "done"})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "done"
    assert any(e == "roadmap.milestone.completed" for e, _ in capture_events)

    # re-patching done->done stays silent
    capture_events.clear()
    client.patch(f"/api/v1/roadmap/milestones/{mid}", headers=ctx.headers,
                 json={"status": "done"})
    assert not any(e == "roadmap.milestone.completed" for e, _ in capture_events)


def test_milestone_foreign_phase_404(client, db, make_member_ctx):
    a = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    b = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    a_phase = _first_phase_id(client, a)
    r = client.post("/api/v1/roadmap/milestones", headers=b.headers,
                    json={"phase_id": a_phase, "title": "x"})
    assert r.status_code == 404


def test_milestone_owner_must_be_member(client, db, make_member_ctx):
    import uuid
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    phase_id = _first_phase_id(client, ctx)
    r = client.post("/api/v1/roadmap/milestones", headers=ctx.headers,
                    json={"phase_id": phase_id, "title": "x", "owner_id": str(uuid.uuid4())})
    assert r.status_code == 422
```

`capture_events` is a fixture that monkeypatches `event_bus.publish` to append `(event, payload)`. If it doesn't exist yet, add it to `tests/conftest.py` mirroring how service tests capture events (see Task 4's `monkeypatch` approach), exposing a list.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/api/test_roadmap_milestones.py -v`
Expected: FAIL (routes missing).

- [ ] **Step 3: Add the schemas**

```python
# app/schemas/roadmap.py  (append)
import uuid

from app.db.models.enums import RoadmapStatus


class MilestoneCreate(BaseModel):
    phase_id: uuid.UUID
    title: str
    description: str | None = None
    due_on: date | None = None
    owner_id: uuid.UUID | None = None
    status: RoadmapStatus | None = None
    order: int | None = None


class MilestoneUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    due_on: date | None = None
    owner_id: uuid.UUID | None = None
    status: RoadmapStatus | None = None
    order: int | None = None
```

- [ ] **Step 4: Add resolver, member validation, and routes**

```python
# app/api/v1/endpoints/roadmap.py  (add)
from app.core.errors import AppError
from app.db.models.enums import MembershipStatus, RoadmapStatus
from app.db.models.roadmap import RoadmapMilestone
from app.platform.events import event_bus
from app.schemas.roadmap import MilestoneCreate, MilestoneUpdate
from app.services.roadmap.service import milestone_overdue


def _validate_member(db: Session, membership: Membership, user_id, field: str) -> None:
    if user_id is None:
        return
    ok = (
        db.query(Membership)
        .filter(Membership.startup_id == membership.startup_id,
                Membership.user_id == user_id,
                Membership.status == MembershipStatus.active)
        .first()
    )
    if ok is None:
        raise AppError("VALIDATION_ERROR", "That user is not a member of this workspace.", 422,
                       field_errors=[{"field": field, "message": "Not an active member."}])


def _milestone(db: Session, membership: Membership, milestone_id: uuid.UUID) -> RoadmapMilestone:
    roadmap = _require_roadmap(db, membership)
    m = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(RoadmapMilestone.id == milestone_id, RoadmapPhase.roadmap_id == roadmap.id)
        .first()
    )
    if m is None:
        raise NotFound()
    return m


def _milestone_out(db: Session, m: RoadmapMilestone) -> dict[str, Any]:
    from app.services.roadmap.service import person_ref
    return {"id": str(m.id), "phase_id": str(m.phase_id), "title": m.title,
            "description": m.description,
            "due_on": m.due_on.isoformat() if m.due_on else None,
            "owner": person_ref(db, m.owner_id), "status": m.status.value,
            "progress": m.progress, "overdue": milestone_overdue(m), "order": m.order}


@router.post("/milestones", status_code=201)
def create_milestone_ep(
    body: MilestoneCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    phase = _phase(db, membership, body.phase_id)  # 404 if foreign/unknown
    _validate_member(db, membership, body.owner_id, "owner_id")
    order = body.order if body.order is not None else _next_order(
        db, RoadmapMilestone, phase_id=phase.id
    )
    m = RoadmapMilestone(
        phase_id=phase.id, title=body.title, description=body.description,
        due_on=body.due_on, owner_id=body.owner_id,
        status=body.status or RoadmapStatus.todo, progress=0, order=order,
    )
    db.add(m)
    db.flush()
    if m.status == RoadmapStatus.done:
        m.progress = 100
    db.commit()
    return success_response(_milestone_out(db, m))


@router.patch("/milestones/{milestone_id}")
def update_milestone_ep(
    milestone_id: uuid.UUID,
    body: MilestoneUpdate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    m = _milestone(db, membership, milestone_id)
    was_done = m.status == RoadmapStatus.done
    fields = body.model_dump(exclude_unset=True)
    if "owner_id" in fields:
        _validate_member(db, membership, fields["owner_id"], "owner_id")
    for field, value in fields.items():
        setattr(m, field, value)
    db.flush()
    # keep progress honest if the milestone itself was flipped and has no tasks
    from app.services.roadmap.service import recompute_milestone_progress
    recompute_milestone_progress(db, m)
    now_done = m.status == RoadmapStatus.done
    if now_done and not was_done:
        roadmap = _require_roadmap(db, membership)
        event_bus.publish("roadmap.milestone.completed", {
            "startup_id": str(membership.startup_id), "roadmap_id": str(roadmap.id),
            "milestone_id": str(m.id), "title": m.title,
        })
    db.commit()
    return success_response(_milestone_out(db, m))


@router.delete("/milestones/{milestone_id}")
def delete_milestone_ep(
    milestone_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    m = _milestone(db, membership, milestone_id)
    db.delete(m)
    db.commit()
    return success_response({"deleted": True})
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/api/test_roadmap_milestones.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/schemas/roadmap.py app/api/v1/endpoints/roadmap.py tests/api/test_roadmap_milestones.py tests/conftest.py
git commit -m "feat(roadmap): milestones CRUD + mark-complete transition event"
```

---

## Task 9: Tasks CRUD + progress recompute

**Files:**
- Modify: `app/schemas/roadmap.py` (task schemas)
- Modify: `app/api/v1/endpoints/roadmap.py` (`_task` resolver + 3 routes; recompute progress on every write)
- Test: `tests/api/test_roadmap_tasks.py`

**Interfaces:**
- Produces: `TaskCreate`, `TaskUpdate`; routes `POST /roadmap/tasks`, `PATCH /roadmap/tasks/{id}`, `DELETE /roadmap/tasks/{id}`; resolver `_task(db, membership, id) -> RoadmapTask`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_roadmap_tasks.py
from app.db.models.enums import MembershipRole, StartupStage


def _first_milestone_id(client, ctx):
    data = client.get("/api/v1/roadmap", headers=ctx.headers).json()["data"]
    return data["phases"][0]["milestones"][0]["id"]


def test_task_lifecycle_updates_milestone_progress(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    mid = _first_milestone_id(client, ctx)

    t1 = client.post("/api/v1/roadmap/tasks", headers=ctx.headers,
                     json={"milestone_id": mid, "title": "A"}).json()["data"]["id"]
    t2 = client.post("/api/v1/roadmap/tasks", headers=ctx.headers,
                     json={"milestone_id": mid, "title": "B"}).json()["data"]["id"]

    client.patch(f"/api/v1/roadmap/tasks/{t1}", headers=ctx.headers, json={"status": "done"})

    data = client.get("/api/v1/roadmap", headers=ctx.headers).json()["data"]
    # find the milestone we edited (it now has our 2 extra tasks among the templated ones)
    ms = next(m for p in data["phases"] for m in p["milestones"] if m["id"] == mid)
    done = sum(1 for t in ms["tasks"] if t["status"] == "done")
    total = len(ms["tasks"])
    assert ms["progress"] == round(100 * done / total)

    assert client.delete(f"/api/v1/roadmap/tasks/{t2}", headers=ctx.headers).status_code == 200


def test_task_foreign_milestone_404(client, db, make_member_ctx):
    a = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    b = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    a_mid = _first_milestone_id(client, a)
    r = client.post("/api/v1/roadmap/tasks", headers=b.headers,
                    json={"milestone_id": a_mid, "title": "x"})
    assert r.status_code == 404


def test_task_assignee_must_be_member(client, db, make_member_ctx):
    import uuid
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    mid = _first_milestone_id(client, ctx)
    r = client.post("/api/v1/roadmap/tasks", headers=ctx.headers,
                    json={"milestone_id": mid, "title": "x", "assignee_id": str(uuid.uuid4())})
    assert r.status_code == 422
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/api/test_roadmap_tasks.py -v`
Expected: FAIL (routes missing).

- [ ] **Step 3: Add the schemas**

```python
# app/schemas/roadmap.py  (append)
from app.db.models.enums import TaskEffort


class TaskCreate(BaseModel):
    milestone_id: uuid.UUID
    title: str
    description: str | None = None
    effort: TaskEffort | None = None
    status: RoadmapStatus | None = None
    assignee_id: uuid.UUID | None = None
    due_on: date | None = None
    order: int | None = None


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    effort: TaskEffort | None = None
    status: RoadmapStatus | None = None
    assignee_id: uuid.UUID | None = None
    due_on: date | None = None
    order: int | None = None
```

- [ ] **Step 4: Add resolver + routes**

```python
# app/api/v1/endpoints/roadmap.py  (add)
from app.db.models.enums import TaskEffort
from app.db.models.roadmap import RoadmapTask
from app.schemas.roadmap import TaskCreate, TaskUpdate
from app.services.roadmap.service import recompute_milestone_progress, task_overdue


def _task(db: Session, membership: Membership, task_id: uuid.UUID) -> RoadmapTask:
    roadmap = _require_roadmap(db, membership)
    t = (
        db.query(RoadmapTask)
        .join(RoadmapMilestone, RoadmapTask.milestone_id == RoadmapMilestone.id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(RoadmapTask.id == task_id, RoadmapPhase.roadmap_id == roadmap.id)
        .first()
    )
    if t is None:
        raise NotFound()
    return t


def _task_out(db: Session, t: RoadmapTask) -> dict[str, Any]:
    from app.services.roadmap.service import person_ref
    return {"id": str(t.id), "milestone_id": str(t.milestone_id), "title": t.title,
            "description": t.description, "effort": t.effort.value, "status": t.status.value,
            "assignee": person_ref(db, t.assignee_id),
            "due_on": t.due_on.isoformat() if t.due_on else None,
            "overdue": task_overdue(t), "order": t.order, "depends_on": []}


@router.post("/tasks", status_code=201)
def create_task_ep(
    body: TaskCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    milestone = _milestone(db, membership, body.milestone_id)  # 404 if foreign
    _validate_member(db, membership, body.assignee_id, "assignee_id")
    order = body.order if body.order is not None else _next_order(
        db, RoadmapTask, milestone_id=milestone.id
    )
    t = RoadmapTask(
        milestone_id=milestone.id, title=body.title, description=body.description,
        effort=body.effort or TaskEffort.medium, status=body.status or RoadmapStatus.todo,
        assignee_id=body.assignee_id, due_on=body.due_on, order=order,
    )
    db.add(t)
    db.flush()
    recompute_milestone_progress(db, milestone)
    db.commit()
    return success_response(_task_out(db, t))


@router.patch("/tasks/{task_id}")
def update_task_ep(
    task_id: uuid.UUID,
    body: TaskUpdate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    t = _task(db, membership, task_id)
    fields = body.model_dump(exclude_unset=True)
    if "assignee_id" in fields:
        _validate_member(db, membership, fields["assignee_id"], "assignee_id")
    for field, value in fields.items():
        setattr(t, field, value)
    db.flush()
    milestone = db.query(RoadmapMilestone).filter_by(id=t.milestone_id).one()
    recompute_milestone_progress(db, milestone)
    db.commit()
    return success_response(_task_out(db, t))


@router.delete("/tasks/{task_id}")
def delete_task_ep(
    task_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    t = _task(db, membership, task_id)
    milestone_id = t.milestone_id
    db.delete(t)
    db.flush()
    milestone = db.query(RoadmapMilestone).filter_by(id=milestone_id).one()
    recompute_milestone_progress(db, milestone)
    db.commit()
    return success_response({"deleted": True})
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/api/test_roadmap_tasks.py -v`
Expected: PASS.

- [ ] **Step 6: Run the whole unit suite + linters**

Run: `pytest -q && black --check . && isort --check . && ruff check . && mypy app`
Expected: all green (fix any drift before committing).

- [ ] **Step 7: Commit**

```bash
git add app/schemas/roadmap.py app/api/v1/endpoints/roadmap.py tests/api/test_roadmap_tasks.py
git commit -m "feat(roadmap): tasks CRUD + milestone progress recompute"
```

---

## Task 10: Live E2E roadmap journey + smoke surface

**Files:**
- Create: `e2e/test_roadmap.py`
- Modify: `e2e/test_smoke.py` (add the roadmap paths to the openapi assertion)
- Test: the e2e harness itself

**Interfaces:**
- Consumes: the running app + the e2e helpers in `e2e/conftest.py` (the `http` client, the signup→verify→login→onboard flow used by `e2e/test_onboarding.py`). Inspect `e2e/test_onboarding.py` and reuse its helpers verbatim.

- [ ] **Step 1: Add roadmap paths to the smoke assertion**

```python
# e2e/test_smoke.py  (add to the openapi path list)
        "/api/v1/roadmap",
        "/api/v1/roadmap/generate",
        "/api/v1/roadmap/phases",
        "/api/v1/roadmap/phases/{phase_id}",
        "/api/v1/roadmap/milestones",
        "/api/v1/roadmap/milestones/{milestone_id}",
        "/api/v1/roadmap/tasks",
        "/api/v1/roadmap/tasks/{task_id}",
```

- [ ] **Step 2: Write the E2E journey**

```python
# e2e/test_roadmap.py
# Reuse the onboarding journey helpers from e2e/test_onboarding.py (import or copy the
# signup->verify->login->complete-onboarding flow). Pseudocode of the assertions to make:
#
# 1. A founder signs up, verifies, logs in, completes onboarding at stage "validation".
# 2. GET /api/v1/roadmap -> 200; data.roadmap.template_key == "stage.validation";
#    at least one phase with milestones and tasks; current_stage == "validation".
# 3. POST a new phase, a milestone under it, a task under the milestone -> 201 each.
# 4. PATCH the task status -> "done"; GET roadmap -> that milestone's progress reflects it.
# 5. PATCH the milestone status -> "done" -> 200.
# 6. Invite + accept a team_member (reuse the onboarding invite E2E helper); the team_member
#    can PATCH a task (200).
# 7. Invite + accept a mentor; the mentor GET succeeds (200) but any write returns 403.
# 8. A second founder (fresh workspace) GET/PATCH against the first's phase id -> 404.
```

Write the real requests using the `http` client + header helpers exactly as `e2e/test_onboarding.py` does. Capture each response body to `e2e/_captures/roadmap/*.json` (create the dir) so the FE guide (Task 11) can paste them verbatim.

- [ ] **Step 3: Run the E2E suite**

Run: `make e2e`
Expected: the roadmap journey + smoke pass on a real server against an isolated DB migrated `0001→0006`.

- [ ] **Step 4: Commit**

```bash
git add e2e/test_roadmap.py e2e/test_smoke.py e2e/_captures/roadmap
git commit -m "test(roadmap): live E2E journey + smoke surface"
```

---

## Task 11: SOP + FE integration guide + checklist reconcile

**Files:**
- Create: `docs/sop/2026-08-21-roadmap-core.md`
- Create: `docs/fe-integration-guide-roadmap.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: Write the SOP**

Cover (per the SOP standard): what shipped (roadmap core, 11 endpoints, inline generation), why, how (create-once claim, derived progress, inline+lazy), what's involved (files/paths, migration `0006`, the onboarding wiring change), verification (unit + e2e counts, linters), operate/rollback (`alembic downgrade -1`), and follow-ups (Slice 2/3, overdue event needs a scheduler, workspace-tz base date).

- [ ] **Step 2: Write the FE integration guide from live captures**

Per the FE-guide standard: paste **verbatim** from `e2e/_captures/roadmap/*.json` — `GET /roadmap` (freshly generated + after edits), `POST /generate` 202+job, each CRUD response, the `422` owner/assignee error, the `403` mentor-write, the `404` cross-workspace. Document the derived `overdue`/`progress` (with a "don't hand-edit progress; it's computed from tasks" note), the `depends_on`/`dependency_count` always-empty-in-Slice-1 note, and a verification table marking each behaviour verified-live.

- [ ] **Step 3: Reconcile the checklist**

Tick Slice 1's items in `docs/checklist/PROJECT_CHECKLIST.md` and update the snapshot (a 6th module area in progress → Slice 1 done). Do not mark the Module 05 header fully shipped — Slices 2 and 3 remain.

- [ ] **Step 4: Final verification**

Run: `pytest -q && black --check . && isort --check . && ruff check . && mypy app && make e2e`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add docs/sop/2026-08-21-roadmap-core.md docs/fe-integration-guide-roadmap.md docs/checklist/PROJECT_CHECKLIST.md
git commit -m "docs(roadmap): SOP + FE integration guide + checklist reconcile"
```

---

## Self-Review notes (for the executor)

- **Spec coverage:** every §5 endpoint maps to Tasks 5–9; generation §4 → Task 4; wiring §4.3 → Tasks 5/6; events §6.1 → Tasks 4/8; errors §6.2 → Tasks 6–9; data model §3 → Tasks 1/2; testing §7 → Tasks 4–10; FE guide → Task 11.
- **Deferred, intentionally absent:** dependency *writes* + cycle detection, templates gallery (Slice 2); AI re-plan (Slice 3); `roadmap.milestone.overdue` push event + notifications (Module 20). `roadmap_task_dependencies` is created (Task 1/2) but never written; `depends_on`/`dependency_count` are constant `[]`/`0`.
- **Naming consistency:** `generate_roadmap`, `recompute_milestone_progress`, `milestone_overdue`, `task_overdue`, `person_ref`, `serialize_tree`, resolvers `_roadmap/_require_roadmap/_phase/_milestone/_task`, editor dep `_editor = require_role(founder, team_member)` — used identically across tasks.
- **Fixture names** (`db`, `db_engine`, `client`, `make_member_ctx`, `capture_events`, `http`) are the repo's existing ones — the executor must confirm each against `tests/conftest.py` / `e2e/conftest.py` and adjust to the real names before writing tests.

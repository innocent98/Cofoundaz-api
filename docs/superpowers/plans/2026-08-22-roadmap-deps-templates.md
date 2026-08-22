# Roadmap Dependencies + Templates (Module 05 · Slice 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add task dependencies (with write-time cycle detection + a graph read) and a curated template gallery (browse, preview, non-destructive apply) to the roadmap shipped in Slice 1.

**Architecture:** Two self-contained additions to the existing `app/services/roadmap/` + `app/api/v1/endpoints/roadmap.py`. Dependencies write the pre-existing (Slice-1) `roadmap_task_dependencies` table, gated by a DFS reachability check that keeps the graph a DAG. Templates are a new static versioned `GALLERY_TEMPLATES` catalog; apply appends a pack's phases/milestones/tasks (dates from the apply date) and dedupes via a new `roadmaps.applied_template_keys` JSONB column.

**Tech Stack:** Python 3.11 · FastAPI · SQLAlchemy 2.0 (typed `Mapped`) · Alembic · PostgreSQL · pytest (real Postgres, per-test rollback).

**Spec:** `docs/superpowers/specs/2026-08-22-roadmap-deps-templates-design.md`

## Global Constraints

- **Response envelope:** success via `success_response(data)` (`app.core.envelope`); errors via `AppError` subclasses / `AppError("CODE", msg, status, field_errors=[…])`.
- **No AI-attribution trailer** in any commit message or PR body (project rule).
- **Tenancy:** every task/edge/template resolves to the caller's roadmap (`roadmap.startup_id == membership.startup_id`); cross-workspace → `NotFound()` (uniform 404).
- **Access:** reads = any active member (`require_workspace`); writes = founder or team_member (`_editor = require_role(MembershipRole.founder, MembershipRole.team_member)`, already defined in `roadmap.py:44`); all endpoints require `get_verified_user`. Mentor read-only.
- **Enums / config-versioning:** `GALLERY_TEMPLATE_VERSION = 1`. Migration `0007_roadmap_applied_templates`, `down_revision = '0006_roadmap'`.
- **Dependency direction:** an edge `(task_id, depends_on_task_id)` reads "**task_id depends on depends_on_task_id**". Self-edge blocked by the table CHECK; also reject at the API with `422`.
- **Idempotency:** duplicate dependency edge → `200` (not error); re-applying a template → `200 {already_applied: true}` (no duplication).
- **API test auth:** the repo has NO `make_member_ctx`/`capture_events` fixtures. Use a local `_member(db, *, role, stage)` helper (mint verified user + startup + membership + Bearer/`X-Workspace-Id` headers) and monkeypatch `event_bus.publish` inline for event capture — see Slice-1 tests `tests/api/test_roadmap_*.py` and `tests/services/test_roadmap_generate.py::test_generate_emits_event` for the exact pattern. Confirm real fixture names against `tests/conftest.py` before writing tests.
- **Every implementer** runs `black`/`isort`/`ruff check`/`mypy app` on changes before reporting DONE.

---

## File Structure

- `app/db/models/roadmap.py` — add `Roadmap.applied_template_keys` column (modify).
- `alembic/versions/0007_roadmap_applied_templates.py` — migration (create).
- `app/services/roadmap/gallery.py` — `GALLERY_TEMPLATE_VERSION`, `GALLERY_TEMPLATES`, `template_counts` (create).
- `app/services/roadmap/dependencies.py` — `would_create_cycle`, `add_dependency`, `dependency_map` (create).
- `app/services/roadmap/service.py` — `apply_template`; populate `serialize_tree` `depends_on`/`dependency_count` from one fetch (modify).
- `app/core/errors.py` — add `DependencyCycle` (409) (modify).
- `app/schemas/roadmap.py` — `DependencyCreate` (modify).
- `app/api/v1/endpoints/roadmap.py` — dependency + template routes + resolvers (modify).
- `tests/factories.py` — `create_dependency` (modify).
- `e2e/test_roadmap.py` + `e2e/test_smoke.py` — extend (modify).
- `docs/sop/2026-08-22-roadmap-deps-templates.md`, `docs/fe-integration-guide-roadmap.md`, `docs/checklist/PROJECT_CHECKLIST.md` — docs.

---

## Task 1: `applied_template_keys` column + migration `0007`

**Files:**
- Modify: `app/db/models/roadmap.py`
- Create: `alembic/versions/0007_roadmap_applied_templates.py`
- Test: `tests/db/test_roadmap_applied_templates.py`

**Interfaces:**
- Produces: `Roadmap.applied_template_keys: Mapped[list[str]]` (JSONB, not null, default `[]`).

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_roadmap_applied_templates.py
from tests.factories import create_roadmap, create_startup, create_user


def test_applied_template_keys_defaults_empty(db):
    startup = create_startup(db, owner=create_user(db))
    r = create_roadmap(db, startup)
    db.refresh(r)
    assert r.applied_template_keys == []


def test_applied_template_keys_roundtrips(db):
    startup = create_startup(db, owner=create_user(db))
    r = create_roadmap(db, startup)
    r.applied_template_keys = ["mvp-build"]
    db.flush()
    db.refresh(r)
    assert r.applied_template_keys == ["mvp-build"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/db/test_roadmap_applied_templates.py -v`
Expected: FAIL (`AttributeError`/column missing).

- [ ] **Step 3: Add the column to the model**

In `app/db/models/roadmap.py`, add to `class Roadmap` (after `generated_at`), and ensure `JSONB` is imported (`from sqlalchemy.dialects.postgresql import JSONB`):

```python
    applied_template_keys: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
```

- [ ] **Step 4: Autogenerate + clean the migration**

Run: `poetry run alembic revision --autogenerate -m "roadmap applied templates" --rev-id 0007_roadmap_applied_templates`
Confirm `down_revision = '0006_roadmap'`. The upgrade should be a single `op.add_column("roadmaps", sa.Column("applied_template_keys", postgresql.JSONB(...), server_default="[]", nullable=False))`; `downgrade()` drops the column. Remove any unrelated autogen noise (server_default/CHECK diffs on other tables), matching `0004`/`0005`/`0006` discipline.

- [ ] **Step 5: Run migration up/down/up + tests**

Run: `poetry run alembic upgrade head && poetry run alembic downgrade -1 && poetry run alembic upgrade head && poetry run pytest tests/db/test_roadmap_applied_templates.py -v`
Expected: clean cycle; 2 tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/db/models/roadmap.py alembic/versions/0007_roadmap_applied_templates.py tests/db/test_roadmap_applied_templates.py
git commit -m "feat(roadmap): applied_template_keys column + migration 0007"
```

---

## Task 2: Gallery catalog

**Files:**
- Create: `app/services/roadmap/gallery.py`
- Test: `tests/services/test_roadmap_gallery.py`

**Interfaces:**
- Produces: `GALLERY_TEMPLATE_VERSION: int`; `GALLERY_TEMPLATES: dict[str, dict]` keyed by template id, each `{"id","title","stage"(str|None),"category","phases":[{"name","start_week","end_week","milestones":[{"title","due_week","tasks":[{"title","effort"}]}]}]}`; `template_counts(tmpl) -> tuple[int,int]` returning `(milestone_count, task_count)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_roadmap_gallery.py
from app.db.models.enums import StartupStage, TaskEffort
from app.services.roadmap.gallery import (
    GALLERY_TEMPLATE_VERSION,
    GALLERY_TEMPLATES,
    template_counts,
)


def test_gallery_is_well_formed():
    assert GALLERY_TEMPLATE_VERSION >= 1
    assert len(GALLERY_TEMPLATES) >= 6
    stages = {s.value for s in StartupStage}
    efforts = {e.value for e in TaskEffort}
    for tid, tmpl in GALLERY_TEMPLATES.items():
        assert tmpl["id"] == tid
        assert tmpl["title"] and tmpl["category"]
        assert tmpl["stage"] is None or tmpl["stage"] in stages
        assert tmpl["phases"]
        for ph in tmpl["phases"]:
            assert ph["end_week"] >= ph["start_week"]
            assert ph["milestones"]
            for ms in ph["milestones"]:
                assert ms["due_week"] >= 0
                for tk in ms["tasks"]:
                    assert tk["effort"] in efforts


def test_template_counts():
    mc, tc = template_counts(GALLERY_TEMPLATES["mvp-build"])
    assert mc >= 1 and tc >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/services/test_roadmap_gallery.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write the catalog**

Author six named packs matching the FE gallery (`validation-sprint`/Fintech, `mvp-build`/Fintech, `go-to-market`/B2C, `pre-seed-raise`/General stage None, `company-formation`/Nigeria stage None, `scale-playbook`/General). Example (author all six in this shape):

```python
# app/services/roadmap/gallery.py
GALLERY_TEMPLATE_VERSION = 1

GALLERY_TEMPLATES = {
    "validation-sprint": {"id": "validation-sprint", "title": "Validation sprint",
        "stage": "validation", "category": "Fintech", "phases": [
            {"name": "Validation sprint", "start_week": 0, "end_week": 4, "milestones": [
                {"title": "Problem interviews", "due_week": 1, "tasks": [
                    {"title": "Recruit 10 target users", "effort": "medium"},
                    {"title": "Run and synthesize interviews", "effort": "medium"}]},
                {"title": "Solution test", "due_week": 3, "tasks": [
                    {"title": "Build a concierge test", "effort": "large"}]}]}]},
    "mvp-build": {"id": "mvp-build", "title": "MVP build", "stage": "build",
        "category": "Fintech", "phases": [
            {"name": "MVP", "start_week": 0, "end_week": 8, "milestones": [
                {"title": "Core flow shipped", "due_week": 6, "tasks": [
                    {"title": "Build the core feature", "effort": "large"},
                    {"title": "Instrument analytics", "effort": "small"}]}]}]},
    "go-to-market": {"id": "go-to-market", "title": "Go-to-market", "stage": "launch",
        "category": "B2C", "phases": [
            {"name": "Go-to-market", "start_week": 0, "end_week": 6, "milestones": [
                {"title": "Launch plan", "due_week": 2, "tasks": [
                    {"title": "Define positioning + channels", "effort": "medium"},
                    {"title": "Prepare launch assets", "effort": "medium"}]}]}]},
    "pre-seed-raise": {"id": "pre-seed-raise", "title": "Pre-seed raise", "stage": None,
        "category": "General", "phases": [
            {"name": "Fundraise", "start_week": 0, "end_week": 10, "milestones": [
                {"title": "Deck + data room ready", "due_week": 3, "tasks": [
                    {"title": "Write the pitch deck", "effort": "large"},
                    {"title": "Assemble the data room", "effort": "medium"}]},
                {"title": "Investor outreach", "due_week": 8, "tasks": [
                    {"title": "Build the investor list", "effort": "medium"},
                    {"title": "Run outreach + meetings", "effort": "large"}]}]}]},
    "company-formation": {"id": "company-formation", "title": "Company formation", "stage": None,
        "category": "Nigeria", "phases": [
            {"name": "Incorporation", "start_week": 0, "end_week": 4, "milestones": [
                {"title": "Register the company", "due_week": 2, "tasks": [
                    {"title": "Reserve the company name (CAC)", "effort": "small"},
                    {"title": "File incorporation documents", "effort": "medium"}]},
                {"title": "Tax + banking set up", "due_week": 4, "tasks": [
                    {"title": "Obtain TIN", "effort": "small"},
                    {"title": "Open a corporate bank account", "effort": "medium"}]}]}]},
    "scale-playbook": {"id": "scale-playbook", "title": "Scale playbook", "stage": "scale",
        "category": "General", "phases": [
            {"name": "Scale", "start_week": 0, "end_week": 12, "milestones": [
                {"title": "Repeatable growth engine", "due_week": 8, "tasks": [
                    {"title": "Document the growth motion", "effort": "medium"},
                    {"title": "Hire against the plan", "effort": "large"}]}]}]},
}


def template_counts(tmpl: dict) -> tuple[int, int]:
    milestones = [m for ph in tmpl["phases"] for m in ph["milestones"]]
    tasks = [t for m in milestones for t in m["tasks"]]
    return len(milestones), len(tasks)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/services/test_roadmap_gallery.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/roadmap/gallery.py tests/services/test_roadmap_gallery.py
git commit -m "feat(roadmap): gallery template catalog (v1)"
```

---

## Task 3: Cycle detection + dependency service + error

**Files:**
- Create: `app/services/roadmap/dependencies.py`
- Modify: `app/core/errors.py`
- Modify: `tests/factories.py` (add `create_dependency`)
- Test: `tests/services/test_roadmap_dependencies.py`

**Interfaces:**
- Consumes: models `RoadmapTask`, `RoadmapMilestone`, `RoadmapPhase`, `RoadmapTaskDependency`.
- Produces:
  - `DependencyCycle(AppError)` with `code="DEPENDENCY_CYCLE"`, `http_status=409`.
  - `would_create_cycle(db, roadmap_id, task_id, depends_on_task_id) -> bool`.
  - `add_dependency(db, task_id, depends_on_task_id) -> tuple[RoadmapTaskDependency, bool]` — returns `(row, created)`; `created=False` if the edge already existed (idempotent).
  - `dependency_map(db, roadmap_id) -> dict[uuid.UUID, list[uuid.UUID]]` — `{dependent_task_id: [dependency_task_id, …]}` for all edges in the roadmap.
  - Factory `create_dependency(db, dependent, depends_on) -> RoadmapTaskDependency`.

- [ ] **Step 1: Add the error class**

In `app/core/errors.py`, mirroring the existing 409 subclass `RecommendationResolved`:

```python
class DependencyCycle(AppError):  # noqa: N818
    code = "DEPENDENCY_CYCLE"
    http_status = 409
```

(Confirm the exact base-class attribute names by reading `RecommendationResolved`/`NotFound` in that file; match them.)

- [ ] **Step 2: Write the failing tests**

```python
# tests/services/test_roadmap_dependencies.py
import pytest

from app.services.roadmap.dependencies import (
    add_dependency,
    dependency_map,
    would_create_cycle,
)
from tests.factories import (
    create_milestone, create_phase, create_roadmap, create_startup, create_task, create_user,
)


def _roadmap_with_tasks(db, n=4):
    startup = create_startup(db, owner=create_user(db))
    roadmap = create_roadmap(db, startup)
    phase = create_phase(db, roadmap)
    ms = create_milestone(db, phase)
    tasks = [create_task(db, ms, title=f"T{i}") for i in range(n)]
    return roadmap, tasks


def test_direct_cycle_detected(db):
    roadmap, t = _roadmap_with_tasks(db)
    add_dependency(db, t[0].id, t[1].id)  # T0 depends on T1
    db.flush()
    # adding T1 depends on T0 would loop
    assert would_create_cycle(db, roadmap.id, t[1].id, t[0].id) is True


def test_transitive_cycle_detected(db):
    roadmap, t = _roadmap_with_tasks(db)
    add_dependency(db, t[0].id, t[1].id)  # T0 -> T1
    add_dependency(db, t[1].id, t[2].id)  # T1 -> T2
    db.flush()
    assert would_create_cycle(db, roadmap.id, t[2].id, t[0].id) is True  # T2 -> T0 closes loop


def test_valid_dag_no_cycle(db):
    roadmap, t = _roadmap_with_tasks(db)
    add_dependency(db, t[1].id, t[0].id)  # diamond: B->A
    add_dependency(db, t[2].id, t[0].id)  # C->A
    db.flush()
    assert would_create_cycle(db, roadmap.id, t[3].id, t[1].id) is False  # D->B ok
    assert would_create_cycle(db, roadmap.id, t[3].id, t[2].id) is False  # D->C ok


def test_add_dependency_idempotent(db):
    roadmap, t = _roadmap_with_tasks(db)
    row1, created1 = add_dependency(db, t[0].id, t[1].id)
    row2, created2 = add_dependency(db, t[0].id, t[1].id)
    db.flush()
    assert created1 is True and created2 is False


def test_dependency_map(db):
    roadmap, t = _roadmap_with_tasks(db)
    add_dependency(db, t[0].id, t[1].id)
    add_dependency(db, t[0].id, t[2].id)
    db.flush()
    m = dependency_map(db, roadmap.id)
    assert set(m[t[0].id]) == {t[1].id, t[2].id}
```

- [ ] **Step 3: Run to verify they fail**

Run: `poetry run pytest tests/services/test_roadmap_dependencies.py -v`
Expected: FAIL (`ModuleNotFoundError` / factory missing).

- [ ] **Step 4: Add the factory**

```python
# tests/factories.py  (add import + function)
from app.db.models.roadmap import RoadmapTaskDependency


def create_dependency(db, dependent, depends_on) -> RoadmapTaskDependency:
    d = RoadmapTaskDependency(task_id=dependent.id, depends_on_task_id=depends_on.id)
    db.add(d)
    db.flush()
    return d
```

- [ ] **Step 5: Implement the service**

```python
# app/services/roadmap/dependencies.py
from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy.orm import Session

from app.db.models.roadmap import (
    RoadmapMilestone,
    RoadmapPhase,
    RoadmapTask,
    RoadmapTaskDependency,
)


def _roadmap_edges(db: Session, roadmap_id: uuid.UUID) -> list[RoadmapTaskDependency]:
    # edges whose dependent task belongs to this roadmap (both ends are in the
    # same roadmap by construction, since creation is roadmap-scoped)
    return (
        db.query(RoadmapTaskDependency)
        .join(RoadmapTask, RoadmapTaskDependency.task_id == RoadmapTask.id)
        .join(RoadmapMilestone, RoadmapTask.milestone_id == RoadmapMilestone.id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(RoadmapPhase.roadmap_id == roadmap_id)
        .all()
    )


def dependency_map(db: Session, roadmap_id: uuid.UUID) -> dict[uuid.UUID, list[uuid.UUID]]:
    out: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for e in _roadmap_edges(db, roadmap_id):
        out[e.task_id].append(e.depends_on_task_id)
    return dict(out)


def would_create_cycle(
    db: Session, roadmap_id: uuid.UUID, task_id: uuid.UUID, depends_on_task_id: uuid.UUID
) -> bool:
    # edge task_id -> depends_on_task_id ("task depends on depends_on").
    # A loop forms iff depends_on_task_id can already reach task_id following
    # existing dependent->dependency edges.
    adj = dependency_map(db, roadmap_id)
    stack = [depends_on_task_id]
    seen: set[uuid.UUID] = set()
    while stack:
        node = stack.pop()
        if node == task_id:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(adj.get(node, []))
    return False


def add_dependency(
    db: Session, task_id: uuid.UUID, depends_on_task_id: uuid.UUID
) -> tuple[RoadmapTaskDependency, bool]:
    existing = (
        db.query(RoadmapTaskDependency)
        .filter_by(task_id=task_id, depends_on_task_id=depends_on_task_id)
        .first()
    )
    if existing is not None:
        return existing, False
    row = RoadmapTaskDependency(task_id=task_id, depends_on_task_id=depends_on_task_id)
    db.add(row)
    db.flush()
    return row, True
```

- [ ] **Step 6: Run to verify they pass**

Run: `poetry run pytest tests/services/test_roadmap_dependencies.py -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

```bash
git add app/services/roadmap/dependencies.py app/core/errors.py tests/factories.py tests/services/test_roadmap_dependencies.py
git commit -m "feat(roadmap): dependency service + cycle detection + DEPENDENCY_CYCLE error"
```

---

## Task 4: Dependency endpoints (`POST`/`DELETE`)

**Files:**
- Modify: `app/schemas/roadmap.py`
- Modify: `app/api/v1/endpoints/roadmap.py`
- Test: `tests/api/test_roadmap_dependencies_api.py`

**Interfaces:**
- Consumes: `_task`, `_require_roadmap`, `_editor` (roadmap.py); `would_create_cycle`, `add_dependency`, `DependencyCycle` (Task 3).
- Produces: `DependencyCreate` schema; routes `POST /roadmap/tasks/{task_id}/dependencies`, `DELETE /roadmap/tasks/{task_id}/dependencies/{depends_on_task_id}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_roadmap_dependencies_api.py
from app.db.models.enums import MembershipRole, StartupStage


def _two_tasks(client, ctx):
    data = client.get("/api/v1/roadmap", headers=ctx.headers).json()["data"]
    ms = data["phases"][0]["milestones"][0]["id"]
    a = client.post("/api/v1/roadmap/tasks", headers=ctx.headers,
                    json={"milestone_id": ms, "title": "A"}).json()["data"]["id"]
    b = client.post("/api/v1/roadmap/tasks", headers=ctx.headers,
                    json={"milestone_id": ms, "title": "B"}).json()["data"]["id"]
    return a, b


def test_create_dependency_then_cycle_rejected(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    a, b = _two_tasks(client, ctx)
    # A depends on B -> 201
    r = client.post(f"/api/v1/roadmap/tasks/{a}/dependencies", headers=ctx.headers,
                    json={"depends_on_task_id": b})
    assert r.status_code == 201
    # duplicate -> idempotent 200
    r2 = client.post(f"/api/v1/roadmap/tasks/{a}/dependencies", headers=ctx.headers,
                     json={"depends_on_task_id": b})
    assert r2.status_code == 200
    # B depends on A -> cycle 409
    r3 = client.post(f"/api/v1/roadmap/tasks/{b}/dependencies", headers=ctx.headers,
                     json={"depends_on_task_id": a})
    assert r3.status_code == 409
    assert r3.json()["error"]["code"] == "DEPENDENCY_CYCLE"


def test_self_dependency_422(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    a, _ = _two_tasks(client, ctx)
    r = client.post(f"/api/v1/roadmap/tasks/{a}/dependencies", headers=ctx.headers,
                    json={"depends_on_task_id": a})
    assert r.status_code == 422


def test_delete_dependency(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    a, b = _two_tasks(client, ctx)
    client.post(f"/api/v1/roadmap/tasks/{a}/dependencies", headers=ctx.headers,
                json={"depends_on_task_id": b})
    r = client.delete(f"/api/v1/roadmap/tasks/{a}/dependencies/{b}", headers=ctx.headers)
    assert r.status_code == 200
    # deleting a missing edge -> 404
    assert client.delete(f"/api/v1/roadmap/tasks/{a}/dependencies/{b}",
                         headers=ctx.headers).status_code == 404


def test_dependency_write_forbidden_for_mentor(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.mentor, stage=StartupStage.idea)
    # mentor cannot create tasks either; use a bogus id — the 403 must come from the role gate
    import uuid
    r = client.post(f"/api/v1/roadmap/tasks/{uuid.uuid4()}/dependencies", headers=ctx.headers,
                    json={"depends_on_task_id": str(uuid.uuid4())})
    assert r.status_code == 403


def test_dependency_cross_workspace_404(client, db, make_member_ctx):
    a_ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    b_ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    a, b = _two_tasks(client, a_ctx)
    r = client.post(f"/api/v1/roadmap/tasks/{a}/dependencies", headers=b_ctx.headers,
                    json={"depends_on_task_id": b})
    assert r.status_code == 404
```

Adapt `make_member_ctx` to the repo's real `_member` helper (see Global Constraints).

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/api/test_roadmap_dependencies_api.py -v`
Expected: FAIL (routes missing).

- [ ] **Step 3: Add the schema**

```python
# app/schemas/roadmap.py  (append)
class DependencyCreate(BaseModel):
    depends_on_task_id: uuid.UUID
```

- [ ] **Step 4: Add the routes**

```python
# app/api/v1/endpoints/roadmap.py  (add imports + routes)
from app.core.errors import AppError, DependencyCycle
from app.db.models.roadmap import RoadmapTaskDependency
from app.schemas.roadmap import DependencyCreate
from app.services.roadmap.dependencies import add_dependency, would_create_cycle


@router.post("/tasks/{task_id}/dependencies", status_code=201)
def create_dependency_ep(
    task_id: uuid.UUID,
    body: DependencyCreate,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    dependent = _task(db, membership, task_id)  # 404 if foreign
    if body.depends_on_task_id == dependent.id:
        raise AppError("VALIDATION_ERROR", "A task cannot depend on itself.", 422,
                       field_errors=[{"field": "depends_on_task_id",
                                      "message": "A task cannot depend on itself."}])
    dependency = _task(db, membership, body.depends_on_task_id)  # 404 if foreign
    roadmap = _require_roadmap(db, membership)
    if would_create_cycle(db, roadmap.id, dependent.id, dependency.id):
        raise DependencyCycle(
            f"That would create a loop — {dependency.title} already depends on {dependent.title}."
        )
    _row, created = add_dependency(db, dependent.id, dependency.id)
    db.commit()
    payload = {"task_id": str(dependent.id), "depends_on_task_id": str(dependency.id)}
    return JSONResponse(status_code=(201 if created else 200),
                        content=success_response(payload))


@router.delete("/tasks/{task_id}/dependencies/{depends_on_task_id}")
def delete_dependency_ep(
    task_id: uuid.UUID,
    depends_on_task_id: uuid.UUID,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    dependent = _task(db, membership, task_id)  # 404 if foreign
    edge = (
        db.query(RoadmapTaskDependency)
        .filter_by(task_id=dependent.id, depends_on_task_id=depends_on_task_id)
        .first()
    )
    if edge is None:
        raise NotFound()
    db.delete(edge)
    db.commit()
    return success_response({"deleted": True})
```

Notes: `DependencyCycle(message)` — confirm the `AppError.__init__` signature (Slice 1 used `AppError("CODE", msg, status, field_errors=...)`; subclasses set `code`/`http_status` and take the message positionally — match the real signature). `JSONResponse` is `from fastapi.responses import JSONResponse` (add the import) — used only to vary 201-vs-200 on one route; if the file already returns bare dicts with a fixed `status_code=` decorator, instead split into the created path returning the decorator's 201 and raise nothing for the idempotent case by returning `success_response(...)` with a `Response`-set status. Prefer the `JSONResponse` approach for clarity.

- [ ] **Step 5: Run to verify they pass**

Run: `poetry run pytest tests/api/test_roadmap_dependencies_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/schemas/roadmap.py app/api/v1/endpoints/roadmap.py tests/api/test_roadmap_dependencies_api.py
git commit -m "feat(roadmap): task dependency create/delete endpoints"
```

---

## Task 5: `GET /roadmap/dependencies` graph + populate tree seams

**Files:**
- Modify: `app/services/roadmap/service.py` (`serialize_tree`)
- Modify: `app/api/v1/endpoints/roadmap.py` (graph route)
- Test: `tests/api/test_roadmap_dependency_graph.py`

**Interfaces:**
- Consumes: `dependency_map` (Task 3).
- Produces: `GET /roadmap/dependencies`; `serialize_tree` now fills `depends_on` per task and `dependency_count` per milestone from a single `dependency_map` fetch.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_roadmap_dependency_graph.py
from app.db.models.enums import MembershipRole, StartupStage


def _two_tasks(client, ctx):
    data = client.get("/api/v1/roadmap", headers=ctx.headers).json()["data"]
    ms = data["phases"][0]["milestones"][0]["id"]
    a = client.post("/api/v1/roadmap/tasks", headers=ctx.headers,
                    json={"milestone_id": ms, "title": "A"}).json()["data"]["id"]
    b = client.post("/api/v1/roadmap/tasks", headers=ctx.headers,
                    json={"milestone_id": ms, "title": "B"}).json()["data"]["id"]
    return ms, a, b


def test_graph_and_tree_reflect_edge(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    ms, a, b = _two_tasks(client, ctx)
    client.post(f"/api/v1/roadmap/tasks/{a}/dependencies", headers=ctx.headers,
                json={"depends_on_task_id": b})

    g = client.get("/api/v1/roadmap/dependencies", headers=ctx.headers)
    assert g.status_code == 200
    gd = g.json()["data"]
    assert {"task_id": a, "depends_on_task_id": b} in gd["edges"]
    assert any(n["task_id"] == a for n in gd["nodes"])

    tree = client.get("/api/v1/roadmap", headers=ctx.headers).json()["data"]
    task_a = next(t for p in tree["phases"] for m in p["milestones"]
                  for t in m["tasks"] if t["id"] == a)
    assert b in task_a["depends_on"]
    m_obj = next(m for p in tree["phases"] for m in p["milestones"] if m["id"] == ms)
    assert m_obj["dependency_count"] >= 1


def test_graph_requires_auth(client):
    assert client.get("/api/v1/roadmap/dependencies").status_code == 401
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/api/test_roadmap_dependency_graph.py -v`
Expected: FAIL (route missing / `depends_on` empty).

- [ ] **Step 3: Populate the tree seams**

In `app/services/roadmap/service.py::serialize_tree`, fetch the dependency map once at the top and use it. Replace the per-milestone `dep_count` query and the hardcoded `"depends_on": []`:

```python
    from app.services.roadmap.dependencies import dependency_map
    dep_map = dependency_map(db, roadmap.id)   # {dependent_task_id: [dependency_id, ...]}
```
- milestone `dependency_count`: `sum(1 for t in tasks if t.id in dep_map)` (count of that milestone's tasks that have ≥1 dependency).
- task `depends_on`: `[str(d) for d in dep_map.get(t.id, [])]`.

Remove the old `db.query(RoadmapTaskDependency)…count()` block.

- [ ] **Step 4: Add the graph route**

```python
# app/api/v1/endpoints/roadmap.py
from app.services.roadmap.dependencies import dependency_map
from app.db.models.roadmap import RoadmapMilestone, RoadmapPhase, RoadmapTask


@router.get("/dependencies")
def get_dependencies(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    roadmap = _require_roadmap(db, membership)
    rows = (
        db.query(RoadmapTask.id, RoadmapTask.title, RoadmapMilestone.id, RoadmapMilestone.title,
                 RoadmapPhase.id, RoadmapPhase.name)
        .join(RoadmapMilestone, RoadmapTask.milestone_id == RoadmapMilestone.id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(RoadmapPhase.roadmap_id == roadmap.id)
        .all()
    )
    title_by_id = {r[0]: r[1] for r in rows}
    nodes = [{"task_id": str(r[0]), "title": r[1], "milestone_id": str(r[2]),
              "milestone_title": r[3], "phase_id": str(r[4]), "phase_name": r[5]} for r in rows]
    dep_map = dependency_map(db, roadmap.id)
    edges, listing = [], []
    for dependent, deps in dep_map.items():
        for dep in deps:
            edges.append({"task_id": str(dependent), "depends_on_task_id": str(dep)})
            listing.append({"task": title_by_id.get(dependent), "depends_on": title_by_id.get(dep)})
    return success_response({"nodes": nodes, "edges": edges, "list": listing})
```

`GET /dependencies` must be registered BEFORE any `GET /{something}` catch-all if one exists — there is none in this router (the tree is `GET ""`), so ordering is fine; keep it near the other GETs.

- [ ] **Step 5: Run to verify they pass**

Run: `poetry run pytest tests/api/test_roadmap_dependency_graph.py tests/api/test_roadmap_get.py -v`
Expected: PASS (new graph tests + the Slice-1 tree tests still green).

- [ ] **Step 6: Commit**

```bash
git add app/services/roadmap/service.py app/api/v1/endpoints/roadmap.py tests/api/test_roadmap_dependency_graph.py
git commit -m "feat(roadmap): GET /dependencies graph + populate tree depends_on/dependency_count"
```

---

## Task 6: Template gallery + preview endpoints

**Files:**
- Modify: `app/api/v1/endpoints/roadmap.py`
- Test: `tests/api/test_roadmap_templates_gallery.py`

**Interfaces:**
- Consumes: `GALLERY_TEMPLATES`, `template_counts` (Task 2); `Roadmap.applied_template_keys` (Task 1).
- Produces: `GET /roadmap/templates`, `GET /roadmap/templates/{template_id}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_roadmap_templates_gallery.py
from app.db.models.enums import MembershipRole, StartupStage


def test_gallery_lists_templates(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    client.get("/api/v1/roadmap", headers=ctx.headers)  # ensure roadmap exists
    r = client.get("/api/v1/roadmap/templates", headers=ctx.headers)
    assert r.status_code == 200
    items = r.json()["data"]
    assert len(items) >= 6
    one = next(i for i in items if i["id"] == "mvp-build")
    assert one["title"] == "MVP build"
    assert one["category"] == "Fintech"
    assert one["milestone_count"] >= 1 and one["task_count"] >= 1
    assert one["applied"] is False


def test_template_preview(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    r = client.get("/api/v1/roadmap/templates/mvp-build", headers=ctx.headers)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["id"] == "mvp-build"
    assert d["phases"] and d["phases"][0]["milestones"][0]["tasks"]


def test_template_preview_unknown_404(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    assert client.get("/api/v1/roadmap/templates/nope", headers=ctx.headers).status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/api/test_roadmap_templates_gallery.py -v`
Expected: FAIL (routes missing).

- [ ] **Step 3: Add the routes**

```python
# app/api/v1/endpoints/roadmap.py
from app.services.roadmap.gallery import GALLERY_TEMPLATES, template_counts


@router.get("/templates")
def list_templates(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    roadmap = _roadmap(db, membership)
    applied = set(roadmap.applied_template_keys) if roadmap else set()
    items = []
    for tid, tmpl in GALLERY_TEMPLATES.items():
        mc, tc = template_counts(tmpl)
        items.append({"id": tid, "title": tmpl["title"], "stage": tmpl["stage"],
                      "category": tmpl["category"], "milestone_count": mc, "task_count": tc,
                      "applied": tid in applied})
    return success_response(items)


@router.get("/templates/{template_id}")
def preview_template(
    template_id: str,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    tmpl = GALLERY_TEMPLATES.get(template_id)
    if tmpl is None:
        raise NotFound()
    mc, tc = template_counts(tmpl)
    phases = [{"name": ph["name"], "milestones": [
        {"title": ms["title"], "tasks": [{"title": tk["title"], "effort": tk["effort"]}
                                          for tk in ms["tasks"]]}
        for ms in ph["milestones"]]} for ph in tmpl["phases"]]
    return success_response({"id": tmpl["id"], "title": tmpl["title"], "stage": tmpl["stage"],
                             "category": tmpl["category"], "milestone_count": mc,
                             "task_count": tc, "phases": phases})
```

Register `GET /templates/{template_id}` — a string path param, distinct from the uuid task/milestone routes, so no collision.

- [ ] **Step 4: Run to verify they pass**

Run: `poetry run pytest tests/api/test_roadmap_templates_gallery.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/endpoints/roadmap.py tests/api/test_roadmap_templates_gallery.py
git commit -m "feat(roadmap): template gallery list + preview endpoints"
```

---

## Task 7: `POST /roadmap/templates/{id}/apply`

**Files:**
- Modify: `app/services/roadmap/service.py` (`apply_template`)
- Modify: `app/api/v1/endpoints/roadmap.py` (apply route)
- Test: `tests/services/test_roadmap_apply_template.py`, `tests/api/test_roadmap_apply_api.py`

**Interfaces:**
- Consumes: `GALLERY_TEMPLATES`, `template_counts`; `generate_roadmap` (Slice 1, for lazy-ensure); `event_bus`.
- Produces: `apply_template(db, roadmap, template) -> dict` returning `{"phases": int, "milestones": int, "tasks": int}` added; route `POST /roadmap/templates/{template_id}/apply`.

- [ ] **Step 1: Write the failing service test**

```python
# tests/services/test_roadmap_apply_template.py
from app.db.models.roadmap import RoadmapPhase, RoadmapTask
from app.services.roadmap.gallery import GALLERY_TEMPLATES, template_counts
from app.services.roadmap.service import apply_template
from tests.factories import create_roadmap, create_startup, create_user


def test_apply_appends_pack(db):
    startup = create_startup(db, owner=create_user(db))
    roadmap = create_roadmap(db, startup)
    before = db.query(RoadmapPhase).filter_by(roadmap_id=roadmap.id).count()
    tmpl = GALLERY_TEMPLATES["mvp-build"]
    added = apply_template(db, roadmap, tmpl)
    db.flush()
    mc, tc = template_counts(tmpl)
    assert added == {"phases": len(tmpl["phases"]), "milestones": mc, "tasks": tc}
    assert db.query(RoadmapPhase).filter_by(roadmap_id=roadmap.id).count() == before + len(tmpl["phases"])
    assert "mvp-build" in roadmap.applied_template_keys
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/services/test_roadmap_apply_template.py -v`
Expected: FAIL (`apply_template` undefined).

- [ ] **Step 3: Implement `apply_template`**

```python
# app/services/roadmap/service.py  (append)
def apply_template(db: Session, roadmap: Roadmap, tmpl: dict) -> dict:
    base = date.today()
    start_order = _max_phase_order(db, roadmap.id) + 1
    phases = milestones = tasks = 0
    for p_idx, ph in enumerate(tmpl["phases"]):
        phase = RoadmapPhase(
            roadmap_id=roadmap.id, name=ph["name"], order=start_order + p_idx,
            starts_on=base + timedelta(weeks=ph["start_week"]),
            ends_on=base + timedelta(weeks=ph["end_week"]),
        )
        db.add(phase)
        db.flush()
        phases += 1
        for m_idx, ms in enumerate(ph["milestones"]):
            milestone = RoadmapMilestone(
                phase_id=phase.id, title=ms["title"],
                due_on=base + timedelta(weeks=ms["due_week"]),
                status=RoadmapStatus.todo, progress=0, order=m_idx,
            )
            db.add(milestone)
            db.flush()
            milestones += 1
            for t_idx, tk in enumerate(ms["tasks"]):
                db.add(RoadmapTask(
                    milestone_id=milestone.id, title=tk["title"],
                    effort=TaskEffort(tk["effort"]), status=RoadmapStatus.todo, order=t_idx,
                ))
                tasks += 1
    roadmap.applied_template_keys = [*roadmap.applied_template_keys, tmpl["id"]]
    db.flush()
    return {"phases": phases, "milestones": milestones, "tasks": tasks}


def _max_phase_order(db: Session, roadmap_id: uuid.UUID) -> int:
    from sqlalchemy import func as _func
    val = db.query(_func.max(RoadmapPhase.order)).filter_by(roadmap_id=roadmap_id).scalar()
    return -1 if val is None else val
```

(Reuse `date`, `timedelta`, `RoadmapStatus`, `TaskEffort`, `RoadmapPhase/Milestone/Task` already imported in the module; add `timedelta` if missing.)

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/services/test_roadmap_apply_template.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing API test**

```python
# tests/api/test_roadmap_apply_api.py
from app.db.models.enums import MembershipRole, StartupStage


def _tree_counts(client, ctx):
    d = client.get("/api/v1/roadmap", headers=ctx.headers).json()["data"]
    ph = len(d["phases"])
    ms = sum(len(p["milestones"]) for p in d["phases"])
    return ph, ms


def test_apply_then_reapply_is_noop(client, db, make_member_ctx, monkeypatch):
    events = []
    from app.platform import events as ev
    monkeypatch.setattr(ev.event_bus, "publish", lambda e, p: events.append((e, p)))

    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    ph0, ms0 = _tree_counts(client, ctx)

    r = client.post("/api/v1/roadmap/templates/mvp-build/apply", headers=ctx.headers)
    assert r.status_code == 201
    assert r.json()["data"]["already_applied"] is False
    assert any(e == "roadmap.template.applied" for e, _ in events)
    ph1, ms1 = _tree_counts(client, ctx)
    assert ph1 > ph0

    events.clear()
    r2 = client.post("/api/v1/roadmap/templates/mvp-build/apply", headers=ctx.headers)
    assert r2.status_code == 200
    assert r2.json()["data"]["already_applied"] is True
    assert not any(e == "roadmap.template.applied" for e, _ in events)
    ph2, ms2 = _tree_counts(client, ctx)
    assert (ph2, ms2) == (ph1, ms1)  # no duplication

    # gallery now marks it applied
    items = client.get("/api/v1/roadmap/templates", headers=ctx.headers).json()["data"]
    assert next(i for i in items if i["id"] == "mvp-build")["applied"] is True


def test_apply_unknown_404(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.idea)
    assert client.post("/api/v1/roadmap/templates/nope/apply",
                       headers=ctx.headers).status_code == 404


def test_apply_forbidden_for_mentor(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.mentor, stage=StartupStage.idea)
    assert client.post("/api/v1/roadmap/templates/mvp-build/apply",
                       headers=ctx.headers).status_code == 403
```

- [ ] **Step 6: Run to verify it fails**

Run: `poetry run pytest tests/api/test_roadmap_apply_api.py -v`
Expected: FAIL (route missing).

- [ ] **Step 7: Add the apply route**

```python
# app/api/v1/endpoints/roadmap.py
from app.services.roadmap.service import apply_template, generate_roadmap
from app.platform.events import event_bus


@router.post("/templates/{template_id}/apply")
def apply_template_ep(
    template_id: str,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    tmpl = GALLERY_TEMPLATES.get(template_id)
    if tmpl is None:
        raise NotFound()
    startup = _startup(db, membership)
    roadmap = _roadmap(db, membership) or generate_roadmap(db, startup, actor=user)
    if template_id in roadmap.applied_template_keys:
        db.commit()
        return success_response({"already_applied": True,
                                 "added": {"phases": 0, "milestones": 0, "tasks": 0}})
    added = apply_template(db, roadmap, tmpl)
    event_bus.publish("roadmap.template.applied", {
        "startup_id": str(membership.startup_id), "roadmap_id": str(roadmap.id),
        "template_id": template_id, "added": added})
    db.commit()
    return JSONResponse(status_code=201,
                        content=success_response({"already_applied": False, "added": added}))
```

- [ ] **Step 8: Run to verify it passes + full suite**

Run: `poetry run pytest tests/api/test_roadmap_apply_api.py -v && poetry run pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 9: Lint + commit**

Run: `poetry run black --check . && poetry run isort --check . && poetry run ruff check . && poetry run mypy app`

```bash
git add app/services/roadmap/service.py app/api/v1/endpoints/roadmap.py tests/services/test_roadmap_apply_template.py tests/api/test_roadmap_apply_api.py
git commit -m "feat(roadmap): POST /templates/{id}/apply (append + dedup + event)"
```

---

## Task 8: Live E2E extension + smoke surface

**Files:**
- Modify: `e2e/test_roadmap.py`
- Modify: `e2e/test_smoke.py`
- Create: capture dir `e2e/_captures/roadmap/` additions

**Interfaces:**
- Consumes: the running app + `e2e/conftest.py` helpers (reuse the Slice-1 roadmap journey scaffolding in `e2e/test_roadmap.py`).

- [ ] **Step 1: Add the new paths to smoke**

```python
# e2e/test_smoke.py  (add to the openapi path list)
        "/api/v1/roadmap/dependencies",
        "/api/v1/roadmap/tasks/{task_id}/dependencies",
        "/api/v1/roadmap/tasks/{task_id}/dependencies/{depends_on_task_id}",
        "/api/v1/roadmap/templates",
        "/api/v1/roadmap/templates/{template_id}",
        "/api/v1/roadmap/templates/{template_id}/apply",
```

- [ ] **Step 2: Extend the E2E journey**

Extend `e2e/test_roadmap.py` (reuse its existing founder+roadmap setup). Add, capturing each response body to `e2e/_captures/roadmap/`:
- create two tasks; `POST .../{a}/dependencies {depends_on_task_id: b}` → 201 (`dependency_create.json`);
- attempt the reverse edge → 409 with `DEPENDENCY_CYCLE` (`dependency_cycle.json`);
- `GET /roadmap/dependencies` → nodes/edges/list (`dependencies_graph.json`); and re-`GET /roadmap` showing `depends_on` populated (`get_tree_with_deps.json`);
- `GET /roadmap/templates` → gallery (`templates_list.json`); `GET /roadmap/templates/mvp-build` → preview (`template_preview.json`);
- `POST /roadmap/templates/mvp-build/apply` → 201 added counts (`template_apply.json`); re-apply → 200 `already_applied:true` (`template_apply_noop.json`).

Write the real requests mirroring the Slice-1 journey's client/header helpers.

- [ ] **Step 3: Run the E2E suite**

Run: `make e2e`
Expected: green on a real server, DB migrated `0001→0007`.

- [ ] **Step 4: Commit**

```bash
git add e2e/test_roadmap.py e2e/test_smoke.py e2e/_captures/roadmap
git commit -m "test(roadmap): live E2E for dependencies + templates + smoke surface"
```

---

## Task 9: SOP + FE integration guide + checklist reconcile

**Files:**
- Create: `docs/sop/2026-08-22-roadmap-deps-templates.md`
- Modify: `docs/fe-integration-guide-roadmap.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: Write the SOP**

Cover what shipped (dependencies + cycle detection + graph; template gallery + preview + apply/dedup; migration `0007`), why, how (DFS reachability keeps a DAG; append+dedup via `applied_template_keys`; single-fetch tree population), files/paths, verification (test counts + `make e2e`), rollback (`alembic downgrade -1`), and follow-ups (Slice 3; dependency-aware scheduling; `roadmap.template.applied` has no consumer until Module 20).

- [ ] **Step 2: Update the FE guide from live captures**

Append sections to `docs/fe-integration-guide-roadmap.md` from `e2e/_captures/roadmap/*.json` (verbatim): the dependency create/delete (incl. the `409 DEPENDENCY_CYCLE` body + exact message), the `GET /dependencies` graph shape, the now-populated `depends_on`/`dependency_count` in the tree, the gallery list (+ `applied` flag) and preview, and the apply response (fresh `201` + `already_applied:true` `200`). Update the verification table.

- [ ] **Step 3: Reconcile the checklist**

Tick Slice 2's items in `docs/checklist/PROJECT_CHECKLIST.md`; mark Slice 2 done within Module 05 (leave the Module 05 header in-progress — Slice 3 remains). Update the snapshot counts.

- [ ] **Step 4: Final verification**

Run: `poetry run pytest -q && poetry run black --check . && poetry run isort --check . && poetry run ruff check . && poetry run mypy app && make e2e`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add docs/sop/2026-08-22-roadmap-deps-templates.md docs/fe-integration-guide-roadmap.md docs/checklist/PROJECT_CHECKLIST.md
git commit -m "docs(roadmap): Slice 2 SOP + FE guide + checklist reconcile"
```

---

## Self-Review notes (for the executor)

- **Spec coverage:** gallery catalog §3/§5 → Tasks 2/6/7; migration §3 → Task 1; dependencies §4 → Tasks 3/4/5; templates §5 → Tasks 6/7; events §6.1 → Task 7; errors §6.2 → Tasks 3/4; access §7 → every endpoint task; testing §8 → Tasks 3–8; FE guide → Task 9.
- **Dependency direction is fixed everywhere:** edge `(task_id, depends_on_task_id)` = "task_id depends on depends_on_task_id". `would_create_cycle` walks from `depends_on_task_id` seeking `task_id`. The 409 message names the *existing* relationship: "{dependency.title} already depends on {dependent.title}".
- **Names used consistently:** `would_create_cycle`, `add_dependency`, `dependency_map`, `apply_template`, `template_counts`, `_max_phase_order`, error `DependencyCycle`, column `applied_template_keys`, event `roadmap.template.applied`.
- **The `dependency_count` "no-op" flagged in Slice 1's SOP is retired** in Task 5 (single `dependency_map` fetch drives both `depends_on` and `dependency_count`).
- **Fixture names** (`db`, `client`, `make_member_ctx`) are the plan's placeholders — the executor confirms the real ones (`_member` helper, monkeypatched `event_bus.publish`) against `tests/conftest.py` and the Slice-1 roadmap tests before writing tests, per Global Constraints.
- **201-vs-200 on one route:** dependency create and template apply both vary status by outcome; use `fastapi.responses.JSONResponse` to set the code (idempotent → 200, created → 201).

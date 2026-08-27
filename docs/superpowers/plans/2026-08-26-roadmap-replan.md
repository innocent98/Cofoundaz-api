# Roadmap AI Re-plan (Module 05 · Slice 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a co-pilot re-planner to the roadmap — detect slipped milestones, propose a deterministic dependency-aware date cascade, and let a founder apply the changes they accept (never auto-apply).

**Architecture:** A pure cascade engine (`app/services/roadmap/replan.py`) computes proposed milestone-date shifts by propagating each slip along the Slice-1 task-dependency DAG (projected to milestone level) taking the **max** delay at each node. `preview` is a stateless read of that pure function; `apply` recomputes and commits only the selected, still-valid changes, stamping per-milestone markers and writing one `roadmap_replans` history row. The tree gains a `drift` summary and a per-milestone `replanned` marker.

**Tech Stack:** Python 3.11 · FastAPI · SQLAlchemy 2.0 (typed `Mapped`) · Alembic · PostgreSQL · pytest (real Postgres, per-test rollback).

**Spec:** `docs/superpowers/specs/2026-08-26-roadmap-replan-design.md`

## Global Constraints

- **Worktree/branch:** `feat/roadmap-replan`. **Migration `0008_roadmap_replan`**, `down_revision = '0007_roadmap_applied_templates'`. (Module 04 builds in parallel on migration `0009` — do not take `0009`.)
- **Envelope:** success via `success_response(data)`; errors via `AppError` subclasses. **No new error codes.**
- **No AI-attribution trailer** in any commit (project rule).
- **Tenancy:** every resource resolves to the caller's roadmap; cross-workspace → `NotFound()` (uniform 404). Reuse `_require_roadmap`, `_milestone` (`roadmap.py`).
- **Access:** `preview` + `history` = any member (`require_workspace`); `apply` = editor (`_editor = require_role(founder, team_member)`, already defined `roadmap.py:55`). All require `get_verified_user`.
- **Never auto-applies.** No worker, no consumer for `roadmap.replan`; do not touch `app/services/assessment/service.py`.
- **`REPLAN_BUFFER_DAYS = 7`** — a slipped milestone targets `today + 7d`.
- **`change_id == milestone_id`** everywhere.
- **Direction:** a task edge `(task_id, depends_on_task_id)` means task depends on depends_on. So if a task in milestone **B** depends on a task in milestone **A**, then **A precedes B** (A upstream); when A slips, B shifts.
- **Fixtures:** the plan's `make_member_ctx`/`capture_events` do NOT exist. Use the repo's real `_member` helper (mint verified user + startup + membership + Bearer/`X-Workspace-Id`; see `tests/api/test_roadmap_get.py:9`) and monkeypatch `event_bus.publish` inline (see `tests/services/test_roadmap_generate.py::test_generate_emits_event`). Confirm names against `tests/conftest.py` before writing tests.
- **Every implementer** runs `black`/`isort`/`ruff check`/`mypy app` before reporting DONE.

---

## File Structure

- `app/db/models/roadmap.py` — add `RoadmapReplan` model; add `last_replanned_at`/`last_replan_reason` to `RoadmapMilestone` (modify).
- `app/db/models/__init__.py` — register `RoadmapReplan` (modify).
- `alembic/versions/0008_roadmap_replan.py` — migration (create).
- `tests/factories.py` — `create_replan` (modify).
- `app/services/roadmap/replan.py` — `REPLAN_BUFFER_DAYS`, `Change`, `detect_drift`, `compute_replan`, `apply_replan`, `_milestone_precedence` (create).
- `app/services/roadmap/service.py` — `serialize_tree` gains `roadmap.drift` + milestone `replanned` (modify `:201`–`:266`).
- `app/schemas/roadmap.py` — `ReplanApply` (modify).
- `app/api/v1/endpoints/roadmap.py` — `preview` / `apply` / `history` routes (modify).
- `e2e/test_roadmap_replan.py` + `e2e/test_smoke.py` — live journey + surface (modify/create).
- `docs/sop/2026-08-26-roadmap-replan.md`, `docs/fe-integration-guide-roadmap.md`, `docs/checklist/PROJECT_CHECKLIST.md` — docs.

---

## Task 1: `RoadmapReplan` model + milestone markers + migration `0008`

**Files:**
- Modify: `app/db/models/roadmap.py`, `app/db/models/__init__.py`
- Create: `alembic/versions/0008_roadmap_replan.py`
- Modify: `tests/factories.py`
- Test: `tests/db/test_roadmap_replan_model.py`

**Interfaces:**
- Produces: `RoadmapReplan` (`roadmap_replans`); `RoadmapMilestone.last_replanned_at: Mapped[datetime|None]`, `.last_replan_reason: Mapped[str|None]`; factory `create_replan(db, roadmap, *, applied_by=None, change_count=1, changes=None, summary="Re-planned 1 milestone") -> RoadmapReplan`.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_roadmap_replan_model.py
from datetime import UTC, datetime

from app.db.models.roadmap import RoadmapReplan
from tests.factories import (
    create_milestone, create_phase, create_replan, create_roadmap, create_startup, create_user,
)


def test_replan_and_markers_persist(db):
    startup = create_startup(db, owner=create_user(db))
    roadmap = create_roadmap(db, startup)
    r = create_replan(db, roadmap, change_count=2,
                      changes=[{"milestone_id": "x", "title": "M", "old_due": "2026-01-01",
                                "new_due": "2026-01-08", "reason": "…"}],
                      summary="Re-planned 2 milestones")
    db.flush()
    assert r.roadmap_id == roadmap.id
    assert r.change_count == 2
    assert r.changes[0]["new_due"] == "2026-01-08"

    m = create_milestone(db, create_phase(db, roadmap))
    m.last_replanned_at = datetime.now(UTC)
    m.last_replan_reason = "Shifts 7 days with its dependency 'X'."
    db.flush()
    db.refresh(m)
    assert m.last_replan_reason.startswith("Shifts")

    db.delete(roadmap)
    db.flush()
    assert db.query(RoadmapReplan).count() == 0  # cascade
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/db/test_roadmap_replan_model.py -v`
Expected: FAIL (`ImportError`/attribute missing).

- [ ] **Step 3: Add the model + milestone columns**

```python
# app/db/models/roadmap.py — add to class RoadmapMilestone (after due_on/status/progress):
    last_replanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_replan_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

# and a new model (mirror the file's existing models; JSONB already imported for the module):
class RoadmapReplan(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "roadmap_replans"

    roadmap_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("roadmaps.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    applied_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    change_count: Mapped[int] = mapped_column(Integer, nullable=False)
    changes: Mapped[list] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(String(200), nullable=False)
```

Ensure imports at the top of the file cover `DateTime`, `datetime`, `String`, `Integer`, `JSONB`, `ForeignKey`, `PGUUID` (most already present — add any missing).

- [ ] **Step 4: Register the model**

```python
# app/db/models/__init__.py — add RoadmapReplan to the roadmap import line
from app.db.models.roadmap import (  # noqa: F401
    Roadmap, RoadmapMilestone, RoadmapPhase, RoadmapReplan, RoadmapTask, RoadmapTaskDependency,
)
```

- [ ] **Step 5: Add the factory**

```python
# tests/factories.py
from app.db.models.roadmap import RoadmapReplan


def create_replan(db, roadmap, *, applied_by=None, change_count=1, changes=None,
                  summary="Re-planned 1 milestone") -> RoadmapReplan:
    r = RoadmapReplan(
        roadmap_id=roadmap.id,
        applied_by=applied_by or roadmap.startup_id,  # any user id works in tests; see note
        change_count=change_count, changes=changes or [], summary=summary,
    )
    db.add(r)
    db.flush()
    return r
```
Note: `applied_by` is a real `users.id` FK — in the test above pass a real user (`create_user`) if the FK is enforced; adjust the factory to require a `user` if a raw startup_id violates the FK. Confirm and fix on first run.

- [ ] **Step 6: Autogenerate + clean the migration**

Run: `poetry run alembic revision --autogenerate -m "roadmap replan" --rev-id 0008_roadmap_replan`
Confirm `down_revision = '0007_roadmap_applied_templates'`. It should `create_table("roadmap_replans", …)` and `add_column` twice on `roadmap_milestones`. Strip any unrelated autogen noise (server_default/CHECK diffs on other tables) — same discipline as `0004`–`0007`.

- [ ] **Step 7: Migrate up/down/up + tests**

Run: `poetry run alembic upgrade head && poetry run alembic downgrade -1 && poetry run alembic upgrade head && poetry run pytest tests/db/test_roadmap_replan_model.py -v`
Expected: clean cycle; test passes.

- [ ] **Step 8: Commit**

```bash
git add app/db/models/roadmap.py app/db/models/__init__.py alembic/versions/0008_roadmap_replan.py tests/factories.py tests/db/test_roadmap_replan_model.py
git commit -m "feat(roadmap): RoadmapReplan model + milestone re-plan markers + migration 0008"
```

---

## Task 2: The cascade engine — `detect_drift` + `compute_replan`

**Files:**
- Create: `app/services/roadmap/replan.py`
- Test: `tests/services/test_roadmap_replan_compute.py`

**Interfaces:**
- Consumes: models (Task 1); `dependency_map` is NOT reused (need task→milestone projection — compute here).
- Produces:
  - `REPLAN_BUFFER_DAYS: int = 7`
  - `@dataclass Change` with fields `change_id: uuid.UUID, milestone_id: uuid.UUID, title: str, old_due: date, new_due: date, reason: str`.
  - `detect_drift(db, roadmap) -> list[RoadmapMilestone]`
  - `compute_replan(db, roadmap) -> list[Change]`
  - `_milestone_precedence(db, roadmap) -> dict[uuid.UUID, set[uuid.UUID]]` (`{downstream_ms: {upstream_ms…}}`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/test_roadmap_replan_compute.py
from datetime import date, timedelta

from app.db.models.enums import RoadmapStatus
from app.services.roadmap.replan import REPLAN_BUFFER_DAYS, compute_replan, detect_drift
from tests.factories import (
    create_dependency, create_milestone, create_phase, create_roadmap, create_startup,
    create_task, create_user,
)


def _rm(db):
    startup = create_startup(db, owner=create_user(db))
    roadmap = create_roadmap(db, startup)
    phase = create_phase(db, roadmap)
    return roadmap, phase


def test_slipped_milestone_targets_today_plus_buffer(db):
    roadmap, phase = _rm(db)
    past = date.today() - timedelta(days=10)
    m = create_milestone(db, phase, due_on=past, status=RoadmapStatus.todo)
    db.flush()
    changes = compute_replan(db, roadmap)
    assert len(changes) == 1
    assert changes[0].milestone_id == m.id
    assert changes[0].new_due == date.today() + timedelta(days=REPLAN_BUFFER_DAYS)
    assert "overdue" in changes[0].reason


def test_downstream_dependency_shifts(db):
    roadmap, phase = _rm(db)
    past = date.today() - timedelta(days=5)
    up = create_milestone(db, phase, title="Up", due_on=past, status=RoadmapStatus.todo)
    down = create_milestone(db, phase, title="Down",
                            due_on=date.today() + timedelta(days=30), status=RoadmapStatus.todo)
    tu = create_task(db, up)
    td = create_task(db, down)
    create_dependency(db, td, tu)  # td depends on tu  => up precedes down
    db.flush()
    changes = {c.milestone_id: c for c in compute_replan(db, roadmap)}
    assert up.id in changes and down.id in changes
    shift = (changes[up.id].new_due - up.due_on).days
    assert changes[down.id].new_due == down.due_on + timedelta(days=shift)  # same delta
    assert "dependency" in changes[down.id].reason


def test_diamond_shifts_by_max_not_sum(db):
    roadmap, phase = _rm(db)
    b = create_milestone(db, phase, title="B", due_on=date.today() - timedelta(days=3),
                         status=RoadmapStatus.todo)   # small slip
    c = create_milestone(db, phase, title="C", due_on=date.today() - timedelta(days=20),
                         status=RoadmapStatus.todo)   # big slip
    d = create_milestone(db, phase, title="D", due_on=date.today() + timedelta(days=60),
                         status=RoadmapStatus.todo)
    tb, tc, td = create_task(db, b), create_task(db, c), create_task(db, d)
    create_dependency(db, td, tb)  # D depends on B
    create_dependency(db, td, tc)  # D depends on C
    db.flush()
    ch = {x.milestone_id: x for x in compute_replan(db, roadmap)}
    sb = (ch[b.id].new_due - b.due_on).days
    sc = (ch[c.id].new_due - c.due_on).days
    sd = (ch[d.id].new_due - d.due_on).days
    assert sd == max(sb, sc)   # max, never sb + sc


def test_no_drift_is_empty(db):
    roadmap, phase = _rm(db)
    create_milestone(db, phase, due_on=date.today() + timedelta(days=10), status=RoadmapStatus.todo)
    db.flush()
    assert compute_replan(db, roadmap) == []


def test_done_milestone_is_not_drift(db):
    roadmap, phase = _rm(db)
    create_milestone(db, phase, due_on=date.today() - timedelta(days=10), status=RoadmapStatus.done)
    db.flush()
    assert detect_drift(db, roadmap) == []
    assert compute_replan(db, roadmap) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/services/test_roadmap_replan_compute.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the engine**

```python
# app/services/roadmap/replan.py
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.db.models.enums import RoadmapStatus
from app.db.models.roadmap import (
    RoadmapMilestone, RoadmapPhase, RoadmapTask, RoadmapTaskDependency,
)

REPLAN_BUFFER_DAYS = 7


@dataclass
class Change:
    change_id: uuid.UUID
    milestone_id: uuid.UUID
    title: str
    old_due: date
    new_due: date
    reason: str


def _milestones(db: Session, roadmap_id: uuid.UUID) -> list[RoadmapMilestone]:
    return (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(RoadmapPhase.roadmap_id == roadmap_id)
        .all()
    )


def detect_drift(db: Session, roadmap) -> list[RoadmapMilestone]:
    today = date.today()
    return [
        m for m in _milestones(db, roadmap.id)
        if m.due_on is not None and m.due_on < today and m.status != RoadmapStatus.done
    ]


def _milestone_precedence(db: Session, roadmap) -> dict[uuid.UUID, set[uuid.UUID]]:
    # task -> its milestone
    task_ms = dict(
        db.query(RoadmapTask.id, RoadmapTask.milestone_id)
        .join(RoadmapMilestone, RoadmapTask.milestone_id == RoadmapMilestone.id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .filter(RoadmapPhase.roadmap_id == roadmap.id)
        .all()
    )
    preds: dict[uuid.UUID, set[uuid.UUID]] = {}
    for dep in db.query(RoadmapTaskDependency).all():
        down_ms = task_ms.get(dep.task_id)          # task depends on depends_on
        up_ms = task_ms.get(dep.depends_on_task_id)  # so depends_on's milestone is upstream
        if down_ms and up_ms and down_ms != up_ms:
            preds.setdefault(down_ms, set()).add(up_ms)
    return preds


def _toposort(nodes: list[uuid.UUID], preds: dict[uuid.UUID, set[uuid.UUID]]) -> list[uuid.UUID]:
    ordered: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()

    def visit(n: uuid.UUID, stack: set[uuid.UUID]) -> None:
        if n in seen or n in stack:  # cycle guard: skip the back-edge
            return
        stack.add(n)
        for up in preds.get(n, ()):
            visit(up, stack)
        stack.discard(n)
        if n not in seen:
            seen.add(n)
            ordered.append(n)

    for n in nodes:
        visit(n, set())
    return ordered  # upstreams appear before downstreams


def compute_replan(db: Session, roadmap) -> list[Change]:
    today = date.today()
    milestones = {m.id: m for m in _milestones(db, roadmap.id)}
    base_shift: dict[uuid.UUID, int] = {}
    for mid, m in milestones.items():
        if m.due_on is not None and m.due_on < today and m.status != RoadmapStatus.done:
            target = today + timedelta(days=REPLAN_BUFFER_DAYS)
            base_shift[mid] = max(0, (target - m.due_on).days)
        else:
            base_shift[mid] = 0

    preds = _milestone_precedence(db, roadmap)
    order = _toposort(list(milestones.keys()), preds)

    shift: dict[uuid.UUID, int] = {}
    max_upstream: dict[uuid.UUID, uuid.UUID | None] = {}
    for mid in order:
        s = base_shift.get(mid, 0)
        winner = None
        for up in preds.get(mid, ()):
            if shift.get(up, 0) > s:
                s = shift[up]
                winner = up
        shift[mid] = s
        max_upstream[mid] = winner

    changes: list[Change] = []
    for mid in order:
        s = shift.get(mid, 0)
        m = milestones[mid]
        if s <= 0 or m.due_on is None:
            continue
        if base_shift.get(mid, 0) > 0:
            reason = f"{(today - m.due_on).days} days overdue and not yet done."
        else:
            up = max_upstream.get(mid)
            up_title = milestones[up].title if up in milestones else "an upstream milestone"
            reason = f"Shifts {s} days with its dependency '{up_title}'."
        changes.append(Change(mid, mid, m.title, m.due_on, m.due_on + timedelta(days=s), reason))
    return changes
```

- [ ] **Step 4: Run to verify they pass**

Run: `poetry run pytest tests/services/test_roadmap_replan_compute.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/roadmap/replan.py tests/services/test_roadmap_replan_compute.py
git commit -m "feat(roadmap): re-plan cascade engine (drift + max-shift dependency propagation)"
```

---

## Task 3: `apply_replan` — commit selected changes + markers + history + event

**Files:**
- Modify: `app/services/roadmap/replan.py`
- Test: `tests/services/test_roadmap_replan_apply.py`

**Interfaces:**
- Consumes: `compute_replan`, `Change`, models; `event_bus` (`app.platform.events`).
- Produces: `apply_replan(db, roadmap, actor, change_ids: list[uuid.UUID]) -> dict` returning `{"applied": [str…], "skipped": [str…], "replan_id": str|None, "summary": str|None}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/test_roadmap_replan_apply.py
from datetime import date, timedelta

from app.db.models.enums import RoadmapStatus
from app.db.models.roadmap import RoadmapReplan
from app.services.roadmap.replan import apply_replan, compute_replan
from tests.factories import (
    create_milestone, create_phase, create_roadmap, create_startup, create_user,
)


def _slipped(db):
    user = create_user(db)
    startup = create_startup(db, owner=user)
    roadmap = create_roadmap(db, startup)
    phase = create_phase(db, roadmap)
    m = create_milestone(db, phase, due_on=date.today() - timedelta(days=10),
                         status=RoadmapStatus.todo)
    db.flush()
    return roadmap, user, m


def test_apply_commits_marker_history_event(db, monkeypatch):
    events = []
    from app.platform import events as ev
    monkeypatch.setattr(ev.event_bus, "publish", lambda e, p: events.append((e, p)))

    roadmap, user, m = _slipped(db)
    changes = compute_replan(db, roadmap)
    result = apply_replan(db, roadmap, user, [changes[0].change_id])
    db.flush()

    assert result["applied"] == [str(m.id)] and result["skipped"] == []
    db.refresh(m)
    assert m.due_on == date.today() + timedelta(days=7)
    assert m.last_replanned_at is not None and m.last_replan_reason
    row = db.query(RoadmapReplan).filter_by(roadmap_id=roadmap.id).one()
    assert row.change_count == 1 and row.summary == "Re-planned 1 milestone"
    assert row.changes[0]["milestone_id"] == str(m.id)
    assert any(e == "roadmap.replanned" for e, _ in events)


def test_apply_skips_stale_change_id(db):
    import uuid
    roadmap, user, _m = _slipped(db)
    result = apply_replan(db, roadmap, user, [uuid.uuid4()])  # not in the proposal
    db.flush()
    assert result["applied"] == [] and len(result["skipped"]) == 1
    assert result["replan_id"] is None
    assert db.query(RoadmapReplan).count() == 0  # no row, no event


def test_reapply_is_idempotent(db):
    roadmap, user, m = _slipped(db)
    changes = compute_replan(db, roadmap)
    apply_replan(db, roadmap, user, [changes[0].change_id])
    db.flush()
    # after apply the milestone is no longer overdue -> nothing to apply
    again = apply_replan(db, roadmap, user, [changes[0].change_id])
    db.flush()
    assert again["applied"] == []
    assert db.query(RoadmapReplan).count() == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/services/test_roadmap_replan_apply.py -v`
Expected: FAIL (`apply_replan` undefined).

- [ ] **Step 3: Implement `apply_replan`**

```python
# app/services/roadmap/replan.py  (append; add imports)
from datetime import UTC, datetime

from app.db.models.roadmap import RoadmapReplan
from app.platform.events import event_bus


def apply_replan(db: Session, roadmap, actor, change_ids: list[uuid.UUID]) -> dict:
    proposal = {c.change_id: c for c in compute_replan(db, roadmap)}
    now = datetime.now(UTC)
    applied: list[str] = []
    skipped: list[str] = []
    snapshot: list[dict] = []

    for cid in change_ids:
        c = proposal.get(cid)
        if c is None:
            skipped.append(str(cid))
            continue
        m = db.get(RoadmapMilestone, c.milestone_id)
        m.due_on = c.new_due
        m.last_replanned_at = now
        m.last_replan_reason = c.reason
        applied.append(str(cid))
        snapshot.append({
            "milestone_id": str(c.milestone_id), "title": c.title,
            "old_due": c.old_due.isoformat(), "new_due": c.new_due.isoformat(), "reason": c.reason,
        })

    replan_id: str | None = None
    summary: str | None = None
    if applied:
        n = len(applied)
        summary = f"Re-planned {n} milestone{'s' if n != 1 else ''}"
        replan = RoadmapReplan(
            roadmap_id=roadmap.id, applied_by=actor.id, change_count=n,
            changes=snapshot, summary=summary,
        )
        db.add(replan)
        db.flush()
        replan_id = str(replan.id)
        event_bus.publish("roadmap.replanned", {
            "startup_id": str(roadmap.startup_id), "roadmap_id": str(roadmap.id),
            "replan_id": replan_id, "change_count": n, "applied_by": str(actor.id),
        })
    db.flush()
    return {"applied": applied, "skipped": skipped, "replan_id": replan_id, "summary": summary}
```

- [ ] **Step 4: Run to verify they pass**

Run: `poetry run pytest tests/services/test_roadmap_replan_apply.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/roadmap/replan.py tests/services/test_roadmap_replan_apply.py
git commit -m "feat(roadmap): apply_replan — commit selected changes, markers, history, event"
```

---

## Task 4: `preview` + `apply` endpoints

**Files:**
- Modify: `app/schemas/roadmap.py`, `app/api/v1/endpoints/roadmap.py`
- Test: `tests/api/test_roadmap_replan_api.py`

**Interfaces:**
- Consumes: `compute_replan`, `apply_replan` (Tasks 2–3); `_require_roadmap`, `_startup`, `_editor` (`roadmap.py`).
- Produces: `ReplanApply {change_ids: list[uuid.UUID]}`; routes `POST /roadmap/replan/preview`, `POST /roadmap/replan/apply`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_roadmap_replan_api.py
from datetime import date, timedelta

from app.db.models.enums import MembershipRole, RoadmapStatus, StartupStage
from tests.factories import create_membership, create_startup, create_user
# NOTE: reuse the real _member helper pattern from tests/api/test_roadmap_get.py


def _force_slip(client, headers, db):
    """GET the tree (lazy-generates), then PATCH the first milestone's due_on into the past."""
    data = client.get("/api/v1/roadmap", headers=headers).json()["data"]
    mid = data["phases"][0]["milestones"][0]["id"]
    past = (date.today() - timedelta(days=10)).isoformat()
    client.patch(f"/api/v1/roadmap/milestones/{mid}", headers=headers, json={"due_on": past})
    return mid


def test_preview_then_apply(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.validation)
    _force_slip(client, ctx.headers, db)

    pv = client.post("/api/v1/roadmap/replan/preview", headers=ctx.headers)
    assert pv.status_code == 200
    body = pv.json()["data"]
    assert body["drift_count"] >= 1 and len(body["changes"]) >= 1
    cid = body["changes"][0]["change_id"]

    ap = client.post("/api/v1/roadmap/replan/apply", headers=ctx.headers,
                     json={"change_ids": [cid]})
    assert ap.status_code == 200
    assert cid in ap.json()["data"]["applied"]
    assert ap.json()["data"]["replan_id"]


def test_apply_forbidden_for_mentor(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.mentor, stage=StartupStage.validation)
    import uuid
    r = client.post("/api/v1/roadmap/replan/apply", headers=ctx.headers,
                    json={"change_ids": [str(uuid.uuid4())]})
    assert r.status_code == 403


def test_preview_no_drift_empty(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.validation)
    client.get("/api/v1/roadmap", headers=ctx.headers)  # generate, no slip
    pv = client.post("/api/v1/roadmap/replan/preview", headers=ctx.headers)
    assert pv.status_code == 200 and pv.json()["data"]["changes"] == []
```

Adapt `make_member_ctx` to the repo's real `_member` helper.

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/api/test_roadmap_replan_api.py -v`
Expected: FAIL (routes missing).

- [ ] **Step 3: Add the schema**

```python
# app/schemas/roadmap.py  (append)
class ReplanApply(BaseModel):
    change_ids: list[uuid.UUID]
```

- [ ] **Step 4: Add the routes**

```python
# app/api/v1/endpoints/roadmap.py  (add imports + routes)
from app.services.roadmap.replan import apply_replan, compute_replan, detect_drift
from app.schemas.roadmap import ReplanApply


@router.post("/replan/preview")
def replan_preview(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    roadmap = _require_roadmap(db, membership)
    changes = compute_replan(db, roadmap)
    return success_response({
        "drift_count": len(detect_drift(db, roadmap)),
        "changes": [{"change_id": str(c.change_id), "milestone_id": str(c.milestone_id),
                     "title": c.title, "old_due": c.old_due.isoformat(),
                     "new_due": c.new_due.isoformat(), "reason": c.reason} for c in changes],
    })


@router.post("/replan/apply")
def replan_apply(
    body: ReplanApply,
    membership: Membership = Depends(_editor),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    roadmap = _require_roadmap(db, membership)
    result = apply_replan(db, roadmap, user, body.change_ids)
    db.commit()
    return success_response(result)
```

Note the route order: define `/replan/preview` and `/replan/apply` — no collision with `/{...}` uuid routes (distinct literal paths).

- [ ] **Step 5: Run to verify they pass**

Run: `poetry run pytest tests/api/test_roadmap_replan_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/schemas/roadmap.py app/api/v1/endpoints/roadmap.py tests/api/test_roadmap_replan_api.py
git commit -m "feat(roadmap): POST /replan/preview + /replan/apply endpoints"
```

---

## Task 5: `GET /replan/history` + tree `drift` + milestone `replanned`

**Files:**
- Modify: `app/services/roadmap/service.py` (`serialize_tree`), `app/api/v1/endpoints/roadmap.py`
- Test: `tests/api/test_roadmap_replan_history.py`, `tests/api/test_roadmap_drift_tree.py`

**Interfaces:**
- Produces: `GET /roadmap/replan/history`; `serialize_tree` adds `roadmap.drift.slipped_count` and milestone `replanned`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_roadmap_drift_tree.py
from datetime import date, timedelta
from app.db.models.enums import MembershipRole, StartupStage


def test_tree_exposes_drift_and_replanned(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.validation)
    data = client.get("/api/v1/roadmap", headers=ctx.headers).json()["data"]
    assert data["roadmap"]["drift"]["slipped_count"] == 0
    mid = data["phases"][0]["milestones"][0]["id"]
    # every milestone starts with replanned == null
    m0 = next(m for p in data["phases"] for m in p["milestones"] if m["id"] == mid)
    assert m0["replanned"] is None

    past = (date.today() - timedelta(days=10)).isoformat()
    client.patch(f"/api/v1/roadmap/milestones/{mid}", headers=ctx.headers, json={"due_on": past})
    d2 = client.get("/api/v1/roadmap", headers=ctx.headers).json()["data"]
    assert d2["roadmap"]["drift"]["slipped_count"] >= 1

    cid = client.post("/api/v1/roadmap/replan/preview", headers=ctx.headers).json()["data"]["changes"][0]["change_id"]
    client.post("/api/v1/roadmap/replan/apply", headers=ctx.headers, json={"change_ids": [cid]})
    d3 = client.get("/api/v1/roadmap", headers=ctx.headers).json()["data"]
    m3 = next(m for p in d3["phases"] for m in p["milestones"] if m["id"] == mid)
    assert m3["replanned"] is not None and m3["replanned"]["reason"]
```

```python
# tests/api/test_roadmap_replan_history.py
from datetime import date, timedelta
from app.db.models.enums import MembershipRole, StartupStage


def test_history_lists_applied_replan(client, db, make_member_ctx):
    ctx = make_member_ctx(role=MembershipRole.founder, stage=StartupStage.validation)
    data = client.get("/api/v1/roadmap", headers=ctx.headers).json()["data"]
    mid = data["phases"][0]["milestones"][0]["id"]
    client.patch(f"/api/v1/roadmap/milestones/{mid}", headers=ctx.headers,
                 json={"due_on": (date.today() - timedelta(days=10)).isoformat()})
    cid = client.post("/api/v1/roadmap/replan/preview", headers=ctx.headers).json()["data"]["changes"][0]["change_id"]
    client.post("/api/v1/roadmap/replan/apply", headers=ctx.headers, json={"change_ids": [cid]})

    h = client.get("/api/v1/roadmap/replan/history", headers=ctx.headers)
    assert h.status_code == 200
    items = h.json()["data"]
    assert len(items) == 1 and items[0]["change_count"] == 1
    assert items[0]["summary"] == "Re-planned 1 milestone"
    assert items[0]["changes"][0]["milestone_id"] == mid
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/api/test_roadmap_drift_tree.py tests/api/test_roadmap_replan_history.py -v`
Expected: FAIL.

- [ ] **Step 3: Add `drift` + `replanned` to `serialize_tree`**

In `app/services/roadmap/service.py::serialize_tree` (`:201`–`:266`): compute a drift count and add it to the `"roadmap"` dict; add `"replanned"` to each milestone dict.

```python
    from app.services.roadmap.replan import detect_drift  # local import avoids a cycle
    slipped = len(detect_drift(db, roadmap))
```
- In the milestone dict (near `"dependency_count"`), add:
```python
                    "replanned": (
                        {"at": m.last_replanned_at.isoformat(), "reason": m.last_replan_reason}
                        if m.last_replanned_at else None
                    ),
```
- In the `"roadmap"` dict (`:260`), add `"drift": {"slipped_count": slipped}`.

- [ ] **Step 4: Add the history route**

```python
# app/api/v1/endpoints/roadmap.py
from app.db.models.roadmap import RoadmapReplan


@router.get("/replan/history")
def replan_history(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    roadmap = _require_roadmap(db, membership)
    rows = (
        db.query(RoadmapReplan).filter_by(roadmap_id=roadmap.id)
        .order_by(RoadmapReplan.created_at.desc()).all()
    )
    from app.services.roadmap.service import person_ref
    return success_response([
        {"id": str(r.id), "change_count": r.change_count, "summary": r.summary,
         "applied_by": person_ref(db, r.applied_by),
         "created_at": r.created_at.isoformat(), "changes": r.changes}
        for r in rows
    ])
```

- [ ] **Step 5: Run to verify they pass**

Run: `poetry run pytest tests/api/test_roadmap_drift_tree.py tests/api/test_roadmap_replan_history.py tests/api/test_roadmap_get.py -v`
Expected: PASS (new tests + Slice-1 tree tests still green).

- [ ] **Step 6: Full suite + lint + commit**

Run: `poetry run pytest -q && poetry run black --check . && poetry run isort --check . && poetry run ruff check . && poetry run mypy app`

```bash
git add app/services/roadmap/service.py app/api/v1/endpoints/roadmap.py tests/api/test_roadmap_replan_history.py tests/api/test_roadmap_drift_tree.py
git commit -m "feat(roadmap): GET /replan/history + tree drift summary + milestone replanned marker"
```

---

## Task 6: Live E2E + smoke surface

**Files:**
- Create: `e2e/test_roadmap_replan.py`
- Modify: `e2e/test_smoke.py`

- [ ] **Step 1: Add the new paths to smoke**

```python
# e2e/test_smoke.py  (add to the openapi path list)
        "/api/v1/roadmap/replan/preview",
        "/api/v1/roadmap/replan/apply",
        "/api/v1/roadmap/replan/history",
```

- [ ] **Step 2: Write the E2E journey**

Reuse the Slice-1/2 roadmap journey helpers in `e2e/test_roadmap.py` (`http` client + header helpers). Steps, capturing bodies to `e2e/_captures/roadmap/`:
- founder onboards → `GET /roadmap` (generates); read the first milestone id;
- `PATCH .../milestones/{id}` `{due_on: <10 days ago>}` to force a slip;
- `POST /roadmap/replan/preview` → assert `drift_count >= 1`, capture (`replan_preview.json`);
- `POST /roadmap/replan/apply {change_ids:[that id]}` → assert `applied`, capture (`replan_apply.json`);
- `GET /roadmap` → assert the milestone's `due_on` moved + `replanned` marker set + `drift.slipped_count` reduced (`get_tree_replanned.json`);
- `GET /roadmap/replan/history` → assert one entry, capture (`replan_history.json`).

- [ ] **Step 3: Run the E2E suite**

Run: `make e2e`
Expected: green on a real server, DB migrated `0001→0008`.

- [ ] **Step 4: Commit**

```bash
git add e2e/test_roadmap_replan.py e2e/test_smoke.py e2e/_captures/roadmap
git commit -m "test(roadmap): live E2E for AI re-plan + smoke surface"
```

---

## Task 7: SOP + FE integration guide + checklist reconcile

**Files:**
- Create: `docs/sop/2026-08-26-roadmap-replan.md`
- Modify: `docs/fe-integration-guide-roadmap.md`, `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: Write the SOP**

Cover: what shipped (re-plan preview/apply/history + drift/replanned tree fields + migration `0008`), why, how (max-shift dependency-DAG cascade; stateless preview + recompute-on-apply; markers + history; `roadmap.replanned` emitted, no consumer), files/paths, verification (test counts + `make e2e`), rollback (`alembic downgrade -1`), follow-ups (AI rationale → Module 03; notification → Module 20; `roadmap.replan` job stays an unconsumed stub; phases/tasks not shifted in v1).

- [ ] **Step 2: Update the FE guide from live captures**

Append to `docs/fe-integration-guide-roadmap.md` from `e2e/_captures/roadmap/replan_*.json` + `get_tree_replanned.json` (verbatim): preview / apply / history payloads; the tree's new `roadmap.drift` + milestone `replanned`; the field-nesting note (`replanned` on the tree only); the "never auto-applies" UX note; verification table.

- [ ] **Step 3: Reconcile the checklist**

In `docs/checklist/PROJECT_CHECKLIST.md`: mark Module 05 **Slice 3 done** and — since all three slices ship — mark **Module 05 complete**. Update the snapshot counts.

- [ ] **Step 4: Final verification**

Run: `poetry run pytest -q && poetry run black --check . && poetry run isort --check . && poetry run ruff check . && poetry run mypy app && make e2e`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add docs/sop/2026-08-26-roadmap-replan.md docs/fe-integration-guide-roadmap.md docs/checklist/PROJECT_CHECKLIST.md
git commit -m "docs(roadmap): Slice 3 SOP + FE guide + checklist reconcile"
```

---

## Self-Review notes (for the executor)

- **Spec coverage:** data model §3 → Task 1; cascade §4.1 → Task 2; apply §4.2 → Task 3; endpoints §5 → Tasks 4–5; tree additions §5 → Task 5; events/errors §6 → Tasks 3–4; testing §7 → Tasks 2–6; FE guide → Task 7.
- **Direction is fixed:** a task edge `(task_id, depends_on_task_id)` → `depends_on`'s milestone is **upstream** (precedes); `_milestone_precedence` records `preds[down_ms].add(up_ms)`; propagation is `shift[M] = max(base, max over upstream)`; the diamond test asserts **max, not sum**.
- **Names consistent:** `Change`, `change_id==milestone_id`, `detect_drift`, `compute_replan`, `apply_replan`, `_milestone_precedence`, `REPLAN_BUFFER_DAYS`, model `RoadmapReplan`, columns `last_replanned_at`/`last_replan_reason`, event `roadmap.replanned`, tree `drift.slipped_count` + milestone `replanned`.
- **Migration is `0008`** (Module 04 owns `0009`).
- **Fixtures** (`db`, `client`, `make_member_ctx`, `monkeypatch`) → confirm real names (`_member`, inline `event_bus.publish` patch) against `tests/conftest.py` before writing tests.

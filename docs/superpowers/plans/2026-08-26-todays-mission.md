# Today's Mission (Module 04) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A daily 1–3 task mission generated from the founder's roadmap, with completion tracking, snooze/reorder/reject, custom tasks, a derived streak, history, and settings.

**Architecture:** A `mission` service reads (never writes) the roadmap to select today's tasks lazily on first read, storing a `missions` + `mission_tasks` tree with a soft (un-FK'd) `roadmap_task_id` link. Streak is derived. New `app/services/mission/` + `app/api/v1/endpoints/mission.py`; no roadmap file is modified.

**Tech Stack:** Python 3.11 · FastAPI · SQLAlchemy 2.0 · Alembic · PostgreSQL · pytest (real Postgres, per-test rollback).

**Spec:** `docs/superpowers/specs/2026-08-26-todays-mission-design.md`

## Global Constraints

- **Worktree/branch:** `feat/todays-mission`. **Migration `0009_mission`**, `down_revision = '0007_roadmap_applied_templates'`. (Roadmap Slice 3 owns `0008` in a parallel build — do NOT take `0008`. On merge, if `0008` lands first, this stays `0009`; if not, coordinate.)
- **READ-ONLY roadmap:** never modify `app/services/roadmap/*` or `app/api/v1/endpoints/roadmap.py`. Import roadmap *models* for reads only.
- **Envelope:** `success_response(data)`; errors via `AppError` subclasses. No new error codes.
- **No AI-attribution trailer** in commits.
- **Tenancy:** scope to the caller's workspace; cross-workspace → `NotFound()` (uniform 404). Reuse `require_workspace` / `require_role(founder, team_member)` (editor) / `get_verified_user`.
- **`mission_tasks.roadmap_task_id`** is a nullable UUID **with no FK**.
- **Reuse `TaskEffort`** for `mission_tasks.effort`.
- **Fixtures:** plan's `make_member_ctx` does NOT exist — use the repo's real `_member` helper (`tests/api/test_roadmap_get.py:9`) and monkeypatch `event_bus.publish` inline (`tests/services/test_roadmap_generate.py::test_generate_emits_event`). Confirm names against `tests/conftest.py`.
- **Env:** deps installed; `poetry run` for all tools. Isolated test DB (`.env` → `cofoundaz_test_mission`); Postgres up. Do NOT change `.env`.
- Every implementer runs `black`/`isort`/`ruff check`/`mypy app` before DONE.

---

## File Structure
- `app/db/models/enums.py` — add `MissionStatus`, `MissionTaskStatus` (modify).
- `app/db/models/mission.py` — `Mission`, `MissionTask`, `MissionSettings` (create).
- `app/db/models/__init__.py` — register (modify).
- `alembic/versions/0009_mission.py` — migration (create).
- `tests/factories.py` — `create_mission`, `create_mission_task`, `create_mission_settings` (modify).
- `app/services/mission/__init__.py`, `app/services/mission/service.py` — generation, selection, streak, serialize (create).
- `app/schemas/mission.py` — request models (create).
- `app/api/v1/endpoints/mission.py` — routes + resolvers (create).
- `app/api/v1/api.py` — register router at `/missions` (modify).
- `e2e/test_mission.py`, `e2e/test_smoke.py` — E2E + surface (create/modify).
- `docs/sop/…`, `docs/fe-integration-guide-mission.md`, `docs/checklist/PROJECT_CHECKLIST.md` — docs.

---

## Task 1: Enums + 3 models + migration `0009` + factories

**Files:** Modify `enums.py`, `__init__.py`, `tests/factories.py`; Create `app/db/models/mission.py`, `alembic/versions/0009_mission.py`; Test `tests/db/test_mission_models.py`.

**Interfaces produced:** enums `MissionStatus(pending|complete)`, `MissionTaskStatus(todo|done|snoozed|rejected)`; models `Mission` (`missions`), `MissionTask` (`mission_tasks`), `MissionSettings` (`mission_settings`); factories `create_mission(db, startup, *, mission_date=date.today(), generated_by="system", status=MissionStatus.pending)`, `create_mission_task(db, mission, *, title="T", effort=TaskEffort.medium, status=MissionTaskStatus.todo, order=0, roadmap_task_id=None)`, `create_mission_settings(db, startup, *, mission_size=3)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_mission_models.py
from datetime import date
from app.db.models.enums import MissionStatus, MissionTaskStatus
from app.db.models.mission import Mission, MissionTask
from tests.factories import (
    create_mission, create_mission_task, create_mission_settings,
    create_startup, create_user,
)

def test_mission_tree_and_settings(db):
    s = create_startup(db, owner=create_user(db))
    m = create_mission(db, s, mission_date=date.today())
    t = create_mission_task(db, m, title="Interviews")
    st = create_mission_settings(db, s, mission_size=2)
    assert m.startup_id == s.id and m.status == MissionStatus.pending
    assert t.mission_id == m.id and t.status == MissionTaskStatus.todo
    assert t.roadmap_task_id is None
    assert st.mission_size == 2
    db.delete(m); db.flush()
    assert db.query(MissionTask).count() == 0  # cascade

def test_one_mission_per_day(db):
    import sqlalchemy, pytest
    s = create_startup(db, owner=create_user(db))
    create_mission(db, s, mission_date=date.today())
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        create_mission(db, s, mission_date=date.today()); db.flush()
```

- [ ] **Step 2: Run to verify it fails** — `poetry run pytest tests/db/test_mission_models.py -v` → FAIL (import error).

- [ ] **Step 3: Add enums**
```python
# app/db/models/enums.py (append)
class MissionStatus(str, enum.Enum):
    pending = "pending"
    complete = "complete"

class MissionTaskStatus(str, enum.Enum):
    todo = "todo"
    done = "done"
    snoozed = "snoozed"
    rejected = "rejected"
```

- [ ] **Step 4: Create models** (`app/db/models/mission.py`) — mirror `app/db/models/roadmap.py` conventions (UUIDMixin, TimestampMixin, `Enum(..., native_enum=False, length=20)`, PGUUID FKs):
```python
import uuid
from datetime import date, datetime, time
from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, Time, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import MissionStatus, MissionTaskStatus, TaskEffort

class Mission(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "missions"
    startup_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True)
    mission_date: Mapped[date] = mapped_column(Date, nullable=False)
    generated_by: Mapped[str] = mapped_column(String(10), nullable=False, default="system")
    status: Mapped[MissionStatus] = mapped_column(Enum(MissionStatus, native_enum=False, length=20), default=MissionStatus.pending, nullable=False)
    __table_args__ = (UniqueConstraint("startup_id", "mission_date", name="uq_mission_startup_date"),)

class MissionTask(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "mission_tasks"
    mission_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True)
    roadmap_task_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)  # no FK — soft link
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    effort: Mapped[TaskEffort] = mapped_column(Enum(TaskEffort, native_enum=False, length=20), default=TaskEffort.medium, nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[MissionTaskStatus] = mapped_column(Enum(MissionTaskStatus, native_enum=False, length=20), default=MissionTaskStatus.todo, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)

class MissionSettings(TimestampMixin, Base):
    __tablename__ = "mission_settings"
    startup_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), primary_key=True)
    mission_size: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    delivery_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(6, 0))
    weekend_missions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```
Register all three in `app/db/models/__init__.py`.

- [ ] **Step 5: Add factories** (`tests/factories.py`) mirroring the roadmap factories, with `create_mission_settings`.

- [ ] **Step 6: Autogenerate + clean migration** — `poetry run alembic revision --autogenerate -m "mission" --rev-id 0009_mission`; confirm `down_revision = '0007_roadmap_applied_templates'`; only the 3 mission tables; strip autogen noise. Run up/down/up clean.

- [ ] **Step 7: Run tests** — `poetry run pytest tests/db/test_mission_models.py -v` → PASS.

- [ ] **Step 8: Commit** — `git add … && git commit -m "feat(mission): enums, models, migration 0009, factories"`

---

## Task 2: Generation service + streak

**Files:** Create `app/services/mission/__init__.py`, `app/services/mission/service.py`; Test `tests/services/test_mission_generate.py`.

**Interfaces produced:** `get_or_generate_today(db, startup) -> Mission | None`; `streak(db, startup) -> int`; `serialize_mission(db, mission, streak) -> dict`. Reads roadmap via `Roadmap`/`RoadmapTask`/`RoadmapMilestone`/`RoadmapPhase` models (read-only).

- [ ] **Step 1: Write failing tests**
```python
# tests/services/test_mission_generate.py
from datetime import date, timedelta
from app.db.models.enums import MissionStatus, MissionTaskStatus, RoadmapStatus
from app.services.mission.service import get_or_generate_today, streak
from tests.factories import (
    create_mission, create_milestone, create_phase, create_roadmap, create_startup,
    create_task, create_user, create_mission_settings,
)

def test_generates_from_roadmap_tasks(db):
    s = create_startup(db, owner=create_user(db))
    r = create_roadmap(db, s); ph = create_phase(db, r)
    m1 = create_milestone(db, ph, due_on=date.today() + timedelta(days=2))
    for i in range(5):
        create_task(db, m1, title=f"T{i}", status=RoadmapStatus.todo)
    db.flush()
    mission = get_or_generate_today(db, s)
    assert mission is not None
    tasks = sorted(mission.tasks if hasattr(mission, "tasks") else
                   [t for t in _mission_tasks(db, mission)], key=lambda t: t.order)  # adjust to query
    # default mission_size = 3
    from app.db.models.mission import MissionTask
    got = db.query(MissionTask).filter_by(mission_id=mission.id).count()
    assert got == 3
    assert all(t.roadmap_task_id is not None for t in db.query(MissionTask).filter_by(mission_id=mission.id))

def test_no_roadmap_returns_none(db):
    s = create_startup(db, owner=create_user(db))
    assert get_or_generate_today(db, s) is None

def test_generation_is_idempotent(db):
    s = create_startup(db, owner=create_user(db))
    r = create_roadmap(db, s); ph = create_phase(db, r)
    create_task(db, create_milestone(db, ph), status=RoadmapStatus.todo)
    db.flush()
    a = get_or_generate_today(db, s); db.flush()
    b = get_or_generate_today(db, s)
    assert a.id == b.id

def test_streak_counts_consecutive_completed(db):
    s = create_startup(db, owner=create_user(db))
    for d in range(1, 4):  # yesterday, 2 days ago, 3 days ago all complete
        create_mission(db, s, mission_date=date.today() - timedelta(days=d), status=MissionStatus.complete)
    db.flush()
    assert streak(db, s) == 3
```
(Fix the `_mission_tasks` helper reference — query `MissionTask` directly.)

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement** `app/services/mission/service.py`:
  - `_roadmap(db, startup)` → `db.query(Roadmap).filter_by(startup_id=startup.id).first()`.
  - `_candidate_tasks(db, roadmap)` → query `RoadmapTask` join `RoadmapMilestone` join `RoadmapPhase` filter `RoadmapPhase.roadmap_id == roadmap.id`, `RoadmapTask.status != done`, order by `RoadmapMilestone.due_on.asc().nullslast(), RoadmapPhase.order, RoadmapMilestone.order, RoadmapTask.order`.
  - `get_or_generate_today`: existing today's mission → return; no roadmap → None; else settings (lazy default), weekend-off check (empty mission), carry-forward snoozed from most recent prior mission first, fill to `mission_size`, create rows with templated `reason` (`f"From your '{milestone.title}' milestone."`).
  - `streak`: walk back from today/yesterday counting consecutive `complete` missions.
  - `serialize_mission`.

- [ ] **Step 4: Run tests → PASS. Step 5: Commit.**

---

## Task 3: `GET /missions/today` + settings endpoints

**Files:** Create `app/schemas/mission.py`, `app/api/v1/endpoints/mission.py`; Modify `app/api/v1/api.py`; Test `tests/api/test_mission_today.py`.

**Interfaces produced:** `GET /missions/today`, `GET /missions/settings`, `PATCH /missions/settings`; resolvers `_startup`, `_settings`. Router registered at `/missions`.

- [ ] Tests: `GET /missions/today` after roadmap generation returns 1–3 tasks + `streak`; no roadmap → `{status:"no_roadmap"}`; requires auth (401); settings GET returns defaults; PATCH `mission_size=2` persists; `mission_size=5` → 422.
- [ ] Implement routes (mirror `app/api/v1/endpoints/assessments.py`: `require_workspace` for reads, `require_role(founder, team_member)` for writes, `get_verified_user`, `success_response`). `GET /today` calls `get_or_generate_today`; `db.commit()`; empty-state when None. `PATCH /settings` clamps `mission_size` to 1–3 (else `AppError("VALIDATION_ERROR", …, 422)`).
- [ ] Register router in `api.py` (`api_router.include_router(mission.router, prefix="/missions", tags=["missions"])`).
- [ ] Run → PASS; commit.

---

## Task 4: task actions — `POST /missions/tasks` + `PATCH /missions/tasks/{id}`

**Files:** Modify `mission.py` endpoints + `service.py` (action helpers) + `schemas/mission.py`; Test `tests/api/test_mission_tasks.py`.

- [ ] Tests: add custom task (roadmap_task_id null, appended); `complete` → `status=done` + `completed_at` + `mission.task.completed` event; completing the LAST task → `mission.completed` event + mission status complete; streak-milestone event fires at a 7-day streak (seed 6 prior complete days + complete today); `snooze`/`reorder`/`reject` transitions; mentor `403`; cross-workspace `404`.
- [ ] Implement: `POST /missions/tasks` (editor) appends to today's mission. `PATCH /missions/tasks/{id}` (editor) dispatches on `action`:
  - `complete`: set done + completed_at; emit `mission.task.completed`; if all non-rejected tasks done → mission.status=complete, emit `mission.completed`, compute `streak`, and if it crosses 7/30/100 emit `mission.streak.milestone`.
  - `snooze`/`reorder`/`reject` as specified. `_mission_task(db, membership, id)` resolver scopes to the caller's workspace (mission.startup_id) → uniform 404.
- [ ] Run → PASS; commit.

---

## Task 5: `GET /missions/history`

**Files:** Modify `mission.py` + `service.py`; Test `tests/api/test_mission_history.py`.

- [ ] Tests: history lists past missions reverse-chron with `completed/total` counts + status; weekly completion % present.
- [ ] Implement `GET /missions/history` (member) → serialize prior missions with per-mission done/total. Run → PASS; full suite + lint; commit.

---

## Task 6: Live E2E + smoke surface

**Files:** Create `e2e/test_mission.py`; Modify `e2e/test_smoke.py`.

- [ ] Add mission paths to smoke openapi assertion (`/missions/today`, `/missions/tasks`, `/missions/tasks/{task_id}`, `/missions/history`, `/missions/settings`).
- [ ] E2E journey (reuse onboarding+roadmap setup helpers): onboard → roadmap generates → `GET /missions/today` returns 1–3 tasks from the roadmap → complete each → `mission.completed` reachable → `GET /missions/history` reflects it. Capture bodies to `e2e/_captures/mission/`.
- [ ] `make e2e` green (DB migrated `0001→0009`). Commit.

Note: `make e2e` uses the shared `cofoundaz_e2e` DB + a fixed server port — if the parallel Slice-3 e2e runs at the same moment they can collide. Run this task's `make e2e` when the other worktree is not mid-e2e (the controller serializes the two E2E steps).

---

## Task 7: SOP + FE integration guide + checklist reconcile

- [ ] SOP `docs/sop/2026-08-26-todays-mission.md` (what/why/how/files/verification/rollback/follow-ups: cron+push → Module 20, AI reasons → Module 03).
- [ ] FE guide `docs/fe-integration-guide-mission.md` from live captures (today/settings/tasks/history payloads; the `no_roadmap` empty-state; the streak; field-nesting; verification table).
- [ ] Checklist: mark **Module 04 shipped**; update snapshot.
- [ ] Final `poetry run pytest -q && black/isort/ruff/mypy && make e2e` green. Commit.

---

## Self-Review notes
- **Read-only roadmap** is the load-bearing constraint — Tasks 2/6 read roadmap models; no task edits a roadmap file. `roadmap_task_id` is un-FK'd.
- **Migration `0009`** (Slice 3 owns `0008`). `down_revision` is `0007_roadmap_applied_templates` for both — the two migrations are siblings; on merge they form `0007 → {0008, 0009}` which Alembic resolves as a linear or branched chain; the controller ensures one `down_revision` chains off the other at merge time if a strict linear history is required.
- **Names:** `get_or_generate_today`, `streak`, `serialize_mission`, models `Mission`/`MissionTask`/`MissionSettings`, enums `MissionStatus`/`MissionTaskStatus`, events `mission.task.completed`/`mission.completed`/`mission.streak.milestone`.
- **Fixtures** confirmed against `tests/conftest.py` (real `_member`, inline event patch) before writing tests.

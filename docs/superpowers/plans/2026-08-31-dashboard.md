# Module 02 Founder Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the founder home screen — a single `GET /dashboard/summary` aggregation call over the shipped modules plus a durable, cursor-paginated team-activity feed.

**Architecture:** An in-process aggregation BFF (`app/services/dashboard/service.py`) composes existing module service functions (no new business logic) with per-section resilience. One new durable write — `write_activity()` (sibling of `write_audit`) into a new `activity_log` table — is wired into ~8 meaningful action sites already present in the shipped modules. AI/financial widgets render as the PRD's honest empty-states.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.0 (typed `Mapped`) / Alembic / PostgreSQL / Poetry / pytest (real Postgres, per-test transaction rollback).

**Spec:** `docs/superpowers/specs/2026-08-31-dashboard-design.md`

## Global Constraints

- **Branch:** `feat/dashboard`. **Migration number:** `0010_dashboard`, `down_revision = "0009_mission"` (current head). Single branch — no migration-sibling coordination.
- **Response envelope:** every endpoint returns `success_response(data)` from `app.core.envelope` → `{ "data": …, "meta": null }`. Errors via `AppError` / the `app.core.errors` taxonomy — **no new error codes**.
- **Access:** reads = any active member — `membership: Membership = Depends(require_workspace)` + `user: User = Depends(get_verified_user)` + `db: Session = Depends(get_db)`. Cross-tenant / non-member → uniform `403` via `require_workspace` (no path-resolved resource). Mentor may read.
- **`entity_id` on `activity_log` has NO foreign key** — it is a soft cross-module reference (mirrors `mission_tasks.roadmap_task_id`).
- **`write_activity` runs in the caller's transaction** (before the endpoint's `db.commit()`), so a rolled-back action leaves no phantom activity.
- **No AI-attribution** in any commit message or artifact (authored by Adebayo alone).
- **TDD**, real Postgres + per-test rollback. Reuse factories in `tests/factories.py`.
- **Deferred (do NOT build):** briefing endpoints, `kpi_snapshots`/`briefings` tables, realtime `workspace.{id}.activity`, widget-level role/grant filtering. Financial KPIs are declared-but-`null`.

---

## File Structure

**Create:**
- `app/db/models/activity.py` — `ActivityLog` model.
- `app/platform/activity.py` — `write_activity()` helper.
- `app/services/dashboard/__init__.py`, `app/services/dashboard/service.py` — aggregation (`get_summary`, `get_activity`) + `UPCOMING_WINDOW_DAYS`.
- `app/api/v1/endpoints/dashboard.py` — the two routes.
- `alembic/versions/0010_dashboard.py` — the migration.
- Tests: `tests/platform/test_activity.py`, `tests/api/test_activity_wiring.py`, `tests/services/test_dashboard_summary.py`, `tests/api/test_dashboard_summary.py`, `tests/api/test_dashboard_activity.py`, `e2e/test_dashboard.py`.
- Docs: `docs/sop/2026-08-31-dashboard.md`, `docs/fe-integration-guide-dashboard.md`.

**Modify (additive):**
- `app/db/models/__init__.py` — register `ActivityLog`.
- `app/api/v1/api.py:25` — include the dashboard router.
- `tests/factories.py` — add `create_activity`.
- `app/api/v1/endpoints/mission.py` (4 sites), `roadmap.py` (2 sites), `assessments.py` (1 site), `invitations.py` (1 site) — `write_activity` calls.
- `e2e/test_smoke.py:74` — add the two dashboard paths.
- `docs/checklist/PROJECT_CHECKLIST.md` — reconcile.

---

## Task 1: `activity_log` model + migration + `write_activity` helper + factory

**Files:**
- Create: `app/db/models/activity.py`, `app/platform/activity.py`, `alembic/versions/0010_dashboard.py`
- Modify: `app/db/models/__init__.py`, `tests/factories.py`
- Test: `tests/platform/test_activity.py`

**Interfaces:**
- Produces: `ActivityLog` model; `write_activity(db, *, startup_id, action, summary, actor_user_id=None, entity_type=None, entity_id=None, meta=None) -> ActivityLog`; `create_activity(db, *, startup, action="x", summary="y", actor=None, entity_type=None, entity_id=None, meta=None) -> ActivityLog`.

- [ ] **Step 1: Write the failing test** — `tests/platform/test_activity.py`

```python
import uuid
from app.db.models.activity import ActivityLog
from app.platform.activity import write_activity
from tests.factories import create_user, create_startup


def test_write_activity_persists_row_in_caller_transaction(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)

    row = write_activity(
        db,
        startup_id=startup.id,
        actor_user_id=owner.id,
        action="mission.task.completed",
        summary="Ada completed 'Draft pricing options'",
        entity_type="mission_task",
        entity_id=uuid.uuid4(),
        meta={"streak": 3},
    )

    assert row.id is not None
    fetched = db.query(ActivityLog).filter(ActivityLog.id == row.id).one()
    assert fetched.startup_id == startup.id
    assert fetched.actor_user_id == owner.id
    assert fetched.action == "mission.task.completed"
    assert fetched.summary == "Ada completed 'Draft pricing options'"
    assert fetched.entity_type == "mission_task"
    assert fetched.meta == {"streak": 3}
    assert fetched.created_at is not None


def test_write_activity_allows_null_actor_for_system_rows(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)
    row = write_activity(
        db, startup_id=startup.id, action="mission.generated", summary="Today's mission is ready"
    )
    assert row.actor_user_id is None
    assert row.entity_id is None
    assert row.meta is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/platform/test_activity.py -v`
Expected: FAIL — `ModuleNotFoundError: app.db.models.activity`.

- [ ] **Step 3: Create the model** — `app/db/models/activity.py`

```python
import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class ActivityLog(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "activity_log"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_activity_log_startup_created", "startup_id", "created_at", "id"),
    )
```

Confirm mixin/base import paths against `app/db/models/audit.py` (same `UUIDMixin, TimestampMixin, Base`). Then register in `app/db/models/__init__.py` — add `from app.db.models.activity import ActivityLog` alongside the other model imports and add `"ActivityLog"` to `__all__` if that file maintains one (match the file's existing pattern; check how `AuditLog` is registered).

- [ ] **Step 4: Create the helper** — `app/platform/activity.py` (mirror `app/platform/audit.py`)

```python
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.activity import ActivityLog


def write_activity(
    db: Session,
    *,
    startup_id: uuid.UUID,
    action: str,
    summary: str,
    actor_user_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    meta: dict[str, Any] | None = None,
) -> ActivityLog:
    row = ActivityLog(
        startup_id=startup_id,
        actor_user_id=actor_user_id,
        action=action,
        summary=summary,
        entity_type=entity_type,
        entity_id=entity_id,
        meta=meta,
    )
    db.add(row)
    db.flush()
    return row
```

- [ ] **Step 5: Create the migration** — `alembic/versions/0010_dashboard.py`

Copy the structure of `alembic/versions/0009_mission.py` (imports, `revision`/`down_revision` lines, `op.create_table`/`op.create_index`). Set:

```python
revision = "0010_dashboard"
down_revision = "0009_mission"
branch_labels = None
depends_on = None
```

`upgrade()` creates `activity_log` with columns matching the model (UUID `id` PK default, `startup_id` UUID NOT NULL FK→`startups.id` ON DELETE CASCADE, `actor_user_id` UUID NULL FK→`users.id`, `action` VARCHAR(80) NOT NULL, `entity_type` VARCHAR(40) NULL, `entity_id` UUID NULL, `summary` VARCHAR(300) NOT NULL, `meta` JSONB NULL, `created_at`/`updated_at` timestamptz NOT NULL matching the mixin) and the composite index `ix_activity_log_startup_created (startup_id, created_at, id)`. `downgrade()` drops the index then the table.

- [ ] **Step 6: Add the factory** — append to `tests/factories.py` (match the `create_mission` style at line 286)

```python
def create_activity(
    db: Session,
    *,
    startup: Startup,
    action: str = "mission.task.completed",
    summary: str = "Someone did a thing",
    actor: User | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    meta: dict | None = None,
) -> "ActivityLog":
    from app.db.models.activity import ActivityLog

    row = ActivityLog(
        startup_id=startup.id,
        actor_user_id=actor.id if actor else None,
        action=action,
        summary=summary,
        entity_type=entity_type,
        entity_id=entity_id,
        meta=meta,
    )
    db.add(row)
    db.flush()
    return row
```

- [ ] **Step 7: Run the model/helper tests + the migration round-trip**

Run: `poetry run pytest tests/platform/test_activity.py -v`
Expected: PASS.
Then verify the migration applies on a fresh DB and the head is single:
Run: `poetry run alembic upgrade head && poetry run alembic heads`
Expected: exactly `0010_dashboard (head)`; then `poetry run alembic downgrade -1 && poetry run alembic upgrade head` round-trips clean.

- [ ] **Step 8: Gates + commit**

Run: `poetry run black app tests && poetry run isort app tests && poetry run ruff check . && poetry run mypy app`
```bash
git add app/db/models/activity.py app/platform/activity.py app/db/models/__init__.py alembic/versions/0010_dashboard.py tests/platform/test_activity.py tests/factories.py
git commit -m "feat(dashboard): activity_log model + migration 0010 + write_activity helper + factory"
```

---

## Task 2: Wire `write_activity` into the 8 action sites

**Files:**
- Modify: `app/api/v1/endpoints/mission.py:161` (patch_task: `complete`/`snooze`/`reject` cases) + `:136` (create_task); `app/api/v1/endpoints/roadmap.py:466` (update_milestone_ep, inside `if now_done and not was_done`) + `:642` (replan apply); `app/api/v1/endpoints/assessments.py:100` (post_complete_assessment); `app/api/v1/endpoints/invitations.py:16` (accept)
- Test: `tests/api/test_activity_wiring.py`

**Interfaces:**
- Consumes: `write_activity` (Task 1). Each site already has `db`, the acting `user` (via `get_verified_user`), the `membership` (→ `startup_id`), and the mutated entity in scope.
- Produces: exactly one `activity_log` row per successful action, with the `action` strings: `mission.task.completed`, `mission.task.snoozed`, `mission.task.rejected`, `mission.task.added`, `roadmap.milestone.completed`, `roadmap.replanned`, `assessment.completed`, `member.joined`.

**Pattern (apply at every site):** call `write_activity(...)` *after* the service mutation succeeds and *before* the existing `db.commit()`, using data already loaded. `summary` embeds the actor's first name and the entity title. Actor first name is `user.first_name` if the codebase exposes it — otherwise derive from `user` per the existing convention (check `app/db/models/user.py` for the name field; use that field, do not invent one).

- [ ] **Step 1: Write the failing wiring tests** — `tests/api/test_activity_wiring.py`

Use the existing API test harness (TestClient + `X-Workspace-Id` header + verified founder) exactly as `tests/api/test_mission_tasks.py` sets it up (copy its fixtures/helpers). One representative test per site; here are the two that anchor the shape — write the analogous test for each of the 8 actions:

```python
from app.db.models.activity import ActivityLog


def test_completing_mission_task_writes_activity(client, db, founder_ctx):
    # founder_ctx: helper that returns (headers, startup, user) with a mission task todo
    task = founder_ctx.mission_task
    r = client.patch(
        f"/api/v1/missions/tasks/{task.id}",
        json={"action": "complete"},
        headers=founder_ctx.headers,
    )
    assert r.status_code == 200
    rows = (
        db.query(ActivityLog)
        .filter(ActivityLog.startup_id == founder_ctx.startup.id, ActivityLog.action == "mission.task.completed")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].actor_user_id == founder_ctx.user.id
    assert rows[0].entity_type == "mission_task"
    assert rows[0].entity_id == task.id
    assert task.title in rows[0].summary


def test_accepting_invitation_writes_member_joined_activity(client, db, invited_user_ctx):
    r = client.post("/api/v1/invitations/accept", json={"token": invited_user_ctx.token}, headers=invited_user_ctx.headers)
    assert r.status_code == 200
    rows = db.query(ActivityLog).filter(ActivityLog.action == "member.joined").all()
    assert len(rows) == 1
    assert rows[0].startup_id == invited_user_ctx.startup.id
    assert rows[0].actor_user_id == invited_user_ctx.user.id
```

(Match the actual fixtures your API tests use; the `founder_ctx`/`invited_user_ctx` names above stand in for whatever helper the suite already provides — reuse it, do not build a new harness.)

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/api/test_activity_wiring.py -v`
Expected: FAIL — no `activity_log` rows written yet.

- [ ] **Step 3: Wire mission task actions** — `app/api/v1/endpoints/mission.py`

In `patch_task`, inside each relevant `case`, after the service call:

```python
from app.platform.activity import write_activity  # add to imports

# case "complete":
complete_task(db, startup, task)
write_activity(
    db, startup_id=startup.id, actor_user_id=user.id,
    action="mission.task.completed", entity_type="mission_task", entity_id=task.id,
    summary=f"{user.first_name} completed '{task.title}'",
)
# case "snooze":  -> action="mission.task.snoozed", summary=f"{user.first_name} snoozed '{task.title}'"
# case "reject":  -> action="mission.task.rejected", summary=f"{user.first_name} rejected '{task.title}'"
```

Do **not** write activity for the `reorder` case (not team-meaningful). In `create_task`, after `add_custom_task(...)`:

```python
write_activity(
    db, startup_id=startup.id, actor_user_id=user.id,
    action="mission.task.added", entity_type="mission_task", entity_id=task.id,
    summary=f"{user.first_name} added '{task.title}' to today's mission",
)
```

Use the real name field from `app/db/models/user.py` (verify: it may be `first_name`, `name`, or `full_name` — use whichever exists).

- [ ] **Step 4: Wire roadmap sites** — `app/api/v1/endpoints/roadmap.py`

In `update_milestone_ep`, inside the existing `if now_done and not was_done:` block (right after the `event_bus.publish("roadmap.milestone.completed", …)`):

```python
write_activity(
    db, startup_id=membership.startup_id, actor_user_id=user.id,
    action="roadmap.milestone.completed", entity_type="roadmap_milestone", entity_id=m.id,
    summary=f"{user.first_name} completed milestone '{m.title}'",
)
```

At the replan-apply site (~`:642`, where `event_bus.publish("roadmap.replanned", …)` fires after `apply_replan`), add — using the applied count already in scope:

```python
write_activity(
    db, startup_id=membership.startup_id, actor_user_id=user.id,
    action="roadmap.replanned", entity_type="roadmap", entity_id=roadmap.id,
    summary=f"{user.first_name} applied a roadmap re-plan ({len(applied)} milestone(s) shifted)",
)
```

(Match the real variable names at that site — the applied list and roadmap object as they are named there.)

- [ ] **Step 5: Wire assessment + invitation** — `app/api/v1/endpoints/assessments.py` and `invitations.py`

`assessments.py` `post_complete_assessment`, after `result = complete_assessment(db, a, startup)`:

```python
write_activity(
    db, startup_id=startup.id, actor_user_id=user.id,
    action="assessment.completed", entity_type="assessment", entity_id=a.id,
    summary=f"{user.first_name} completed the startup assessment",
)
```

`invitations.py` `accept`, after `membership = accept_invitation(db, user, token)` (use the real returned variable) and before its `db.commit()`:

```python
write_activity(
    db, startup_id=membership.startup_id, actor_user_id=user.id,
    action="member.joined", entity_type="membership", entity_id=membership.id,
    summary=f"{user.first_name} joined the workspace",
)
```

- [ ] **Step 6: Run the wiring tests + full suite**

Run: `poetry run pytest tests/api/test_activity_wiring.py -v`
Expected: PASS (all 8).
Run: `poetry run pytest -q`
Expected: the full suite still green (no regression in the shipped modules' existing tests).

- [ ] **Step 7: Gates + commit**

Run: `poetry run black app tests && poetry run isort app tests && poetry run ruff check . && poetry run mypy app`
```bash
git add app/api/v1/endpoints/mission.py app/api/v1/endpoints/roadmap.py app/api/v1/endpoints/assessments.py app/api/v1/endpoints/invitations.py tests/api/test_activity_wiring.py
git commit -m "feat(dashboard): record team activity at mission/roadmap/assessment/invite action sites"
```

---

## Task 3: Dashboard aggregation service — `get_summary` + resilience

**Files:**
- Create: `app/services/dashboard/__init__.py`, `app/services/dashboard/service.py`
- Test: `tests/services/test_dashboard_summary.py`

**Interfaces:**
- Consumes: `health_score.service.get_overview(db, startup)`, `health_score.service.latest_completed_result(db, startup_id)`, `mission.service.get_or_generate_today` / `streak` / `serialize_mission`, roadmap models `Roadmap`/`RoadmapPhase`/`RoadmapMilestone` + `RoadmapStatus`, `mission` models `Mission`/`MissionTask` + `MissionTaskStatus`.
- Produces: `UPCOMING_WINDOW_DAYS = 7`; `get_summary(db, startup: Startup, user: User) -> dict` with the exact shape below.

**`get_summary` return shape (contract — Task 4 endpoint, Task 6 e2e, Task 7 FE guide all depend on it):**

```python
{
  "greeting": {"salutation": "Good morning|afternoon|evening", "first_name": str, "startup_name": str},
  "health": {...} | {"error": True},                  # health_score.get_overview
  "mission": {...} | None | {"error": True},          # serialize_mission or None (no roadmap)
  "upcoming": [{"id": str, "title": str, "due_on": "YYYY-MM-DD", "milestone_id": str}] | {"error": True},
  "kpis": {"tasks_done_this_week": int, "revenue": None, "runway": None,
           "pipeline_value": None, "campaign_performance": None} | {"error": True},
  "calibration": {"assessment_complete": bool},
  "briefing": {"status": "empty", "message": "I'll have your first briefing ready tomorrow morning once I've seen a full day of your workspace."},
  "risks": {"status": "empty", "message": "No open risks. I'm watching runway, deadlines, and pipeline for you."},
  "opportunities": {"status": "empty", "message": "Opportunities I spot — grants, quick wins, market signals — will show up here."},
}
```

- [ ] **Step 1: Write the failing tests** — `tests/services/test_dashboard_summary.py`

```python
from datetime import date, timedelta
from app.services.dashboard.service import get_summary, UPCOMING_WINDOW_DAYS
from tests.factories import (
    create_user, create_startup, create_roadmap, create_phase, create_milestone,
    create_mission, create_mission_task,
)


def test_summary_has_all_sections_and_greeting(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)
    out = get_summary(db, startup, owner)
    assert set(out) == {
        "greeting", "health", "mission", "upcoming", "kpis",
        "calibration", "briefing", "risks", "opportunities",
    }
    assert out["greeting"]["startup_name"] == startup.name
    assert out["briefing"]["status"] == "empty"
    assert out["calibration"]["assessment_complete"] is False


def test_upcoming_window_and_done_exclusion(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)
    roadmap = create_roadmap(db, startup=startup)
    phase = create_phase(db, roadmap=roadmap)
    today = date.today()
    inside = create_milestone(db, phase=phase, title="Inside", due_on=today + timedelta(days=3))
    create_milestone(db, phase=phase, title="TooFar", due_on=today + timedelta(days=30))
    create_milestone(db, phase=phase, title="Past", due_on=today - timedelta(days=1))
    titles = [u["title"] for u in get_summary(db, startup, owner)["upcoming"]]
    assert titles == ["Inside"]


def test_tasks_done_this_week_counts_only_recent_done(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)
    mission = create_mission(db, startup=startup, mission_date=date.today())
    create_mission_task(db, mission=mission, status="done")   # counts
    create_mission_task(db, mission=mission, status="todo")   # doesn't
    assert get_summary(db, startup, owner)["kpis"]["tasks_done_this_week"] >= 1


def test_a_failing_section_becomes_error_marker_not_a_raise(db, monkeypatch):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)

    def boom(*a, **k):
        raise RuntimeError("health exploded")

    monkeypatch.setattr("app.services.dashboard.service.get_overview", boom)
    out = get_summary(db, startup, owner)
    assert out["health"] == {"error": True}
    assert out["greeting"]["startup_name"] == startup.name  # rest still populated
```

(Confirm `create_milestone` accepts `due_on` and `create_mission_task` accepts `status`; adjust the kwargs to the factories' real signatures at `tests/factories.py:227` and `:305`.)

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/services/test_dashboard_summary.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the service** — `app/services/dashboard/service.py`

```python
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.enums import MissionTaskStatus, RoadmapStatus
from app.db.models.mission import Mission, MissionTask
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.db.models.startup import Startup
from app.db.models.user import User
from app.services.health_score.service import get_overview, latest_completed_result
from app.services.mission.service import get_or_generate_today, serialize_mission, streak

UPCOMING_WINDOW_DAYS = 7

_BRIEFING_EMPTY = "I'll have your first briefing ready tomorrow morning once I've seen a full day of your workspace."
_RISKS_EMPTY = "No open risks. I'm watching runway, deadlines, and pipeline for you."
_OPPS_EMPTY = "Opportunities I spot — grants, quick wins, market signals — will show up here."


def _salutation(now: datetime) -> str:
    h = now.hour
    if h < 12:
        return "Good morning"
    if h < 18:
        return "Good afternoon"
    return "Good evening"


def _section(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - per-card isolation: one failure must not 500 the page
        return {"error": True}


def _upcoming(db: Session, startup: Startup) -> list[dict[str, Any]]:
    today = date.today()
    rows = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(Roadmap.startup_id == startup.id)
        .filter(RoadmapMilestone.due_on >= today)
        .filter(RoadmapMilestone.due_on <= today + timedelta(days=UPCOMING_WINDOW_DAYS))
        .filter(RoadmapMilestone.status != RoadmapStatus.done)
        .order_by(RoadmapMilestone.due_on.asc())
        .all()
    )
    return [
        {"id": str(m.id), "title": m.title, "due_on": m.due_on.isoformat(), "milestone_id": str(m.id)}
        for m in rows
    ]


def _tasks_done_this_week(db: Session, startup: Startup) -> int:
    since = date.today() - timedelta(days=6)
    return (
        db.query(MissionTask)
        .join(Mission, MissionTask.mission_id == Mission.id)
        .filter(Mission.startup_id == startup.id)
        .filter(MissionTask.status == MissionTaskStatus.done)
        .filter(MissionTask.completed_at.isnot(None))
        .filter(MissionTask.completed_at >= datetime(since.year, since.month, since.day))
        .count()
    )


def _mission_section(db: Session, startup: Startup) -> Any:
    m = get_or_generate_today(db, startup)
    if m is None:
        return None
    return serialize_mission(db, m, streak(db, startup))


def get_summary(db: Session, startup: Startup, user: User) -> dict[str, Any]:
    name_field = getattr(user, "first_name", None) or getattr(user, "name", None) or ""
    return {
        "greeting": {
            "salutation": _salutation(datetime.now()),
            "first_name": name_field,
            "startup_name": startup.name,
        },
        "health": _section(lambda: get_overview(db, startup)),
        "mission": _section(lambda: _mission_section(db, startup)),
        "upcoming": _section(lambda: _upcoming(db, startup)),
        "kpis": _section(lambda: {
            "tasks_done_this_week": _tasks_done_this_week(db, startup),
            "revenue": None, "runway": None, "pipeline_value": None, "campaign_performance": None,
        }),
        "calibration": {"assessment_complete": latest_completed_result(db, startup.id) is not None},
        "briefing": {"status": "empty", "message": _BRIEFING_EMPTY},
        "risks": {"status": "empty", "message": _RISKS_EMPTY},
        "opportunities": {"status": "empty", "message": _OPPS_EMPTY},
    }
```

Verify the real field/enum names before running: `MissionTask.completed_at` and `MissionTaskStatus.done` (from `app/db/models/mission.py`), `RoadmapMilestone.due_on`/`.phase_id`/`.status` + `RoadmapPhase.roadmap_id` + `RoadmapStatus.done` (from `app/db/models/roadmap.py`), and `latest_completed_result`'s signature (`app/services/health_score/service.py:28`). `get_or_generate_today` commits nothing itself — the endpoint owns the commit.

Create `app/services/dashboard/__init__.py` (empty).

- [ ] **Step 4: Run the tests**

Run: `poetry run pytest tests/services/test_dashboard_summary.py -v`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

Run: `poetry run black app tests && poetry run isort app tests && poetry run ruff check . && poetry run mypy app`
```bash
git add app/services/dashboard/ tests/services/test_dashboard_summary.py
git commit -m "feat(dashboard): aggregation service — get_summary with per-section resilience"
```

---

## Task 4: `GET /dashboard/summary` endpoint

**Files:**
- Create: `app/api/v1/endpoints/dashboard.py`
- Modify: `app/api/v1/api.py:25`
- Test: `tests/api/test_dashboard_summary.py`

**Interfaces:**
- Consumes: `get_summary` (Task 3). Endpoint deps per Global Constraints.
- Produces: `GET /api/v1/dashboard/summary`.

- [ ] **Step 1: Write the failing test** — `tests/api/test_dashboard_summary.py`

Reuse the API harness (TestClient + verified founder + `X-Workspace-Id`) exactly as `tests/api/test_mission_today.py` (or the mission API tests) set it up.

```python
def test_summary_returns_all_sections(client, founder_ctx):
    r = client.get("/api/v1/dashboard/summary", headers=founder_ctx.headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert set(data) >= {"greeting", "health", "mission", "upcoming", "kpis", "calibration", "briefing"}
    assert data["greeting"]["startup_name"] == founder_ctx.startup.name


def test_summary_requires_membership(client, outsider_ctx):
    r = client.get("/api/v1/dashboard/summary", headers=outsider_ctx.headers)
    assert r.status_code == 403


def test_summary_requires_verified_email(client, unverified_ctx):
    r = client.get("/api/v1/dashboard/summary", headers=unverified_ctx.headers)
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify it fails** — Run: `poetry run pytest tests/api/test_dashboard_summary.py -v` → FAIL (404, route missing).

- [ ] **Step 3: Implement** — `app/api/v1/endpoints/dashboard.py`

```python
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_workspace
from app.services.dashboard.service import get_summary

router = APIRouter()


def _startup(db: Session, membership: Membership) -> Startup:
    return db.query(Startup).filter(Startup.id == membership.startup_id).one()


@router.get("/summary")
def dashboard_summary(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    data = get_summary(db, startup, user)
    db.commit()  # get_or_generate_today may have lazily created today's mission
    return success_response(data)
```

(Copy the exact `_startup` helper from `mission.py` if it differs.) Register in `app/api/v1/api.py`: add `dashboard` to the endpoint imports and, after line 25, `api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])`.

- [ ] **Step 4: Run** — `poetry run pytest tests/api/test_dashboard_summary.py -v` → PASS.

- [ ] **Step 5: Gates + commit**

```bash
git add app/api/v1/endpoints/dashboard.py app/api/v1/api.py tests/api/test_dashboard_summary.py
git commit -m "feat(dashboard): GET /dashboard/summary"
```

---

## Task 5: `get_activity` keyset pagination + `GET /dashboard/activity`

**Files:**
- Modify: `app/services/dashboard/service.py` (add `get_activity`), `app/api/v1/endpoints/dashboard.py` (add route)
- Test: `tests/api/test_dashboard_activity.py`

**Interfaces:**
- Consumes: `ActivityLog` (Task 1), `write_activity`-written rows.
- Produces: `get_activity(db, startup, cursor: str | None = None, limit: int = 20) -> dict` returning `{"items": [...], "next_cursor": str | None}`; `GET /api/v1/dashboard/activity?cursor=&limit=`.

**Item shape:** `{"id","action","entity_type","entity_id","summary","meta","actor": {"id","name"} | None, "created_at": ISO8601}`. Ordered `(created_at DESC, id DESC)`. `actor` resolved by one `LEFT JOIN users` (no N+1). Cursor = base64 of `f"{created_at.isoformat()}|{id}"`.

- [ ] **Step 1: Write the failing test** — `tests/api/test_dashboard_activity.py`

```python
from tests.factories import create_activity


def test_activity_newest_first_and_paginates(client, db, founder_ctx):
    for i in range(3):
        create_activity(db, startup=founder_ctx.startup, actor=founder_ctx.user,
                        action="mission.task.completed", summary=f"did thing {i}")
    db.flush()
    r1 = client.get("/api/v1/dashboard/activity?limit=2", headers=founder_ctx.headers)
    assert r1.status_code == 200
    body1 = r1.json()["data"]
    assert len(body1["items"]) == 2
    assert body1["next_cursor"] is not None
    assert body1["items"][0]["actor"]["id"] == str(founder_ctx.user.id)

    r2 = client.get(f"/api/v1/dashboard/activity?cursor={body1['next_cursor']}", headers=founder_ctx.headers)
    body2 = r2.json()["data"]
    ids1 = {i["id"] for i in body1["items"]}
    ids2 = {i["id"] for i in body2["items"]}
    assert ids1.isdisjoint(ids2)   # pages don't overlap


def test_activity_empty_workspace(client, founder_ctx):
    r = client.get("/api/v1/dashboard/activity", headers=founder_ctx.headers)
    assert r.json()["data"] == {"items": [], "next_cursor": None}


def test_activity_never_leaks_across_tenants(client, db, founder_ctx, other_startup):
    create_activity(db, startup=other_startup, action="x", summary="theirs")
    db.flush()
    r = client.get("/api/v1/dashboard/activity", headers=founder_ctx.headers)
    assert r.json()["data"]["items"] == []
```

- [ ] **Step 2: Run to verify it fails** — `poetry run pytest tests/api/test_dashboard_activity.py -v` → FAIL.

- [ ] **Step 3: Implement `get_activity`** — append to `app/services/dashboard/service.py`

```python
import base64
from app.db.models.activity import ActivityLog


def _encode_cursor(created_at: datetime, row_id: Any) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{row_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    ts, row_id = raw.split("|", 1)
    return datetime.fromisoformat(ts), row_id


def get_activity(db: Session, startup: Startup, cursor: str | None = None, limit: int = 20) -> dict[str, Any]:
    limit = max(1, min(limit, 50))
    q = (
        db.query(ActivityLog, User)
        .outerjoin(User, ActivityLog.actor_user_id == User.id)
        .filter(ActivityLog.startup_id == startup.id)
        .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
    )
    if cursor:
        c_ts, c_id = _decode_cursor(cursor)
        q = q.filter(
            (ActivityLog.created_at < c_ts)
            | ((ActivityLog.created_at == c_ts) & (ActivityLog.id < c_id))
        )
    rows = q.limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = []
    for row, actor in rows:
        name = None
        if actor is not None:
            name = getattr(actor, "first_name", None) or getattr(actor, "name", None)
        items.append({
            "id": str(row.id),
            "action": row.action,
            "entity_type": row.entity_type,
            "entity_id": str(row.entity_id) if row.entity_id else None,
            "summary": row.summary,
            "meta": row.meta,
            "actor": ({"id": str(actor.id), "name": name} if actor else None),
            "created_at": row.created_at.isoformat(),
        })
    next_cursor = None
    if has_more and rows:
        last, _ = rows[-1]
        next_cursor = _encode_cursor(last.created_at, last.id)
    return {"items": items, "next_cursor": next_cursor}
```

Add `User` to the module imports. The keyset predicate compares `id` lexically against the decoded string; if `ActivityLog.id` is UUID, cast consistently — verify the comparison works against the UUID column (wrap `c_id` as `uuid.UUID(c_id)` if the column is typed UUID) and adjust so the test passes.

- [ ] **Step 4: Add the route** — `app/api/v1/endpoints/dashboard.py`

```python
from fastapi import Query
from app.services.dashboard.service import get_activity

@router.get("/activity")
def dashboard_activity(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    return success_response(get_activity(db, startup, cursor=cursor, limit=limit))
```

- [ ] **Step 5: Run** — `poetry run pytest tests/api/test_dashboard_activity.py -v` → PASS. Then `poetry run pytest -q` → full suite green.

- [ ] **Step 6: Gates + commit**

```bash
git add app/services/dashboard/service.py app/api/v1/endpoints/dashboard.py tests/api/test_dashboard_activity.py
git commit -m "feat(dashboard): GET /dashboard/activity — keyset-paginated team feed"
```

---

## Task 6: Live E2E + smoke surface

**Files:**
- Create: `e2e/test_dashboard.py`
- Modify: `e2e/test_smoke.py:74`
- Captures: `e2e/_captures/dashboard/`

**Interfaces:**
- Consumes: the running app; reuse the onboarding/roadmap/mission/assessment E2E helpers from `e2e/test_roadmap.py`, `e2e/test_mission.py`, `e2e/conftest.py` (the `capture` helper + `_onboard_steps`/`_wh` pattern).

- [ ] **Step 1: Write the E2E journey** — `e2e/test_dashboard.py`

Mirror `e2e/test_mission.py`'s structure exactly (same fixtures, same `capture` helper writing to `e2e/_captures/<module>/`). Journey: onboard a founder → complete the assessment → roadmap generates → `GET /dashboard/summary` (assert `health` populated, `mission` has ≤3 tasks, `calibration.assessment_complete is True`, `briefing.status == "empty"`; capture as `summary.json`) → `GET /dashboard/settings`? (no — none) → complete one mission task via `PATCH /missions/tasks/{id}` → `GET /dashboard/activity` (assert the top item `action == "mission.task.completed"` and its `actor.id` is the founder; capture as `activity.json`). Capture every response body verbatim.

- [ ] **Step 2: Extend smoke** — `e2e/test_smoke.py`, add to the asserted paths list (line ~74):

```python
        "/api/v1/dashboard/summary",
        "/api/v1/dashboard/activity",
```

- [ ] **Step 3: Run E2E + unit**

Run: `COMPOSE_PROJECT_NAME=cofoundaz-api make e2e` (or `COMPOSE_PROJECT_NAME=cofoundaz-api ./scripts/e2e_run.sh` — check the Makefile). Docker must be up; the script migrates the `cofoundaz_e2e` DB (now through `0010`).
Expected: E2E passes; capture files written under `e2e/_captures/dashboard/`.
Run: `poetry run pytest -q` → full unit suite green.

- [ ] **Step 4: Gates + commit** (commit ONLY the dashboard captures + the two test files; restore any other module's regenerated captures)

```bash
git add e2e/test_dashboard.py e2e/test_smoke.py e2e/_captures/dashboard/
git commit -m "test(dashboard): live E2E journey + smoke surface + captures"
```

---

## Task 7: SOP + FE integration guide + checklist reconcile

**Files:**
- Create: `docs/sop/2026-08-31-dashboard.md`, `docs/fe-integration-guide-dashboard.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: SOP** — `docs/sop/2026-08-31-dashboard.md`, matching the existing SOP style (see `docs/sop/2026-08-26-todays-mission.md`): what shipped (+ commit refs), why (founder home screen aggregating shipped modules), how (aggregation BFF + `write_activity` mirroring `write_audit`, per-section resilience, honest empty-states), what's involved (files/migration `0010`/the 8 activity sites with paths), verification (unit + e2e counts, gates), operate/rollback (`alembic downgrade -1`), follow-ups (briefing→03, financial KPIs→09–11, realtime→20, widget-grant filtering).

- [ ] **Step 2: FE integration guide** — `docs/fe-integration-guide-dashboard.md`, matching `docs/fe-integration-guide-mission.md`. **Every payload copied verbatim from `e2e/_captures/dashboard/`.** Cover `GET /dashboard/summary` (the full shape; call out which fields are `null`/empty-state in v1 and **why** — briefing/risks/opps → Module 03, financial KPIs → 09–11 — so the FE builds the real widgets now) and `GET /dashboard/activity` (item shape + the `actor: {id,name}|null` trap + the cursor contract). End with a verification table marking each behaviour verified-live (cite the capture) or derived-from-code.

- [ ] **Step 3: Checklist** — add a `## ✅ Module 02 — Founder Dashboard` section to `docs/checklist/PROJECT_CHECKLIST.md`, check off its features, mark it **shipped on branch `feat/dashboard` (not yet merged to `main`)**, and update the Snapshot tally (6 modules complete-or-shipped) + the `_Last reconciled_` line. Be honest about deferred items.

- [ ] **Step 4: Final gates + commit**

Run: `poetry run pytest -q && poetry run black --check app tests && poetry run isort --check app tests && poetry run ruff check . && poetry run mypy app`
Expected: all green (state the numbers). `make e2e` was verified in Task 6 — no need to re-run for docs.
```bash
git add docs/sop/2026-08-31-dashboard.md docs/fe-integration-guide-dashboard.md docs/checklist/PROJECT_CHECKLIST.md
git commit -m "docs(dashboard): SOP + FE integration guide + checklist reconcile"
```

---

## Self-Review notes

- **Spec coverage:** §1 scope → Tasks 3–5 (summary/activity) + empty-states baked into `get_summary`; §2 decisions → Tasks 1 (activity_log + helper) & 3 (aggregate); §3 data model → Task 1; §4 write_activity + 8 sites → Tasks 1 & 2; §5 service → Tasks 3 & 5; §6 endpoints → Tasks 4 & 5; §7 events/errors/config → Tasks 3–5 (per-section resilience, no new codes, `UPCOMING_WINDOW_DAYS`); §8 testing → every task's tests + Task 6 e2e + FE guide in Task 7; §9 plan shape → these 7 tasks.
- **Names/types consistent across tasks:** `ActivityLog`, `write_activity(...)`, `create_activity(...)`, `get_summary(db, startup, user) -> dict`, `get_activity(db, startup, cursor, limit) -> dict`, `UPCOMING_WINDOW_DAYS = 7`, migration `0010_dashboard` ← `0009_mission`, router `prefix="/dashboard"`.
- **Verify-before-coding flags for implementers:** the User name field (`first_name` vs `name`), `MissionTask.completed_at`/`MissionTaskStatus.done`, roadmap `due_on`/`phase_id`/`status` + `RoadmapStatus.done`, `latest_completed_result` signature, and the UUID cursor comparison in `get_activity` are all called out inline — confirm each against the real source before implementing, do not assume.
- **The only stable-code edits** are the 8 additive `write_activity` calls in Task 2; everything else is new files. The full suite is run at the end of Tasks 2, 5, and 7 to guard the shipped modules.

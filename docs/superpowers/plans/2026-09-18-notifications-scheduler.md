# Module 20 Slice 3 — Scheduler / Cron Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A throttled scheduler tick inside the existing worker that fires three time-based triggers — daily mission generation (~06:00), roadmap-milestone overdue nudges, quarterly re-assessment reminders — once per period, by enqueuing jobs the worker's handlers then run and notify from.

**Architecture:** No new process. `main_loop` calls `scheduler_tick(db, now)` at most once per `SCHEDULER_INTERVAL`. The tick runs three detectors, and for each due `(task, scope, period)` **claims** it via a unique `scheduled_runs` row (leader-free once-per-period safety) and enqueues a job. The worker's `run_once` later runs the handler, which does the work and publishes a notification event the Slice-1/2 registry fans out (in-app + opt-in email).

**Tech Stack:** Python 3.11 / SQLAlchemy 2.0 (typed `Mapped`) / Alembic / PostgreSQL / `zoneinfo` (stdlib) / pytest (real Postgres, per-test savepoint rollback).

**Spec:** `docs/superpowers/specs/2026-09-18-notifications-scheduler-design.md`

## Global Constraints

- **Same-transaction rule:** job handlers and the registry publish inside the worker's per-job savepoint / the tick's transaction — **no `db.commit()`/`rollback()` in handlers**. The tick commits once at its end.
- **Claim = idempotency:** the unique `(task_key, scope_key, period_key)` on `scheduled_runs` is the only thing preventing a double-fire; `_claim` inserts inside `db.begin_nested()` and returns `False` on `IntegrityError`.
- **Injected time:** `scheduler_tick` and every detector take an explicit `now: datetime` (UTC-aware) so tests drive time deterministically.
- **Timezone:** the 06:00 check and daily/quarterly period keys use `zoneinfo.ZoneInfo(settings.SCHEDULER_TIMEZONE)`; `now.astimezone(tz)`.
- **Enum values:** `RoadmapStatus.done`, `AssessmentStatus.completed`/`in_progress`. FK columns get standalone `index=True`. Enum DB columns (none new here) use `Enum(..., native_enum=False)`.
- **Migrations:** one revision, chained on the current develop head (settle the number at build time; single alembic head).
- **No AI attribution** in any commit/PR/issue body.
- **CI locally before push:** black/isort/ruff/mypy/pylint(≥9.5)/bandit + pytest (cov ≥95) + alembic single-head/round-trip + e2e, via `poetry`.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/db/models/scheduled_run.py` | `ScheduledRun` ledger model |
| `app/db/models/__init__.py` (modify) | import the new model |
| `alembic/versions/00NN_scheduled_runs.py` | migration (number at build time) |
| `app/worker/scheduler.py` | `Due` tuple, `_claim`, three detectors, `scheduler_tick` |
| `app/worker/handlers/scheduled.py` | three job handlers + `register_handler` |
| `app/worker/__main__.py` (modify) | throttled `scheduler_tick` in `main_loop`; import scheduled handlers |
| `app/services/notifications/registry.py` (modify) | three new `SPECS` rows |
| `app/services/notifications/categories.py` (modify) | three new `EVENT_CATEGORY` mappings |
| `app/core/config.py` (modify) | `SCHEDULER_*` settings |
| `tests/worker/test_scheduler.py`, `tests/worker/test_scheduled_handlers.py`, `tests/services/notifications/test_scheduled_events.py`, `tests/worker/test_entrypoint.py` (modify) | tests |
| `e2e/test_notifications_scheduler.py` | live e2e |
| docs (`fe-integration-guide-notifications.md`, `sop/`, checklist) | docs |

---

## Task 1: `scheduled_runs` ledger + migration + `_claim`

**Files:**
- Create: `app/db/models/scheduled_run.py`, `app/worker/scheduler.py` (only `_claim` this task)
- Modify: `app/db/models/__init__.py`
- Create: `alembic/versions/00NN_scheduled_runs.py`
- Test: `tests/worker/test_scheduler.py`

**Interfaces:**
- Produces: `ScheduledRun(task_key: str, scope_key: str, period_key: str)`; `_claim(db, task_key: str, scope_key: str, period_key: str) -> bool` (True = newly claimed, False = already claimed).

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_scheduler.py
from app.db.models.scheduled_run import ScheduledRun
from app.worker.scheduler import _claim


def test_claim_is_once_per_period(db):
    assert _claim(db, "mission.generate", "ws1", "2026-09-18") is True
    # same triple → already claimed
    assert _claim(db, "mission.generate", "ws1", "2026-09-18") is False
    # different period → claimable
    assert _claim(db, "mission.generate", "ws1", "2026-09-19") is True
    assert db.query(ScheduledRun).filter_by(task_key="mission.generate", scope_key="ws1").count() == 2
```

- [ ] **Step 2: Run and verify it fails**

Run: `poetry run pytest tests/worker/test_scheduler.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Create the model**

```python
# app/db/models/scheduled_run.py
from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class ScheduledRun(UUIDMixin, TimestampMixin, Base):
    """Append-only ledger: 'this scheduled task fired for this scope in this period'.

    The unique (task_key, scope_key, period_key) is the whole once-per-period guard —
    the first worker to insert wins; a concurrent/repeat insert raises IntegrityError.
    scope_key is a generic string (a workspace id, or a milestone id for overdue), so
    there is no FK.
    """

    __tablename__ = "scheduled_runs"

    task_key: Mapped[str] = mapped_column(String(60), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    period_key: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("task_key", "scope_key", "period_key", name="uq_scheduled_runs_task_scope_period"),
    )
```

Add to `app/db/models/__init__.py` (mirror existing lines):

```python
from app.db.models.scheduled_run import ScheduledRun  # noqa: F401
```

- [ ] **Step 4: Implement `_claim`**

```python
# app/worker/scheduler.py
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.scheduled_run import ScheduledRun


def _claim(db: Session, task_key: str, scope_key: str, period_key: str) -> bool:
    """Claim (task, scope, period) exactly once. True if newly claimed, False if already taken.

    Inserts inside a SAVEPOINT so an IntegrityError (someone else claimed it) rolls back only
    this insert and leaves the caller's transaction usable.
    """
    try:
        with db.begin_nested():
            db.add(ScheduledRun(task_key=task_key, scope_key=scope_key, period_key=period_key))
            db.flush()
        return True
    except IntegrityError:
        return False
```

- [ ] **Step 5: Generate + finish the migration**

Run: `poetry run alembic revision --autogenerate -m "scheduled_runs ledger"`
Set `down_revision` to the current develop head, name the revision/file `00NN_scheduled_runs` (next free number). Verify `upgrade()` creates `scheduled_runs` with the unique constraint `uq_scheduled_runs_task_scope_period`; `downgrade()` drops the table.

- [ ] **Step 6: Run + migration round-trip**

Run:
```
poetry run pytest tests/worker/test_scheduler.py -v
poetry run alembic heads    # exactly one
poetry run alembic upgrade head && poetry run alembic check && poetry run alembic downgrade -1 && poetry run alembic upgrade head
```
Expected: PASS; one head; no drift.

- [ ] **Step 7: Commit**

```bash
git add app/db/models/scheduled_run.py app/db/models/__init__.py app/worker/scheduler.py alembic/versions/00NN_scheduled_runs.py tests/worker/test_scheduler.py
git commit -m "feat(scheduler): scheduled_runs ledger + once-per-period _claim"
```

---

## Task 2: Config + the three detectors

**Files:**
- Modify: `app/core/config.py`, `app/worker/scheduler.py`
- Test: `tests/worker/test_scheduler.py`

**Interfaces:**
- Consumes: `_claim` (Task 1); models `Startup`, `Membership`, `Roadmap`, `RoadmapPhase`, `RoadmapMilestone`, `Assessment`; enums `MembershipStatus.active`, `RoadmapStatus.done`, `AssessmentStatus.completed`/`in_progress`.
- Produces: `class Due(NamedTuple)`; `_due_missions(db, now) -> list[Due]`, `_due_overdue_milestones(db, now) -> list[Due]`, `_due_quarterly(db, now) -> list[Due]`; job-type constants `SCHED_MISSION`, `SCHED_OVERDUE`, `SCHED_QUARTERLY`.

- [ ] **Step 1: Add config**

```python
# app/core/config.py — near the WORKER_* block
    SCHEDULER_TIMEZONE: str = "UTC"          # IANA name for the 06:00 check
    MISSION_GEN_HOUR: int = 6                 # local hour to pre-generate today's mission
    SCHEDULER_INTERVAL: int = 60             # seconds; throttle the scheduler within the worker poll
    QUARTERLY_REASSESS_DAYS: int = 90
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/worker/test_scheduler.py — add
import uuid
from datetime import UTC, datetime, timedelta

from app.db.models.enums import AssessmentStatus, AssessmentType, RoadmapStatus
from app.worker import scheduler
from tests.factories import (
    create_assessment, create_membership, create_milestone, create_phase,
    create_roadmap, create_startup, create_user,
)


def _ws(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    return u, s


def test_due_missions_gated_by_hour(db):
    _u, s = _ws(db)
    before = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)   # 05:00 UTC < MISSION_GEN_HOUR 6
    after = datetime(2026, 9, 18, 6, 30, tzinfo=UTC)
    assert scheduler._due_missions(db, before) == []
    due = scheduler._due_missions(db, after)
    assert [d.scope_key for d in due] == [str(s.id)]
    assert due[0].period_key == "2026-09-18" and due[0].task_key == "mission.generate"


def test_due_overdue_milestones(db):
    _u, s = _ws(db)
    r = create_roadmap(db, startup=s)
    p = create_phase(db, roadmap=r)
    overdue = create_milestone(db, phase=p, due_on=datetime(2026, 9, 1).date(), status=RoadmapStatus.todo)
    create_milestone(db, phase=p, due_on=datetime(2026, 12, 1).date(), status=RoadmapStatus.todo)  # future
    create_milestone(db, phase=p, due_on=datetime(2026, 9, 1).date(), status=RoadmapStatus.done)   # done
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    due = scheduler._due_overdue_milestones(db, now)
    assert [d.scope_key for d in due] == [str(overdue.id)]
    assert due[0].period_key == "once" and due[0].payload["milestone_id"] == str(overdue.id)


def test_due_quarterly(db):
    _u, s = _ws(db)
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    create_assessment(db, startup=s, type=AssessmentType.initial, status=AssessmentStatus.completed,
                      completed_at=now - timedelta(days=100))
    due = scheduler._due_quarterly(db, now)
    assert [d.scope_key for d in due] == [str(s.id)]
    assert due[0].period_key == "2026-Q3"

    # a fresh assessment (30 days ago) is NOT due
    _u2, s2 = _ws(db)
    create_assessment(db, startup=s2, type=AssessmentType.initial, status=AssessmentStatus.completed,
                      completed_at=now - timedelta(days=30))
    assert str(s2.id) not in {d.scope_key for d in scheduler._due_quarterly(db, now)}
```

- [ ] **Step 3: Run and verify it fails**

Run: `poetry run pytest tests/worker/test_scheduler.py -v`
Expected: FAIL — detectors missing.

- [ ] **Step 4: Implement the detectors**

```python
# app/worker/scheduler.py — add
from datetime import datetime, timedelta
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import func

from app.core.config import settings
from app.db.models.assessment import Assessment
from app.db.models.enums import AssessmentStatus, MembershipStatus, RoadmapStatus
from app.db.models.membership import Membership
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.db.models.startup import Startup

SCHED_MISSION = "scheduled.mission.generate"
SCHED_OVERDUE = "scheduled.roadmap.overdue"
SCHED_QUARTERLY = "scheduled.assessment.quarterly"


class Due(NamedTuple):
    task_key: str
    scope_key: str
    period_key: str
    job_type: str
    startup_id: Any
    payload: dict


def _local(now: datetime) -> datetime:
    return now.astimezone(ZoneInfo(settings.SCHEDULER_TIMEZONE))


def _quarter_key(d: datetime) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def _active_startup_ids(db):
    rows = (
        db.query(Startup.id)
        .join(Membership, Membership.startup_id == Startup.id)
        .filter(Membership.status == MembershipStatus.active)
        .distinct()
        .all()
    )
    return [r[0] for r in rows]


def _due_missions(db, now: datetime) -> list[Due]:
    local = _local(now)
    if local.hour < settings.MISSION_GEN_HOUR:
        return []
    period = local.date().isoformat()
    return [
        Due("mission.generate", str(sid), period, SCHED_MISSION, sid, {"startup_id": str(sid)})
        for sid in _active_startup_ids(db)
    ]


def _due_overdue_milestones(db, now: datetime) -> list[Due]:
    today = _local(now).date()
    rows = (
        db.query(RoadmapMilestone.id, Roadmap.startup_id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(RoadmapMilestone.due_on < today, RoadmapMilestone.status != RoadmapStatus.done)
        .all()
    )
    return [
        Due("roadmap.overdue", str(mid), "once", SCHED_OVERDUE, sid,
            {"startup_id": str(sid), "milestone_id": str(mid)})
        for mid, sid in rows
    ]


def _due_quarterly(db, now: datetime) -> list[Due]:
    period = _quarter_key(_local(now))
    cutoff = now - timedelta(days=settings.QUARTERLY_REASSESS_DAYS)
    latest = (
        db.query(Assessment.startup_id, func.max(Assessment.completed_at).label("last"))
        .filter(Assessment.status == AssessmentStatus.completed)
        .group_by(Assessment.startup_id)
        .subquery()
    )
    due_ids = [r[0] for r in db.query(latest.c.startup_id).filter(latest.c.last <= cutoff).all()]
    in_progress = {
        r[0]
        for r in db.query(Assessment.startup_id)
        .filter(Assessment.status == AssessmentStatus.in_progress)
        .all()
    }
    return [
        Due("assessment.quarterly", str(sid), period, SCHED_QUARTERLY, sid, {"startup_id": str(sid)})
        for sid in due_ids
        if sid not in in_progress
    ]
```

- [ ] **Step 5: Run + verify pass**

Run: `poetry run pytest tests/worker/test_scheduler.py -v`
Expected: PASS. (If a factory kwarg differs — e.g. `create_milestone(due_on=...)` — check `tests/factories.py` and adjust the test's kwargs to match the real signature.)

- [ ] **Step 6: Commit**

```bash
git add app/core/config.py app/worker/scheduler.py tests/worker/test_scheduler.py
git commit -m "feat(scheduler): SCHEDULER_* config + mission/overdue/quarterly detectors"
```

---

## Task 3: `scheduler_tick` (claim + enqueue)

**Files:**
- Modify: `app/worker/scheduler.py`
- Test: `tests/worker/test_scheduler.py`

**Interfaces:**
- Consumes: `_claim`, the three detectors, `Due` (Task 1–2); `job_dispatcher.enqueue(db, type, payload, startup_id)`.
- Produces: `scheduler_tick(db, *, now: datetime) -> int` (number of jobs enqueued).

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_scheduler.py — add
from datetime import UTC, datetime

from app.db.models.job import Job
from app.worker.scheduler import scheduler_tick


def test_tick_enqueues_due_jobs_once(db):
    _u, s = _ws(db)
    now = datetime(2026, 9, 18, 7, 0, tzinfo=UTC)  # past 06:00
    n = scheduler_tick(db, now=now)
    jobs = db.query(Job).filter(Job.type == "scheduled.mission.generate").all()
    assert n >= 1 and len(jobs) == 1 and jobs[0].payload["startup_id"] == str(s.id)
    # a second tick the same period enqueues nothing new
    assert scheduler_tick(db, now=now) == 0
    assert db.query(Job).filter(Job.type == "scheduled.mission.generate").count() == 1
```

- [ ] **Step 2: Run and verify it fails**

Run: `poetry run pytest tests/worker/test_scheduler.py::test_tick_enqueues_due_jobs_once -v`
Expected: FAIL — `scheduler_tick` missing.

- [ ] **Step 3: Implement `scheduler_tick`**

```python
# app/worker/scheduler.py — add
from app.core.logger import log
from app.platform.jobs import job_dispatcher


def scheduler_tick(db, *, now: datetime) -> int:
    """Claim + enqueue every due scheduled task. Returns the count enqueued.

    Each (task, scope) is isolated: a lost claim (already fired this period) is a normal skip;
    any other per-item error is logged and the rest proceed. Commits once at the end.
    """
    due = [*_due_missions(db, now), *_due_overdue_milestones(db, now), *_due_quarterly(db, now)]
    enqueued = 0
    for item in due:
        try:
            if _claim(db, item.task_key, item.scope_key, item.period_key):
                job_dispatcher.enqueue(db, item.job_type, item.payload, item.startup_id)
                enqueued += 1
        except Exception as exc:  # noqa: BLE001 - one bad item must not stop the rest
            log.warning(f"[scheduler] {item.task_key}/{item.scope_key} failed: {exc}")
    db.commit()
    return enqueued
```

- [ ] **Step 4: Run + verify pass**

Run: `poetry run pytest tests/worker/test_scheduler.py -v`
Expected: PASS.

> Note on the test `db` fixture: it binds `join_transaction_mode="create_savepoint"`, so `scheduler_tick`'s `db.commit()` commits into the test's outer savepoint (observable, rolled back at teardown) and `_claim`'s `begin_nested` nests correctly — the same pattern the Slice-2 worker tests use.

- [ ] **Step 5: Commit**

```bash
git add app/worker/scheduler.py tests/worker/test_scheduler.py
git commit -m "feat(scheduler): scheduler_tick claims + enqueues due jobs"
```

---

## Task 4: The three job handlers

**Files:**
- Create: `app/worker/handlers/scheduled.py`
- Test: `tests/worker/test_scheduled_handlers.py`

**Interfaces:**
- Consumes: `SCHED_MISSION`/`SCHED_OVERDUE`/`SCHED_QUARTERLY` (Task 2); `get_or_generate_today(db, startup) -> Mission | None`; `event_bus.publish(db, event, payload)`; `register_handler(type, handler)`; models `Startup`, `RoadmapMilestone`.
- Produces: `handle_mission_generate(db, job)`, `handle_roadmap_overdue(db, job)`, `handle_assessment_quarterly(db, job)`; publishes events `mission.ready`, `roadmap.milestone.overdue`, `assessment.quarterly.due`.

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_scheduled_handlers.py
import uuid
from datetime import UTC, datetime

from app.db.models.enums import RoadmapStatus
from app.db.models.job import Job, JobStatus  # JobStatus from app.db.models.enums if not re-exported
from app.platform import events as events_mod
from app.worker.handlers import scheduled
from tests.factories import (
    create_membership, create_milestone, create_phase, create_roadmap, create_startup, create_user,
)


def _job(job_type, payload):
    return Job(type=job_type, payload=payload, status=JobStatus.running)


def test_mission_generate_publishes_when_a_mission_results(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish",
                        lambda d, e, p: published.append((e, p)))
    u = create_user(db); s = create_startup(db, owner=u); create_membership(db, u, s)
    r = create_roadmap(db, startup=s); p = create_phase(db, roadmap=r)
    create_milestone(db, phase=p)  # gives the roadmap content so a mission can generate
    scheduled.handle_mission_generate(db, _job("scheduled.mission.generate", {"startup_id": str(s.id)}))
    assert any(e == "mission.ready" for e, _ in published)


def test_overdue_publishes_only_if_still_overdue(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u = create_user(db); s = create_startup(db, owner=u)
    r = create_roadmap(db, startup=s); p = create_phase(db, roadmap=r)
    m = create_milestone(db, phase=p, due_on=datetime(2026, 9, 1).date(), status=RoadmapStatus.todo)
    scheduled.handle_roadmap_overdue(
        db, _job("scheduled.roadmap.overdue", {"startup_id": str(s.id), "milestone_id": str(m.id)}))
    assert ("roadmap.milestone.overdue", {"startup_id": str(s.id), "milestone_id": str(m.id)}) in published


def test_quarterly_publishes(db, monkeypatch):
    published = []
    monkeypatch.setattr(events_mod.event_bus, "publish", lambda d, e, p: published.append((e, p)))
    u = create_user(db); s = create_startup(db, owner=u)
    scheduled.handle_assessment_quarterly(db, _job("scheduled.assessment.quarterly", {"startup_id": str(s.id)}))
    assert ("assessment.quarterly.due", {"startup_id": str(s.id)}) in published


def test_handlers_registered():
    from app.worker import runner
    assert "scheduled.mission.generate" in runner.JOB_HANDLERS
    assert "scheduled.roadmap.overdue" in runner.JOB_HANDLERS
    assert "scheduled.assessment.quarterly" in runner.JOB_HANDLERS
```

- [ ] **Step 2: Run and verify it fails**

Run: `poetry run pytest tests/worker/test_scheduled_handlers.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the handlers**

```python
# app/worker/handlers/scheduled.py
import uuid

from sqlalchemy.orm import Session

from app.db.models.enums import RoadmapStatus
from app.db.models.job import Job
from app.db.models.roadmap import RoadmapMilestone
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.mission.service import get_or_generate_today
from app.worker.runner import register_handler
from app.worker.scheduler import SCHED_MISSION, SCHED_OVERDUE, SCHED_QUARTERLY


def handle_mission_generate(db: Session, job: Job) -> None:
    startup = db.get(Startup, uuid.UUID(str(job.payload["startup_id"])))
    if startup is None:
        return
    mission = get_or_generate_today(db, startup)
    if mission is not None:
        event_bus.publish(
            db, "mission.ready", {"startup_id": str(startup.id), "mission_id": str(mission.id)}
        )


def handle_roadmap_overdue(db: Session, job: Job) -> None:
    milestone = db.get(RoadmapMilestone, uuid.UUID(str(job.payload["milestone_id"])))
    # re-check: it may have been completed between enqueue and run
    if milestone is None or milestone.status == RoadmapStatus.done:
        return
    event_bus.publish(
        db,
        "roadmap.milestone.overdue",
        {"startup_id": str(job.payload["startup_id"]), "milestone_id": str(milestone.id)},
    )


def handle_assessment_quarterly(db: Session, job: Job) -> None:
    event_bus.publish(
        db, "assessment.quarterly.due", {"startup_id": str(job.payload["startup_id"])}
    )


register_handler(SCHED_MISSION, handle_mission_generate)
register_handler(SCHED_OVERDUE, handle_roadmap_overdue)
register_handler(SCHED_QUARTERLY, handle_assessment_quarterly)
```

> `JobStatus` import in the test: it lives in `app/db/models/enums.py`. If `from app.db.models.job import Job, JobStatus` fails, use `from app.db.models.enums import JobStatus`.

- [ ] **Step 4: Run + verify pass**

Run: `poetry run pytest tests/worker/test_scheduled_handlers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/worker/handlers/scheduled.py tests/worker/test_scheduled_handlers.py
git commit -m "feat(scheduler): mission.ready / overdue / quarterly job handlers"
```

---

## Task 5: Registry specs + category mappings

**Files:**
- Modify: `app/services/notifications/registry.py`, `app/services/notifications/categories.py`
- Test: `tests/services/notifications/test_scheduled_events.py`

**Interfaces:**
- Consumes: `event_bus.publish`, `_active_member_ids`, `NotifSpec`/`_s` (registry); `category_for`.
- Produces: `SPECS["mission.ready"]`, `SPECS["roadmap.milestone.overdue"]`, `SPECS["assessment.quarterly.due"]`; `EVENT_CATEGORY` for the three.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/notifications/test_scheduled_events.py
from datetime import UTC, datetime

from app.db.models.notification import Notification
from app.platform.events import event_bus
from app.services.notifications.categories import category_for
from app.services.notifications.registry import register
from tests.factories import create_membership, create_startup, create_user


def _ws(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    return u, s


def test_scheduled_events_notify_active_members_and_map_categories(db):
    register()
    u, s = _ws(db)
    for event in ("mission.ready", "roadmap.milestone.overdue", "assessment.quarterly.due"):
        event_bus.publish(db, event, {"startup_id": str(s.id)})
    assert db.query(Notification).filter_by(user_id=u.id).count() == 3
    assert category_for("mission.ready") == "roadmap_missions"
    assert category_for("roadmap.milestone.overdue") == "roadmap_missions"
    assert category_for("assessment.quarterly.due") == "health_assessment"
```

- [ ] **Step 2: Run and verify it fails**

Run: `poetry run pytest tests/services/notifications/test_scheduled_events.py -v`
Expected: FAIL — no rows created (events unhandled) / `category_for` returns None.

- [ ] **Step 3: Add a "all active members" recipients helper + the three SPECS rows**

```python
# app/services/notifications/registry.py — add near _members_minus_actor
def _all_active_members(db: Session, payload: dict) -> list[uuid.UUID]:
    return _active_member_ids(db, payload["startup_id"], exclude=None)
```

Add to the `SPECS` dict (system events, notify everyone active):

```python
    "mission.ready": _s(_all_active_members, "Today's mission is ready"),
    "roadmap.milestone.overdue": _s(_all_active_members, "A roadmap milestone is overdue"),
    "assessment.quarterly.due": _s(_all_active_members, "Time for your quarterly startup assessment"),
```

- [ ] **Step 4: Add the category mappings**

```python
# app/services/notifications/categories.py — add to EVENT_CATEGORY
    "mission.ready": "roadmap_missions",
    "roadmap.milestone.overdue": "roadmap_missions",
    "assessment.quarterly.due": "health_assessment",
```

- [ ] **Step 5: Run + verify pass**

Run: `poetry run pytest tests/services/notifications/test_scheduled_events.py tests/services/notifications/ -q`
Expected: PASS (and no Slice-1/2 regression).

- [ ] **Step 6: Commit**

```bash
git add app/services/notifications/registry.py app/services/notifications/categories.py tests/services/notifications/test_scheduled_events.py
git commit -m "feat(notifications): scheduled events (mission.ready/overdue/quarterly) + categories"
```

---

## Task 6: Worker entrypoint — throttled scheduler tick

**Files:**
- Modify: `app/worker/__main__.py`
- Test: `tests/worker/test_entrypoint.py`

**Interfaces:**
- Consumes: `scheduler_tick(db, now=...)` (Task 3); `register_handler` side effects of `app.worker.handlers.scheduled`.
- Produces: `main_loop` runs `scheduler_tick` at most once per `SCHEDULER_INTERVAL`; `register()` imports the scheduled handlers.

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_entrypoint.py — add
def test_register_wires_scheduled_handlers():
    from app.worker import runner
    from app.worker.__main__ import register
    runner.JOB_HANDLERS.clear()
    register()
    assert "scheduled.mission.generate" in runner.JOB_HANDLERS


def test_main_loop_throttles_scheduler(db, monkeypatch):
    import app.worker.__main__ as entry
    ticks = {"n": 0}
    monkeypatch.setattr(entry, "run_once", lambda d: 0)
    monkeypatch.setattr(entry, "scheduler_tick", lambda d, now: ticks.__setitem__("n", ticks["n"] + 1))
    monkeypatch.setattr(entry, "SessionLocal", lambda: db)
    monkeypatch.setattr(entry.time, "sleep", lambda _s: None)
    # SCHEDULER_INTERVAL default 60s; three quick iterations should tick the scheduler exactly once
    from app.core.config import settings
    monkeypatch.setattr(settings, "SCHEDULER_INTERVAL", 60)
    stops = iter([False, False, False, True])
    entry.main_loop(stop=lambda: next(stops))
    assert ticks["n"] == 1
```

- [ ] **Step 2: Run and verify it fails**

Run: `poetry run pytest tests/worker/test_entrypoint.py -v`
Expected: FAIL — scheduler not wired.

- [ ] **Step 3: Wire the throttled tick + register**

```python
# app/worker/__main__.py
# add imports:
from datetime import UTC, datetime
from app.worker.scheduler import scheduler_tick

# register(): add the scheduled handlers import
def register() -> None:
    import app.worker.handlers.email      # noqa: F401
    import app.worker.handlers.scheduled  # noqa: F401

# main_loop: gate scheduler_tick by SCHEDULER_INTERVAL
def main_loop(stop) -> None:
    last_tick = 0.0
    while not stop():
        db = SessionLocal()
        try:
            run_once(db)
            now_ts = time.monotonic()
            if now_ts - last_tick >= settings.SCHEDULER_INTERVAL:
                scheduler_tick(db, now=datetime.now(UTC))
                last_tick = now_ts
        except Exception as exc:  # noqa: BLE001 - the loop must survive a bad batch
            log.warning(f"[worker] loop iteration errored: {exc}")
            db.rollback()
        finally:
            db.close()
        time.sleep(settings.WORKER_POLL_INTERVAL)
```

Keep the existing `main()`/SIGTERM code unchanged.

- [ ] **Step 4: Run + verify pass**

Run: `poetry run pytest tests/worker/test_entrypoint.py -v`
Expected: PASS. (The test starts `last_tick=0.0` and `time.monotonic()` is large, so the first iteration ticks, and 60s hasn't elapsed across the next two fast iterations → exactly one tick.)

- [ ] **Step 5: Commit**

```bash
git add app/worker/__main__.py tests/worker/test_entrypoint.py
git commit -m "feat(worker): run the scheduler tick, throttled, inside the worker loop"
```

---

## Task 7: Live e2e + captures + FE guide + SOP + checklist

**Files:**
- Create: `e2e/test_notifications_scheduler.py`
- Modify: `docs/fe-integration-guide-notifications.md`, `docs/checklist/PROJECT_CHECKLIST.md`
- Create: `docs/sop/2026-09-18-notifications-scheduler.md`

**Interfaces:** consumes everything above; drives `scheduler_tick` in-process (like the Slice-2 email e2e drives the worker).

- [ ] **Step 1: Write the e2e journey**

```python
# e2e/test_notifications_scheduler.py
"""Live Module 20 Slice 3: scheduler fires time-based notifications.

Drives the scheduler + worker in-process (no separate container in the harness), the
same pattern as e2e/test_notifications_email.py. A founder onboards + gets a roadmap;
we run scheduler_tick at a controlled 'now' past 06:00, drain the worker, and assert
today's mission exists AND a `mission.ready` notification is in the feed.
"""
import httpx


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _tick_and_drain(now):
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import scheduled  # noqa: F401  (registers handlers)
    from app.worker.scheduler import scheduler_tick
    db = SessionLocal()
    try:
        scheduler_tick(db, now=now)
    finally:
        db.close()
    db2 = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db2) == 0:
                break
    finally:
        db2.close()


def test_scheduled_mission_ready_notification(base_url, make_verified_user, unique_email, capture):
    from datetime import UTC, datetime
    with httpx.Client(base_url=base_url, timeout=10.0) as a:
        # onboard founder A + reach a roadmap (mirror e2e/test_roadmap.py's setup helpers),
        # capture A's token + workspace header ...
        # run the scheduler at 07:00 UTC today, then drain:
        _tick_and_drain(datetime.now(UTC).replace(hour=7, minute=0))
        feed = a.get("/api/v1/notifications", headers=wh_a)
        assert feed.status_code == 200
        types = [n["type"] for n in feed.json()["data"]["notifications"]]
        assert "mission.ready" in types
        capture("notifications_scheduler", "mission_ready_feed", feed)
```

> Implementer note: flesh out the A-onboarding + roadmap setup by mirroring `e2e/test_roadmap.py` (which already gets a founder to a generated roadmap). Keep the `_tick_and_drain` + feed assertion exactly as above. Add the quarterly + overdue assertions the same way if cheap; the mission-ready path is the required one.

- [ ] **Step 2: Run the e2e suite**

Run: `./scripts/e2e_run.sh`
Expected: all pass incl. the new journey; capture under `e2e/_captures/notifications_scheduler/`.

- [ ] **Step 3: Docs (after the run — captures byte-accurate)**

Extend `docs/fe-integration-guide-notifications.md`: the three new notification `type`s, their `data` (`mission_id`, `milestone_id`), and that they arrive **with no user action** (scheduled), in-app always + email per the existing `roadmap_missions` / `health_assessment` category prefs. Write `docs/sop/2026-09-18-notifications-scheduler.md` (what shipped, the tick-in-worker + claim-ledger design, files/migration/config, verification, the `SCHEDULER_TIMEZONE`/`MISSION_GEN_HOUR` deploy knobs, the deferred items). Update `docs/checklist/PROJECT_CHECKLIST.md`: Module 20 Slice 3 shipped; Module 20 now needs only Slice 4 (real-time/push).

- [ ] **Step 4: Full local CI reproduction**

Run:
```
poetry run black --check app tests e2e && poetry run isort --check-only app tests e2e && poetry run ruff check app tests e2e
poetry run mypy app && poetry run pylint app --fail-under=9.5 && poetry run bandit -r app/ --quiet
poetry run pytest --cov=app --cov-fail-under=95 -q
poetry run alembic heads    # exactly one
./scripts/e2e_run.sh
```
Expected: all green; one head.

- [ ] **Step 5: Commit**

```bash
git add e2e/ docs/
git commit -m "test(scheduler): Slice 3 live e2e + captures + FE guide + SOP + checklist"
```

---

## Self-Review

**Spec coverage:** §2 tick+claim → T1/T3; §3 ledger → T1; §4 detectors+config → T2; §5 handlers → T4; §6 events+categories → T5; §7 config+throttle → T2/T6; §8 errors → T3 (per-item isolation) + T4 (handler no-commit) + T6 (loop try/except); §9 testing → T1–T6 unit + T7 e2e/docs; §11 waivers (single tz, overdue-once, quarterly-only-prior-assessed, claim/enqueue non-atomic gap covered by mission lazy-read) → surfaced in the SOP (T7) and the tick note (T3). ✓

**Type consistency:** `Due(task_key, scope_key, period_key, job_type, startup_id, payload)`; `_claim(db, task_key, scope_key, period_key) -> bool`; detectors `-> list[Due]`; `scheduler_tick(db, *, now) -> int`; `SCHED_MISSION/OVERDUE/QUARTERLY` job-type strings match the handler `register_handler` keys and the enqueue `item.job_type`; events `mission.ready`/`roadmap.milestone.overdue`/`assessment.quarterly.due` match across handler-publish (T4), registry SPECS (T5), and category map (T5). `job_dispatcher.enqueue(db, type, payload, startup_id)` matches the real signature. `get_or_generate_today(db, startup) -> Mission | None` matches.

**Placeholder scan:** the only deliberate placeholder is the migration number `00NN` (settled at build, single-head, per Global Constraints). The T7 e2e onboarding/roadmap setup references mirroring `e2e/test_roadmap.py` rather than repeating its ~60 lines; the Slice-3-specific `_tick_and_drain` + assertion are given in full.

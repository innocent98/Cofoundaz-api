# Module 19 Calendar — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A per-workspace `calendar_events` store (full CRUD + one date-range/type-filtered list that powers Month / Week / Agenda) and a read-only `GET /calendar/milestones` projection over the existing `RoadmapMilestone` rows.

**Architecture:** A new `calendar` service package + one `calendar_events` table (migration `0046_calendar`). Dates and times are naive local wall-clock, stored as sent. The milestones projection owns no table — it joins `RoadmapMilestone → RoadmapPhase → Roadmap` and filters on `Roadmap.startup_id`. Mirrors the Module 12 Finance transaction CRUD + range-filtered list patterns throughout.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0 (typed `Mapped`), Postgres, Alembic, Poetry, pytest.

**Spec:** `docs/superpowers/specs/2026-10-05-module-19-calendar-design.md` (D1–D13, reviewed and approved)

## Global Constraints

- **CRUD/tenancy:** services `db.flush()` only; endpoints `db.commit()`; every query scoped by `startup_id` taken from `membership.startup_id`, **never** from the body or query; cross-tenant id → `NotFound` (404), never 403.
- **RBAC:** every route behind `_calendar = require_role(MembershipRole.founder, MembershipRole.team_member)`; all other roles (mentor / accountant / investor / …) → 403. Accountant is **not** granted (D4).
- **Endpoint conventions (match `finance.py`):** `response_model=dict[str, Any]`, return `success_response(...)`; POST/PATCH/DELETE commit; deps `payload, membership=Depends(_calendar), user=Depends(get_verified_user), db=Depends(get_db)` each with `# noqa: B008`; DELETE returns `success_response({"deleted": True})`.
- **Validation** via `_validation(field, message)` in a NEW dependency-free `app/services/calendar/errors.py`, copied from `app/services/finance/errors.py`. Do **not** put it in `service.py` — keeps the calendar service modules cycle-free from the start.
- **`AppError.http_status`** (not `.status_code`); `get_db` does NOT auto-commit.
- **Time model (D6):** `event_date` is a `Date`; `start_time` / `end_time` are naive `Time`. Stored exactly as sent, returned as `YYYY-MM-DD` and `HH:MM`. No conversion anywhere. "Today" is `datetime.now(UTC).date()` — **not** the scheduler's `_local(now)`.
- **`all_day` is declared, never inferred (D12):** an event with no times stays `all_day = false`. The backend must not derive one from the other in either direction.
- **CodeQL (required):** no mutating call inside an `assert` (bind to a variable first); no implicit string-concatenation in a list literal.
- **Migration:** id ≤ 32 chars (`0046_calendar`), `down_revision = "0045_finance_model"`. The head test asserts **single head + revision present in `alembic history`**, not "my revision is THE head" — so it still passes after someone else merges ahead and the file is renumbered.
- **Re-check the live head immediately before pushing** (`git fetch && ls alembic/versions | tail`) and renumber if anything merged first. Both previous modules were renumbered at merge time.
- **No new dependencies. No AI attribution** in any commit message.

## Review Focus

- **Range boundaries:** an event exactly on `date_from` and exactly on `date_to` is included (`>=` / `<=`). `date_to` earlier than `date_from` → empty list, not an error. — Task 2.
- **Intra-day ordering:** all-day and untimed events sort **before** timed ones in the same day (`start_time ASC NULLS FIRST`). — Task 2.
- **`all_day` contradictions:** `all_day: true` with either time → 422, on create **and** on PATCH (D9). An event created with neither times nor the flag stays `all_day = false` (D12). — Task 2.
- **Time pairs:** `end_time` without `start_time` → 422; `end_time` < `start_time` → 422; equal is allowed. — Task 2.
- **PATCH on the merged row:** patching only `start_time` to a value later than the row's existing `end_time` → 422. Validating the payload alone would miss it. — Task 2.
- **PATCH explicit null:** `null` for `title` / `type` / `event_date` / `all_day` → 422 (NOT NULL columns); `null` for `start_time` / `end_time` / `description` **clears** them; omitting any field is a no-op. — Task 2.
- **Tenancy:** GET / PATCH / DELETE another workspace's event id → 404. The milestones projection must never return another workspace's milestone — the three-table join is the only thing preventing it. — Tasks 2, 3.
- **RBAC:** mentor / accountant / investor → 403 on all six routes. — Tasks 2, 3.
- **`calendar_status` branches:** done-and-overdue → `complete` (not `at_risk`); todo-and-overdue → `at_risk`; in_progress-and-overdue → `at_risk`; in_progress not overdue → `on_track`; todo not overdue → `not_started`; `due_on is None` → never `at_risk`. — Task 3.
- **Empty states:** a workspace with no roadmap at all → `{"milestones": []}`, not a 500. A workspace with no events → `{"events": []}`. — Tasks 2, 3.

---

## Task 1: Enum + model + errors + migration `0046_calendar`

**Files:**
- Modify: `app/db/models/enums.py` (`CalendarEventType`)
- Modify: `app/db/models/__init__.py` (export `CalendarEvent`)
- Create: `app/db/models/calendar.py` (`CalendarEvent`)
- Create: `app/services/calendar/__init__.py` (empty), `app/services/calendar/errors.py` (`_validation`)
- Create: `alembic/versions/0046_calendar.py`
- Test: `tests/db/test_calendar_models.py`, `tests/test_calendar_migration.py`

**Interfaces (produced):** `CalendarEventType` (milestone/meeting/deadline/task); `CalendarEvent` model; `app.services.calendar.errors._validation`.

- [ ] **Step 1: Write failing model + migration tests**

```python
# tests/db/test_calendar_models.py
import uuid
from datetime import date, time

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.calendar import CalendarEvent
from app.db.models.enums import CalendarEventType
from tests.factories import create_startup, create_user


def _event(db, startup, **kw):
    kw.setdefault("title", "Partner call")
    kw.setdefault("type", CalendarEventType.meeting)
    kw.setdefault("event_date", date(2026, 7, 6))
    row = CalendarEvent(startup_id=startup.id, **kw)
    db.add(row)
    db.flush()
    return row


def test_defaults_all_day_false_and_times_null(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    row = _event(db, s)
    assert row.all_day is False
    assert row.start_time is None and row.end_time is None
    assert row.description is None
    assert row.created_by is None
    assert row.id is not None and row.created_at is not None


def test_stores_naive_times_and_the_enum_value(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    row = _event(
        db,
        s,
        type=CalendarEventType.deadline,
        start_time=time(14, 0),
        end_time=time(15, 30),
        description="Bring the deck",
    )
    db.expire(row)
    assert row.start_time == time(14, 0) and row.end_time == time(15, 30)
    stored = db.execute(
        CalendarEvent.__table__.select().where(CalendarEvent.id == row.id)
    ).mappings().one()
    assert stored["type"] == "deadline"  # the VALUE, not the member name


def test_created_by_survives_its_user(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    other = create_user(db)
    db.flush()
    row = _event(db, s, created_by=other.id)
    db.delete(other)
    db.flush()
    db.expire(row)
    assert row.created_by is None  # SET NULL, the event is the workspace's


def test_event_dies_with_its_startup(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    row = _event(db, s)
    rid = row.id
    db.delete(s)
    db.flush()
    assert db.get(CalendarEvent, rid) is None


def test_startup_id_is_required(db):
    with pytest.raises(IntegrityError):
        db.add(
            CalendarEvent(
                startup_id=uuid.uuid4(),
                title="Orphan",
                type=CalendarEventType.task,
                event_date=date(2026, 7, 6),
            )
        )
        db.flush()
```

```python
# tests/test_calendar_migration.py
import subprocess


def _alembic(*args: str) -> subprocess.CompletedProcess:
    cmd = ["poetry", "run", "alembic", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_migration_chain_single_head_includes_calendar():
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    history = _alembic("history")
    assert history.returncode == 0, history.stderr
    assert "_calendar" in history.stdout


def test_upgrade_then_downgrade_round_trips():
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    down = _alembic("downgrade", "0045_finance_model")
    assert down.returncode == 0, down.stderr
    reup = _alembic("upgrade", "head")
    assert reup.returncode == 0, reup.stderr
```

- [ ] **Step 2: Watch them fail** — `docker exec cfz-test pytest tests/db/test_calendar_models.py -q` must fail on the import of `app.db.models.calendar`.

- [ ] **Step 3: Add the enum** — append to `app/db/models/enums.py` (end of file, two blank lines above):

```python
class CalendarEventType(enum.StrEnum):
    milestone = "milestone"
    meeting = "meeting"
    deadline = "deadline"
    task = "task"
```

- [ ] **Step 4: Create `app/db/models/calendar.py`**

```python
import uuid
from datetime import date, time

from sqlalchemy import Boolean, Date, Enum, ForeignKey, Index, String, Text, Time
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import CalendarEventType


class CalendarEvent(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "calendar_events"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SET NULL, not CASCADE: the event belongs to the workspace, not to whoever typed it, so a
    # departing user must not take the team's calendar with them.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[CalendarEventType] = mapped_column(
        # values_callable so the DB stores the enum *value*, matching the migration labels and the
        # lowercase strings the FE badges.
        Enum(
            CalendarEventType,
            native_enum=False,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Naive wall-clock (spec D6). There is no timezone field anywhere in the app, so a time is
    # stored exactly as sent and returned exactly as stored; nothing converts.
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    all_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_calendar_events_startup_id_event_date", "startup_id", "event_date"),
    )
```

- [ ] **Step 5: Export it** — add `CalendarEvent` to `app/db/models/__init__.py` following the existing import/`__all__` style.

- [ ] **Step 6: Create `app/services/calendar/__init__.py`** (empty) and `app/services/calendar/errors.py`:

```python
from app.core.errors import AppError


def _validation(field: str, message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR", message, 422, field_errors=[{"field": field, "message": message}]
    )
```

- [ ] **Step 7: Autogenerate the migration** — `docker exec cfz-test alembic revision --autogenerate -m "calendar"`, then hand-edit **only** the revision id, `down_revision`, Create Date and docstring:

```python
revision = "0046_calendar"
down_revision = "0045_finance_model"
```

Rename the file to `alembic/versions/0046_calendar.py`. Confirm the autogenerated body creates `calendar_events` with both indexes and nothing else. Write a docstring in the house style saying: one brand-new table, no lock on any existing table beyond validating the two foreign keys; naive `Time` columns per D6; `all_day` server-default `false`.

- [ ] **Step 8: Green** — rebuild the dev DB (`alembic upgrade head`), then `pytest tests/db/test_calendar_models.py tests/test_calendar_migration.py -q`. Confirm `alembic heads` shows one head and `alembic check` reports no drift.

---

## Task 2: Events CRUD — schemas + service + endpoints + router

**Files:**
- Create: `app/schemas/calendar.py`, `app/services/calendar/service.py`, `app/api/v1/endpoints/calendar.py`
- Modify: `app/api/v1/api.py` (register the router at `prefix="/calendar"`)
- Test: `tests/services/calendar/test_events.py`, `tests/api/test_calendar.py`

**Interfaces (produced):** `EventCreate`, `EventUpdate`, `EventResponse`; `create_event`, `get_event`, `list_events`, `update_event`, `delete_event`, `serialize_event`; the five event routes.

- [ ] **Step 1: Write the failing service tests** — `tests/services/calendar/test_events.py` (create `tests/services/calendar/__init__.py` if the convention requires it; `tests/services/validation/` is the precedent). Cover:
  - create with the three FE fields only (title, type, event_date) → `all_day is False`, both times `None`
  - create with `start_time` + `end_time` → stored naive, unchanged
  - `end_time` without `start_time` → `AppError` 422
  - `end_time` < `start_time` → 422; equal → allowed
  - `all_day=True` with either time → 422
  - `list_events` range boundaries: rows on `date_from` and on `date_to` are both returned
  - `list_events` ordering: same date, one all-day + one 09:00 + one 14:00 → all-day first
  - `list_events` type filter, and no filter → everything
  - `date_to` < `date_from` → `[]`
  - `get_event` with another workspace's id → `NotFound`
  - `update_event` merged-row rule: patch `start_time` later than the stored `end_time` → 422
  - `update_event` explicit `None` on `start_time` clears it
  - `delete_event` removes the row; a second delete → `NotFound`

- [ ] **Step 2: Write the failing API tests** — `tests/api/test_calendar.py`, copying the `_headers` / `_member` helpers and `FORBIDDEN_ROLES` shape from `tests/api/test_finance.py`. Add `MembershipRole.accountant` to `FORBIDDEN_ROLES` here (D4 — unlike Finance, accountant has no calendar grant). Cover the CRUD round-trip, the envelope, `{"deleted": True}`, 422 bodies carrying `field_errors`, cross-tenant 404 on GET/PATCH/DELETE, and 403 for every forbidden role, parametrised.

- [ ] **Step 3: Watch them fail.**

- [ ] **Step 4: Create `app/schemas/calendar.py`**

```python
import datetime as dt
import uuid

from pydantic import BaseModel, Field, model_validator

from app.db.models.enums import CalendarEventType, RoadmapStatus

MAX_DESCRIPTION = 2000


def _check_times(
    all_day: bool, start_time: dt.time | None, end_time: dt.time | None
) -> None:
    """Shared by create and update so a PATCH cannot leave a row in a state a POST would refuse.

    Called by update against the MERGED values, never the payload alone.
    """
    if all_day and (start_time is not None or end_time is not None):
        # Refuse rather than silently discard what the caller sent (spec D9).
        raise ValueError("an all-day event may not carry a start or end time")
    if end_time is not None and start_time is None:
        raise ValueError("end_time needs a start_time")
    if start_time is not None and end_time is not None and end_time < start_time:
        raise ValueError("end_time may not be earlier than start_time")


class EventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    type: CalendarEventType
    event_date: dt.date
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    all_day: bool = False
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)

    @model_validator(mode="after")
    def _times_make_sense(self) -> "EventCreate":
        _check_times(self.all_day, self.start_time, self.end_time)
        return self


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    type: CalendarEventType | None = None
    event_date: dt.date | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    all_day: bool | None = None
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)

    @model_validator(mode="after")
    def _no_explicit_null_on_required(self) -> "EventUpdate":
        # title/type/event_date/all_day are NOT NULL on the row; an explicit null would hit a
        # NOT NULL violation at flush (500), so reject it as 422 here. start_time, end_time and
        # description ARE nullable, so an explicit null legitimately CLEARS them. Omitting any
        # field remains a no-op.
        for name in ("title", "type", "event_date", "all_day"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} may not be null")
        return self


class EventResponse(BaseModel):
    id: uuid.UUID
    title: str
    type: CalendarEventType
    event_date: dt.date
    start_time: dt.time | None
    end_time: dt.time | None
    all_day: bool
    description: str | None
    created_by: uuid.UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime


class MilestoneResponse(BaseModel):
    id: uuid.UUID
    title: str
    due_on: dt.date | None
    status: RoadmapStatus
    calendar_status: str
    progress: int
```

> **Note on the wire format:** `model_dump(mode="json")` renders `dt.time` as `HH:MM:SS`, but D6 and the FE guide promise `HH:MM`. Decide in Step 5 and keep it in **one** place — either a `field_serializer` on `EventResponse` for the two time fields, or `.isoformat(timespec="minutes")` in the serializer. A `field_serializer` is preferred: it cannot be forgotten at a call site.

- [ ] **Step 5: Create `app/services/calendar/service.py`** — `serialize_event` via `EventResponse.model_validate(row, from_attributes=True)`; `create_event(db, *, startup_id, created_by, data)`; `get_event(db, *, startup_id, event_id)` raising `NotFound()`; `list_events(db, *, startup_id, date_from=None, date_to=None, type=None)` mirroring `list_transactions` (independent `>=` / `<=`), ordered `event_date ASC`, `start_time ASC NULLS FIRST`, `created_at ASC`; `update_event` applying `model_dump(exclude_unset=True)` then calling `_check_times` on the **merged** values and raising `_validation(...)` on failure; `delete_event`. Services `flush()` only.

  Use `sqlalchemy.nullsfirst(CalendarEvent.start_time.asc())` for the ordering — Postgres defaults ASC to NULLS LAST, which would put untimed events at the bottom of their day, the opposite of what the Agenda renders.

- [ ] **Step 6: Create `app/api/v1/endpoints/calendar.py`** — `_calendar = require_role(MembershipRole.founder, MembershipRole.team_member)` and the five routes from the spec table. `created_by=user.id` on create. POST/PATCH/DELETE commit; the list and single GET do not.

- [ ] **Step 7: Register the router** — in `app/api/v1/api.py`, add `calendar` to the `from app.api.v1.endpoints import (...)` block (alphabetical: after `business`, before `dashboard`) and `api_router.include_router(calendar.router, prefix="/calendar", tags=["calendar"])`.

- [ ] **Step 8: Green** — `pytest tests/services/calendar tests/api/test_calendar.py -q`, then `ruff check app tests`, `black --check app tests`, `mypy app`.

---

## Task 3: Milestones projection — `GET /calendar/milestones`

**Files:**
- Create: `app/services/calendar/milestones.py`
- Modify: `app/api/v1/endpoints/calendar.py` (one route)
- Test: `tests/services/calendar/test_milestones.py`, add API cases to `tests/api/test_calendar.py`

**Interfaces (produced):** `list_milestones(db, *, startup_id, today=None)`, `calendar_status(status, due_on, today)`, `serialize_milestone`.

- [ ] **Step 1: Write the failing tests.** `calendar_status` is pure, so test it directly against all six branches from Review Focus — including **done + overdue → `complete`**, which is the one an implementation ordered differently would get wrong. Then test `list_milestones` against the database for:
  - a milestone in another workspace's roadmap never appears (build two full `Roadmap → RoadmapPhase → RoadmapMilestone` chains under two startups)
  - a workspace with no roadmap → `[]`, no exception
  - ordering: `due_on ASC NULLS LAST`, so a milestone with `due_on = None` sorts after a dated one
  - completed milestones are included
  - `progress` and the raw `status` are both returned

  Pass `today` in explicitly from the tests so overdue cases don't depend on the real clock.

- [ ] **Step 2: Watch them fail.**

- [ ] **Step 3: Create `app/services/calendar/milestones.py`**

```python
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import nullslast
from sqlalchemy.orm import Session

from app.db.models.enums import RoadmapStatus
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.schemas.calendar import MilestoneResponse

ON_TRACK = "on_track"
AT_RISK = "at_risk"
COMPLETE = "complete"
NOT_STARTED = "not_started"


def calendar_status(status: RoadmapStatus, due_on: date | None, today: date) -> str:
    """The four labels the FE badges, derived — RoadmapStatus has no notion of risk.

    Order matters: a finished milestone is never "at risk" however old its due date, and being
    overdue outranks being in progress. RoadmapStatus is exactly {todo, in_progress, done}, so
    the final branch can only be `todo`.
    """
    if status == RoadmapStatus.done:
        return COMPLETE
    if due_on is not None and due_on < today:
        return AT_RISK
    if status == RoadmapStatus.in_progress:
        return ON_TRACK
    return NOT_STARTED


def list_milestones(
    db: Session, *, startup_id: uuid.UUID, today: date | None = None
) -> list[MilestoneResponse]:
    """Read-only projection of this workspace's roadmap milestones (spec D1).

    `RoadmapMilestone` carries no `startup_id`; it scopes through
    `phase_id -> RoadmapPhase.roadmap_id -> Roadmap.startup_id`, so this three-table join IS the
    tenancy boundary. Mirrors `_due_overdue_milestones` in `app/worker/scheduler.py`, with two
    deliberate differences:

    * no `Startup.deleted_at` filter — that function scans every workspace, while this read
      already has an active `Membership` for this one (`resolve_workspace` applies no such
      filter either, so adding one here would be inconsistent with the rest of the app);
    * "today" from `datetime.now(UTC).date()` rather than its `_local(now)`, which depends on
      the `SCHEDULER_TIMEZONE` setting. The two agree while that setting is "UTC", but the
      calendar's clock should not move when a scheduler config value changes (spec D6).
    """
    as_of = today or datetime.now(UTC).date()
    rows = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(Roadmap.startup_id == startup_id)
        .order_by(nullslast(RoadmapMilestone.due_on.asc()), RoadmapMilestone.created_at.asc())
        .all()
    )
    return [serialize_milestone(row, today=as_of) for row in rows]


def serialize_milestone(row: RoadmapMilestone, *, today: date) -> MilestoneResponse:
    # `tasksInfo` is NOT returned: the FE renders "11 of 13 tasks complete" but nothing in the
    # schema backs it. Omitted rather than faked, and labelled not-backed in the FE guide.
    return MilestoneResponse(
        id=row.id,
        title=row.title,
        due_on=row.due_on,
        status=row.status,
        calendar_status=calendar_status(row.status, row.due_on, today),
        progress=row.progress,
    )
```

- [ ] **Step 4: Add the route** to `app/api/v1/endpoints/calendar.py` — behind the same `_calendar` dependency, read-only, no commit, returning `success_response({"milestones": [m.model_dump(mode="json") for m in rows]})`.

- [ ] **Step 5: Green** — `pytest tests/services/calendar tests/api/test_calendar.py -q` plus the gates.

---

## Task 4: E2E journey + captures

**Files:**
- Create: `e2e/test_calendar.py`
- Modify: `e2e/test_smoke.py` (the six route shapes)

- [ ] **Step 1: Add the six route shapes** to the smoke surface assertion: `POST|GET /calendar/events`, `GET|PATCH|DELETE /calendar/events/{event_id}`, `GET /calendar/milestones`.

- [ ] **Step 2: Write `e2e/test_calendar.py`** — one journey over real HTTP, modelled on `e2e/test_finance.py`: onboard a founder → create four events across a month (one per type, one all-day, one timed, one with notes) → list the whole month → list one week → filter by `type=meeting` → read one → PATCH its time → clear the time with an explicit `null` → attempt `all_day: true` + a time and capture the 422 → DELETE one → read the milestones projection → attempt a cross-tenant GET and capture the 404.

- [ ] **Step 3: Capture every response** to `e2e/_captures/calendar/`, including the two failures. These captures, not hand-written examples, are the source for the FE guide in Task 5.

- [ ] **Step 4: Run it** — `bash scripts/e2e_run.sh`. **This has never worked on this machine** (the runner needs Poetry on the Windows host), so expect to leave it to CI exactly as on Modules 17 and 09. If it does not run locally, say so plainly in the SOP's Verification section rather than implying it passed.

---

## Task 5: Docs — FE guide + SOP + checklist

**Files:**
- Create: `docs/fe-integration-guide-calendar.md`, `docs/sop/<ship-date>-calendar.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: FE integration guide, from the captures.** Every success body pasted from `e2e/_captures/calendar/`, never hand-written. If the captures do not exist yet because e2e has not run, add a provenance note saying the values are illustrative until regenerated — and a follow-up to delete that note. (Module 09 shipped with exactly that note; do not pretend otherwise.)

  Five things the guide must state in so many words, because the mock contradicts all of them:
  1. **The API returns `HH:MM`.** The mock shows `'09:00 AM'` in Week and `'09:00'` in Agenda; neither comes off the wire. Format for display.
  2. **`all_day: true` is what produces the "All day" label** — there is no `'All day'` time string.
  3. **`type` is always lowercase** (`meeting`), though `AgendaItemData.type` is capitalized in the mock. Map for display.
  4. **`due_on` is an ISO date**, not the pre-formatted sentence `'Due Friday, Jul 31'`. The FE builds that sentence, including swapping the prefix to "Completed" when `calendar_status == "complete"`.
  5. **The Milestones tab reads one store and writes to another (D13).** The list is the Roadmap projection; "+ Milestone" calls `POST /calendar/events` with `type: "milestone"` and `all_day: true`, and what it creates **does not appear in the list above it** — it lands on Month / Week / Agenda.

  Plus the not-backed fields: `tasksInfo`, `AgendaItemData.subtitle`, "N of M complete this quarter". And the Sync tab: nothing to call in v1.

- [ ] **Step 2: SOP** in the house shape — what shipped, the files, the time model and its rationale, roll-back (`alembic downgrade 0045_finance_model`, lossy: every event destroyed; the milestones projection owns no data so nothing of Roadmap's is touched), the RBAC set, and a Verification section with the real numbers. Follow-ups: multi-day events, pagination, the two deferred aggregations, recurrence, unioning the Milestones list, reminders, external sync.

- [ ] **Step 3: Checklist** — a Module 19 section in the same shape as Module 09's, plus the "Upcoming" tally.

---

## Final verification (before opening the PR)

- [ ] `git fetch origin` then `ls alembic/versions | tail` — **re-check the live `develop` head**. If anything merged ahead of `0045_finance_model`, renumber the migration and update every reference to it (the migration docstring, the migration test's `downgrade` target, the SOP, the checklist).
- [ ] Rebase onto current `develop`; resolve `app/db/models/enums.py`, `app/db/models/__init__.py`, `app/api/v1/api.py` and `docs/checklist/PROJECT_CHECKLIST.md` by **keeping both sides** — those four are the files every module touches.
- [ ] Rebuild the dev DB from zero; `alembic upgrade head`; `alembic heads` shows exactly one head; `alembic check` reports no drift.
- [ ] `ruff check app tests e2e`, `black --check app tests`, `mypy app` — all clean.
- [ ] Full suite green with `REDIS_URL=redis://host.docker.internal:6379/0`; coverage ≥ 95%.
- [ ] Record the real test count and coverage in the SOP and the checklist. Do not carry numbers over from a previous run.
- [ ] PR base is **`develop`**, not `main`. (This was a review blocker on Module 09 — check it before tagging anyone.)
- [ ] No `Co-Authored-By` or "Generated with" lines in any commit message or the PR body.

## Self-Review

- **The three-table join is the only tenancy boundary on the milestones route.** There is no `startup_id` column to fall back on, so a dropped or mis-ordered join leaks another workspace's milestones with no other guard to catch it. The two-workspace test in Task 3 Step 1 is the one test that must not be skipped.
- **`_check_times` must run against the merged row on PATCH.** Validating the payload alone passes a request that sets `start_time` later than the stored `end_time`, and the row ends up in a state a create would have refused. Named in Review Focus and tested explicitly.
- **`NULLS FIRST` is not the Postgres default.** A plain `start_time ASC` puts untimed and all-day events at the *bottom* of their day. The spec's ordering is deliberate and the test for it is in Task 2 Step 1.
- **The `HH:MM` promise is easy to break.** `model_dump(mode="json")` emits `HH:MM:SS`. Fixing it in one serializer rather than at each call site is the difference between a guarantee and a convention.
- **`all_day` must not be inferred (D12).** The temptation is to set it when no time is given; that erases the difference between an untimed deadline and a day-blocking event, and contradicts D9.
- **The migration number will probably move.** Both previous modules were renumbered at merge time. The head test deliberately asserts "single head + present in history" rather than "my revision is the head", so it survives the renumber.
- **e2e has never run on this machine.** Say so in the SOP rather than implying a green run.

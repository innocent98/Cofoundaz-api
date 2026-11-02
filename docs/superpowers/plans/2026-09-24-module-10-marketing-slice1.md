# Module 10 Marketing Hub — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Marketing Hub CRUD spine — a Content Calendar, a fixed per-startup Channels board, and a minimal Overview stat strip — as backend API.

**Architecture:** A new `marketing` module mirroring the existing CRUD modules (journal/dashboard): SQLAlchemy models + migration, Pydantic schemas, a service layer holding all logic, thin endpoints under `/marketing` gated to founder+team_member, a `marketing.post.published` domain event on publish, and a notifications-registry subscriber that turns that event into an in-app notification. AI is entirely deferred to Slice 3.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 (`Mapped`, `Enum(X, native_enum=False, length=N)`), Alembic, Postgres, pytest, Poetry.

**Spec:** `docs/superpowers/specs/2026-09-24-module-10-marketing-slice1-design.md`

## Global Constraints

- **No AI attribution** in any commit message, PR/issue body, or review comment (no `Co-Authored-By`, `Claude-Session`, or "Generated with" footer). Binds every subagent; overrides any harness attribution reminder.
- **Reproduce CI locally and green before push** via the pinned toolchain (`poetry run …`): black, isort, ruff, mypy, pylint ≥ 9.5, bandit, pytest ≥ 95% coverage, **Migrations (fresh-DB round-trip + `alembic check` drift) — one new migration `0033`, single linear head off `0032_journal_prompts`**, e2e.
- **Enum style:** `enum.StrEnum` classes mapped via `Enum(Cls, native_enum=False, length=N)`. **FK columns** `index=True`. Models use `UUIDMixin, TimestampMixin, Base`.
- **Transaction discipline:** services end with `db.flush()` and never `commit`/`rollback`; **endpoints call `db.commit()`** before returning (the `get_db` session does not auto-commit — see `dashboard.py`); event handlers never commit (the bus wraps each in a SAVEPOINT).
- **Access:** all marketing endpoints gated `require_role(MembershipRole.founder, MembershipRole.team_member)`; every read/write scoped by `membership.startup_id`; cross-tenant access → `NotFound`.
- **AI deferred to Slice 3;** SOP + checklist + FE guide updated in the same pass; FE-guide payloads verbatim from live e2e captures.

## Review Focus

Five conditions the spec implies that ordinary tests can miss — each pinned to its owning task:

1. **Cross-tenant access** — a member of workspace A requesting/patching/deleting workspace B's entry or channel must get `NotFound`, never another tenant's data. (Tests in Task 2 for entries, Task 3 for channels.)
2. **Publish idempotency** — `PATCH status=published` on an already-published entry must NOT re-emit `marketing.post.published` or reset `published_at`. (Test in Task 2.)
3. **`scheduled` without `scheduled_at`** — setting status `scheduled` (on create or update) with no `scheduled_at` must return `422`, not persist a scheduled-but-undated entry. (Test in Task 2.)
4. **RBAC on every endpoint** — a `mentor`/`investor` membership must get `403` on all `/marketing/*` routes; only `founder`/`team_member` pass. (Test in Task 4.)
5. **Channel lazy-seed idempotency/race** — repeated (or concurrent first) `GET /marketing/channels` must yield exactly the 8 rows once, never duplicates, tolerating the `uq_marketing_channel_startup_key` race. (Test in Task 3.)

---

## File Structure

- `app/db/models/enums.py` (modify) — add `ContentStatus`, `ChannelStatus`, `ChannelKey`.
- `app/db/models/marketing.py` (create) — `ContentCalendarEntry`, `MarketingChannel`.
- `app/db/models/__init__.py` (modify) — register both models.
- `alembic/versions/0033_marketing_calendar_channels.py` (create).
- `app/schemas/marketing.py` (create) — request/response models.
- `app/services/marketing/__init__.py` (create, empty), `app/services/marketing/service.py` (create) — all logic.
- `app/api/v1/endpoints/marketing.py` (create) — endpoints.
- `app/api/v1/api.py` (modify) — register the router.
- `app/services/notifications/registry.py` (modify) — add the `marketing.post.published` spec row.
- Tests under `tests/db/`, `tests/services/marketing/`, `tests/api/`, `tests/services/notifications/`, and `e2e/`.
- Docs: `docs/fe-integration-guide-marketing-calendar.md`, `docs/sop/2026-09-24-marketing-slice1.md`, `docs/checklist/PROJECT_CHECKLIST.md`.

---

## Task 1: Enums + models + migration 0033

**Files:**
- Modify: `app/db/models/enums.py`
- Create: `app/db/models/marketing.py`
- Modify: `app/db/models/__init__.py`
- Create: `alembic/versions/0033_marketing_calendar_channels.py`
- Test: `tests/db/test_marketing_models.py`, `tests/test_marketing_migration.py`

**Interfaces:**
- Produces: `ContentStatus(draft|scheduled|published)`, `ChannelStatus(active|testing|paused|not_started)`, `ChannelKey(organic_social|paid_social|search|email|content_seo|partnerships|events|referral)`; `ContentCalendarEntry` (table `content_calendar`) and `MarketingChannel` (table `marketing_channels`, unique `(startup_id, key)`).

- [ ] **Step 1: Write the failing model test**

```python
# tests/db/test_marketing_models.py
from datetime import UTC, datetime
from app.db.models.enums import ChannelKey, ChannelStatus, ContentStatus
from app.db.models.marketing import ContentCalendarEntry, MarketingChannel
from tests.factories import create_startup, create_user


def test_content_calendar_entry_persists(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    e = ContentCalendarEntry(
        startup_id=s.id, created_by=u.id, title="Launch tweet",
        channel=ChannelKey.organic_social, status=ContentStatus.scheduled,
        body="Big news", scheduled_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
    )
    db.add(e); db.flush()
    got = db.query(ContentCalendarEntry).filter_by(startup_id=s.id).one()
    assert got.status == ContentStatus.scheduled
    assert got.channel == ChannelKey.organic_social
    assert got.published_at is None


def test_marketing_channel_unique_per_startup_key(db):
    import pytest
    from sqlalchemy.exc import IntegrityError
    s = create_startup(db, owner=create_user(db))
    db.add(MarketingChannel(startup_id=s.id, key=ChannelKey.email, status=ChannelStatus.active))
    db.flush()
    db.add(MarketingChannel(startup_id=s.id, key=ChannelKey.email, status=ChannelStatus.testing))
    with pytest.raises(IntegrityError):
        db.flush()
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/db/test_marketing_models.py -v`
Expected: FAIL (imports missing).

- [ ] **Step 3: Add the enums**

In `app/db/models/enums.py`, append:

```python
class ContentStatus(enum.StrEnum):
    draft = "draft"
    scheduled = "scheduled"
    published = "published"


class ChannelStatus(enum.StrEnum):
    active = "active"
    testing = "testing"
    paused = "paused"
    not_started = "not_started"


class ChannelKey(enum.StrEnum):
    organic_social = "organic_social"
    paid_social = "paid_social"
    search = "search"
    email = "email"
    content_seo = "content_seo"
    partnerships = "partnerships"
    events = "events"
    referral = "referral"
```

- [ ] **Step 4: Create the models**

```python
# app/db/models/marketing.py
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import ChannelKey, ChannelStatus, ContentStatus


class ContentCalendarEntry(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "content_calendar"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[ChannelKey] = mapped_column(
        Enum(ChannelKey, native_enum=False, length=20), nullable=False
    )
    status: Mapped[ContentStatus] = mapped_column(
        Enum(ContentStatus, native_enum=False, length=12),
        nullable=False,
        default=ContentStatus.draft,
    )
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MarketingChannel(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "marketing_channels"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[ChannelKey] = mapped_column(
        Enum(ChannelKey, native_enum=False, length=20), nullable=False
    )
    status: Mapped[ChannelStatus] = mapped_column(
        Enum(ChannelStatus, native_enum=False, length=12),
        nullable=False,
        default=ChannelStatus.not_started,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("startup_id", "key", name="uq_marketing_channel_startup_key"),
    )
```

- [ ] **Step 5: Register the models**

In `app/db/models/__init__.py`, add (alphabetical with the others):

```python
from app.db.models.marketing import ContentCalendarEntry, MarketingChannel  # noqa: F401
```

- [ ] **Step 6: Run the model test — verify it passes**

Run: `poetry run pytest tests/db/test_marketing_models.py -v`
Expected: PASS.

- [ ] **Step 7: Generate + adjust the migration**

Run: `poetry run alembic revision --autogenerate -m "marketing_calendar_channels"`, rename to `alembic/versions/0033_marketing_calendar_channels.py`, set `revision = "0033_marketing_calendar_channels"` and `down_revision = "0032_journal_prompts"`. Ensure `upgrade()` creates both tables (adjust autogenerate to match; mirror `0030`/`0032` style):

```python
def upgrade() -> None:
    op.create_table(
        "content_calendar",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("channel", sa.Enum(
            "organic_social", "paid_social", "search", "email", "content_seo",
            "partnerships", "events", "referral", native_enum=False, length=20), nullable=False),
        sa.Column("status", sa.Enum(
            "draft", "scheduled", "published", native_enum=False, length=12), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("media_ref", sa.String(length=500), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"],
            name=op.f("fk_content_calendar_startup_id_startups"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"],
            name=op.f("fk_content_calendar_created_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_calendar")),
    )
    op.create_index(op.f("ix_content_calendar_startup_id"), "content_calendar", ["startup_id"], unique=False)
    op.create_index(op.f("ix_content_calendar_created_by"), "content_calendar", ["created_by"], unique=False)
    op.create_table(
        "marketing_channels",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Enum(
            "organic_social", "paid_social", "search", "email", "content_seo",
            "partnerships", "events", "referral", native_enum=False, length=20), nullable=False),
        sa.Column("status", sa.Enum(
            "active", "testing", "paused", "not_started", native_enum=False, length=12), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"],
            name=op.f("fk_marketing_channels_startup_id_startups"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_channels")),
        sa.UniqueConstraint("startup_id", "key", name="uq_marketing_channel_startup_key"),
    )
    op.create_index(op.f("ix_marketing_channels_startup_id"), "marketing_channels", ["startup_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_marketing_channels_startup_id"), table_name="marketing_channels")
    op.drop_table("marketing_channels")
    op.drop_index(op.f("ix_content_calendar_created_by"), table_name="content_calendar")
    op.drop_index(op.f("ix_content_calendar_startup_id"), table_name="content_calendar")
    op.drop_table("content_calendar")
```

- [ ] **Step 8: Add a migration round-trip assertion**

In `tests/test_marketing_migration.py`, follow the existing per-module migration test pattern (see `tests/test_journal_migration.py`): assert both tables and the unique constraint `uq_marketing_channel_startup_key` exist after `upgrade head`.

- [ ] **Step 9: Verify round-trip, drift, single head**

Run: `poetry run alembic upgrade head && poetry run alembic downgrade -1 && poetry run alembic upgrade head && poetry run alembic check && poetry run alembic heads`
Expected: succeeds; no drift; single head `0033_marketing_calendar_channels`. Then `poetry run pytest tests/db/test_marketing_models.py tests/test_marketing_migration.py -v` PASS.

- [ ] **Step 10: Commit**

```bash
git add app/db/models/enums.py app/db/models/marketing.py app/db/models/__init__.py \
  alembic/versions/0033_marketing_calendar_channels.py tests/db/test_marketing_models.py tests/test_marketing_migration.py
git commit -m "feat(marketing): content_calendar + marketing_channels tables + enums (migration 0033)"
```

---

## Task 2: Schemas + calendar service (CRUD, publish, event)

**Files:**
- Create: `app/schemas/marketing.py`
- Create: `app/services/marketing/__init__.py` (empty), `app/services/marketing/service.py`
- Test: `tests/services/marketing/test_calendar_service.py`

**Interfaces:**
- Consumes: models + enums from Task 1; `event_bus.publish(db, event, payload)` (`app/platform/events.py`); `NotFound` (`app/core/errors.py`); `AppError` for validation.
- Produces schemas: `CalendarEntryCreate`, `CalendarEntryUpdate`, `CalendarEntryResponse`, `serialize_entry(entry) -> CalendarEntryResponse`. Produces service fns: `create_entry(db, *, startup_id, created_by, data) -> ContentCalendarEntry`; `list_entries(db, *, startup_id, date_from=None, date_to=None, channel=None, status=None) -> list[ContentCalendarEntry]`; `get_entry(db, *, startup_id, entry_id) -> ContentCalendarEntry`; `update_entry(db, *, startup_id, entry_id, actor_id, data) -> ContentCalendarEntry`; `delete_entry(db, *, startup_id, entry_id) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/marketing/test_calendar_service.py
import uuid
from datetime import UTC, datetime

import pytest

from app.core.errors import AppError, NotFound
from app.db.models.enums import ChannelKey, ContentStatus
from app.platform import events as events_mod
from app.schemas.marketing import CalendarEntryCreate, CalendarEntryUpdate
from app.services.marketing import service as svc
from tests.factories import create_startup, create_user

WHEN = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _mk(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    return u, s


def test_create_and_get_entry(db):
    u, s = _mk(db)
    e = svc.create_entry(db, startup_id=s.id, created_by=u.id,
        data=CalendarEntryCreate(title="Post", channel=ChannelKey.email, status=ContentStatus.draft))
    got = svc.get_entry(db, startup_id=s.id, entry_id=e.id)
    assert got.title == "Post" and got.status == ContentStatus.draft


def test_get_entry_other_tenant_not_found(db):
    u, s = _mk(db)
    e = svc.create_entry(db, startup_id=s.id, created_by=u.id,
        data=CalendarEntryCreate(title="P", channel=ChannelKey.email, status=ContentStatus.draft))
    _, other = _mk(db)
    with pytest.raises(NotFound):
        svc.get_entry(db, startup_id=other.id, entry_id=e.id)


def test_scheduled_requires_scheduled_at(db):
    u, s = _mk(db)
    with pytest.raises(AppError) as ei:
        svc.create_entry(db, startup_id=s.id, created_by=u.id,
            data=CalendarEntryCreate(title="P", channel=ChannelKey.email, status=ContentStatus.scheduled))
    assert ei.value.status_code == 422


def test_list_filters_by_date_range_and_channel(db):
    u, s = _mk(db)
    svc.create_entry(db, startup_id=s.id, created_by=u.id, data=CalendarEntryCreate(
        title="A", channel=ChannelKey.email, status=ContentStatus.scheduled, scheduled_at=WHEN))
    svc.create_entry(db, startup_id=s.id, created_by=u.id, data=CalendarEntryCreate(
        title="B", channel=ChannelKey.search, status=ContentStatus.scheduled,
        scheduled_at=datetime(2026, 11, 1, 9, 0, tzinfo=UTC)))
    got = svc.list_entries(db, startup_id=s.id,
        date_from=datetime(2026, 10, 1, tzinfo=UTC), date_to=datetime(2026, 10, 31, tzinfo=UTC))
    assert [e.title for e in got] == ["A"]
    only_search = svc.list_entries(db, startup_id=s.id, channel=ChannelKey.search)
    assert [e.title for e in only_search] == ["B"]


def test_publish_sets_published_at_and_emits_event_once(db, monkeypatch):
    u, s = _mk(db)
    published: list = []
    real_publish = events_mod.event_bus.publish
    def spy(dbx, event, payload):
        if event == "marketing.post.published":
            published.append(payload)
        return real_publish(dbx, event, payload)
    monkeypatch.setattr(events_mod.event_bus, "publish", spy)
    e = svc.create_entry(db, startup_id=s.id, created_by=u.id, data=CalendarEntryCreate(
        title="Launch", channel=ChannelKey.email, status=ContentStatus.scheduled, scheduled_at=WHEN))
    svc.update_entry(db, startup_id=s.id, entry_id=e.id, actor_id=u.id,
        data=CalendarEntryUpdate(status=ContentStatus.published))
    assert e.published_at is not None and len(published) == 1
    assert published[0]["title"] == "Launch" and published[0]["actor_id"] == str(u.id)
    first_published_at = e.published_at
    # re-publish is a no-op: no second event, published_at unchanged
    svc.update_entry(db, startup_id=s.id, entry_id=e.id, actor_id=u.id,
        data=CalendarEntryUpdate(status=ContentStatus.published))
    assert len(published) == 1 and e.published_at == first_published_at


def test_delete_entry(db):
    u, s = _mk(db)
    e = svc.create_entry(db, startup_id=s.id, created_by=u.id,
        data=CalendarEntryCreate(title="P", channel=ChannelKey.email, status=ContentStatus.draft))
    svc.delete_entry(db, startup_id=s.id, entry_id=e.id)
    with pytest.raises(NotFound):
        svc.get_entry(db, startup_id=s.id, entry_id=e.id)
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/services/marketing/test_calendar_service.py -v`
Expected: FAIL (modules missing).

- [ ] **Step 3: Write the schemas**

```python
# app/schemas/marketing.py
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.db.models.enums import ChannelKey, ChannelStatus, ContentStatus


class CalendarEntryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    channel: ChannelKey
    status: ContentStatus = ContentStatus.draft
    body: str | None = None
    media_ref: str | None = Field(default=None, max_length=500)
    scheduled_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Title cannot be blank.")
        return v


class CalendarEntryUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    channel: ChannelKey | None = None
    status: ContentStatus | None = None
    body: str | None = None
    media_ref: str | None = Field(default=None, max_length=500)
    scheduled_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Title cannot be blank.")
        return v


class CalendarEntryResponse(BaseModel):
    id: uuid.UUID
    startup_id: uuid.UUID
    created_by: uuid.UUID | None
    title: str
    channel: ChannelKey
    status: ContentStatus
    body: str | None
    media_ref: str | None
    scheduled_at: datetime | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChannelUpdate(BaseModel):
    status: ChannelStatus | None = None
    notes: str | None = None


class ChannelResponse(BaseModel):
    id: uuid.UUID
    key: ChannelKey
    status: ChannelStatus
    notes: str | None


class OverviewResponse(BaseModel):
    scheduled_this_week: int
    active_channels: int
    active_campaigns: int | None = None
    top_channel_by_conversions: str | None = None
    ai_content_ideas: int | None = None
```

- [ ] **Step 4: Write the calendar service**

```python
# app/services/marketing/service.py
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import ChannelKey, ContentStatus
from app.db.models.marketing import ContentCalendarEntry
from app.platform.events import event_bus
from app.schemas.marketing import (
    CalendarEntryCreate,
    CalendarEntryResponse,
    CalendarEntryUpdate,
)


def _validation(field: str, message: str) -> AppError:
    return AppError("VALIDATION_ERROR", message, 422,
                    field_errors=[{"field": field, "message": message}])


def serialize_entry(e: ContentCalendarEntry) -> CalendarEntryResponse:
    return CalendarEntryResponse.model_validate(e, from_attributes=True)


def create_entry(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID, data: CalendarEntryCreate
) -> ContentCalendarEntry:
    if data.status == ContentStatus.scheduled and data.scheduled_at is None:
        raise _validation("scheduled_at", "A scheduled entry needs a scheduled_at time.")
    entry = ContentCalendarEntry(
        startup_id=startup_id, created_by=created_by, title=data.title, channel=data.channel,
        status=data.status, body=data.body, media_ref=data.media_ref,
        scheduled_at=data.scheduled_at,
    )
    if data.status == ContentStatus.published:
        entry.published_at = datetime.now(UTC)
    db.add(entry)
    db.flush()
    if entry.status == ContentStatus.published:
        _emit_published(db, entry, actor_id=created_by)
    return entry


def get_entry(db: Session, *, startup_id: uuid.UUID, entry_id: uuid.UUID) -> ContentCalendarEntry:
    entry = (
        db.query(ContentCalendarEntry)
        .filter_by(id=entry_id, startup_id=startup_id)
        .one_or_none()
    )
    if entry is None:
        raise NotFound()
    return entry


def list_entries(
    db: Session, *, startup_id: uuid.UUID,
    date_from: datetime | None = None, date_to: datetime | None = None,
    channel: ChannelKey | None = None, status: ContentStatus | None = None,
) -> list[ContentCalendarEntry]:
    q = db.query(ContentCalendarEntry).filter(ContentCalendarEntry.startup_id == startup_id)
    if date_from is not None:
        q = q.filter(ContentCalendarEntry.scheduled_at >= date_from)
    if date_to is not None:
        q = q.filter(ContentCalendarEntry.scheduled_at <= date_to)
    if channel is not None:
        q = q.filter(ContentCalendarEntry.channel == channel)
    if status is not None:
        q = q.filter(ContentCalendarEntry.status == status)
    return q.order_by(ContentCalendarEntry.scheduled_at.is_(None), ContentCalendarEntry.scheduled_at).all()


def update_entry(
    db: Session, *, startup_id: uuid.UUID, entry_id: uuid.UUID, actor_id: uuid.UUID,
    data: CalendarEntryUpdate,
) -> ContentCalendarEntry:
    entry = get_entry(db, startup_id=startup_id, entry_id=entry_id)
    fields = data.model_dump(exclude_unset=True)
    new_status = fields.get("status", entry.status)
    new_scheduled = fields["scheduled_at"] if "scheduled_at" in fields else entry.scheduled_at
    if new_status == ContentStatus.scheduled and new_scheduled is None:
        raise _validation("scheduled_at", "A scheduled entry needs a scheduled_at time.")
    for name, value in fields.items():
        setattr(entry, name, value)
    just_published = (
        new_status == ContentStatus.published and entry.published_at is None
    )
    if just_published:
        entry.published_at = datetime.now(UTC)
    db.flush()
    if just_published:
        _emit_published(db, entry, actor_id=actor_id)
    return entry


def delete_entry(db: Session, *, startup_id: uuid.UUID, entry_id: uuid.UUID) -> None:
    entry = get_entry(db, startup_id=startup_id, entry_id=entry_id)
    db.delete(entry)
    db.flush()


def _emit_published(db: Session, entry: ContentCalendarEntry, *, actor_id: uuid.UUID) -> None:
    event_bus.publish(db, "marketing.post.published", {
        "startup_id": str(entry.startup_id),
        "entry_id": str(entry.id),
        "title": entry.title,
        "channel": entry.channel.value,
        "actor_id": str(actor_id),
        "created_by": str(entry.created_by) if entry.created_by else None,
    })
```

- [ ] **Step 5: Run the tests — verify they pass**

Run: `poetry run pytest tests/services/marketing/test_calendar_service.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/schemas/marketing.py app/services/marketing/__init__.py app/services/marketing/service.py tests/services/marketing/test_calendar_service.py
git commit -m "feat(marketing): calendar service (CRUD, publish transition, post.published event)"
```

---

## Task 3: Channel service (lazy-seed + update) + overview

**Files:**
- Modify: `app/services/marketing/service.py`
- Test: `tests/services/marketing/test_channel_service.py`

**Interfaces:**
- Consumes: `MarketingChannel`, `ChannelKey`, `ChannelStatus` (Task 1); `ContentCalendarEntry`, `ContentStatus` (Task 1); `ChannelUpdate` (Task 2 schemas); `NotFound`; `IntegrityError`.
- Produces: `list_channels(db, *, startup_id) -> list[MarketingChannel]` (lazy-seeds 8, ordered by `ChannelKey`); `update_channel(db, *, startup_id, key, data: ChannelUpdate) -> MarketingChannel`; `overview(db, *, startup_id) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/marketing/test_channel_service.py
from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import NotFound
from app.db.models.enums import ChannelKey, ChannelStatus, ContentStatus
from app.schemas.marketing import CalendarEntryCreate, ChannelUpdate
from app.services.marketing import service as svc
from tests.factories import create_startup, create_user


def test_list_channels_seeds_all_eight_idempotently(db):
    s = create_startup(db, owner=create_user(db))
    first = svc.list_channels(db, startup_id=s.id)
    assert len(first) == 8
    assert {c.key for c in first} == set(ChannelKey)
    again = svc.list_channels(db, startup_id=s.id)  # second call: no duplicates
    assert len(again) == 8


def test_update_channel(db):
    s = create_startup(db, owner=create_user(db))
    svc.list_channels(db, startup_id=s.id)  # seed
    row = svc.update_channel(db, startup_id=s.id, key=ChannelKey.email,
                             data=ChannelUpdate(status=ChannelStatus.active, notes="warming up"))
    assert row.status == ChannelStatus.active and row.notes == "warming up"


def test_update_missing_channel_not_found(db):
    s = create_startup(db, owner=create_user(db))  # not seeded yet
    with pytest.raises(NotFound):
        svc.update_channel(db, startup_id=s.id, key=ChannelKey.email,
                           data=ChannelUpdate(status=ChannelStatus.active))


def test_overview_counts(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    svc.list_channels(db, startup_id=s.id)
    svc.update_channel(db, startup_id=s.id, key=ChannelKey.email, data=ChannelUpdate(status=ChannelStatus.active))
    svc.create_entry(db, startup_id=s.id, created_by=u.id, data=CalendarEntryCreate(
        title="This week", channel=ChannelKey.email, status=ContentStatus.scheduled,
        scheduled_at=datetime.now(UTC) + timedelta(days=1)))
    ov = svc.overview(db, startup_id=s.id)
    assert ov["active_channels"] == 1
    assert ov["scheduled_this_week"] >= 1
    assert ov["active_campaigns"] is None
    assert ov["top_channel_by_conversions"] is None
    assert ov["ai_content_ideas"] is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/services/marketing/test_channel_service.py -v`
Expected: FAIL (functions missing).

- [ ] **Step 3: Implement channel + overview service**

Append to `app/services/marketing/service.py` (extend the import block to add `ChannelStatus`, `MarketingChannel`, `ChannelUpdate`, `IntegrityError`, and `timedelta`):

```python
from datetime import timedelta  # add to datetime import line
from sqlalchemy.exc import IntegrityError
from app.db.models.enums import ChannelStatus  # extend existing enums import
from app.db.models.marketing import MarketingChannel  # extend existing marketing import
from app.schemas.marketing import ChannelUpdate  # extend existing schemas import


def list_channels(db: Session, *, startup_id: uuid.UUID) -> list[MarketingChannel]:
    existing = {
        c.key: c
        for c in db.query(MarketingChannel).filter_by(startup_id=startup_id).all()
    }
    missing = [k for k in ChannelKey if k not in existing]
    if missing:
        try:
            with db.begin_nested():
                for k in missing:
                    db.add(MarketingChannel(
                        startup_id=startup_id, key=k, status=ChannelStatus.not_started))
                db.flush()
        except IntegrityError:
            pass  # a concurrent first-read seeded them; re-read below
    rows = db.query(MarketingChannel).filter_by(startup_id=startup_id).all()
    order = {k: i for i, k in enumerate(ChannelKey)}
    return sorted(rows, key=lambda c: order[c.key])


def update_channel(
    db: Session, *, startup_id: uuid.UUID, key: ChannelKey, data: ChannelUpdate
) -> MarketingChannel:
    row = (
        db.query(MarketingChannel).filter_by(startup_id=startup_id, key=key).one_or_none()
    )
    if row is None:
        raise NotFound()
    fields = data.model_dump(exclude_unset=True)
    for name, value in fields.items():
        setattr(row, name, value)
    db.flush()
    return row


def _week_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


def overview(db: Session, *, startup_id: uuid.UUID) -> dict:
    start, end = _week_bounds(datetime.now(UTC))
    scheduled_this_week = (
        db.query(ContentCalendarEntry)
        .filter(
            ContentCalendarEntry.startup_id == startup_id,
            ContentCalendarEntry.status == ContentStatus.scheduled,
            ContentCalendarEntry.scheduled_at >= start,
            ContentCalendarEntry.scheduled_at < end,
        )
        .count()
    )
    active_channels = (
        db.query(MarketingChannel)
        .filter(MarketingChannel.startup_id == startup_id,
                MarketingChannel.status == ChannelStatus.active)
        .count()
    )
    return {
        "scheduled_this_week": scheduled_this_week,
        "active_channels": active_channels,
        "active_campaigns": None,           # Slice 2
        "top_channel_by_conversions": None,  # Slice 5
        "ai_content_ideas": None,            # Slice 3
    }
```

- [ ] **Step 4: Run the tests — verify they pass**

Run: `poetry run pytest tests/services/marketing/test_channel_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/marketing/service.py tests/services/marketing/test_channel_service.py
git commit -m "feat(marketing): channel lazy-seed + update + overview stats"
```

---

## Task 4: Endpoints + router registration

**Files:**
- Create: `app/api/v1/endpoints/marketing.py`
- Modify: `app/api/v1/api.py`
- Test: `tests/api/test_marketing.py`

**Interfaces:**
- Consumes: all Task 2/3 service fns + schemas; `require_role`, `require_workspace` (`app/db/tenancy.py`); `get_verified_user` (`app/api/deps.py`); `get_db`; `success_response` (`app/core/envelope`); `MembershipRole`.
- Produces: the `/marketing` routes listed in the spec. Serializers: `serialize_entry` (Task 2), and a `ChannelResponse.model_validate(row, from_attributes=True)` for channels.

- [ ] **Step 1: Write the failing endpoint tests**

```python
# tests/api/test_marketing.py
# Reuse this test module's existing helpers for an authenticated workspace client.
# Inspect a sibling API test (e.g. tests/api/test_learning.py) for the exact
# fixtures that mint a founder/team_member/other-role client + workspace, and use those.
from app.db.models.enums import ChannelKey, ChannelStatus, ContentStatus


def test_founder_can_crud_calendar_entry(founder_client):
    client, _ = founder_client
    r = client.post("/api/v1/marketing/calendar-entries",
        json={"title": "Launch", "channel": "email", "status": "draft"})
    assert r.status_code == 200
    eid = r.json()["data"]["id"]
    assert client.get(f"/api/v1/marketing/calendar-entries/{eid}").status_code == 200
    r2 = client.patch(f"/api/v1/marketing/calendar-entries/{eid}", json={"status": "published"})
    assert r2.status_code == 200 and r2.json()["data"]["published_at"] is not None
    assert client.get("/api/v1/marketing/calendar-entries").json()["data"]
    assert client.delete(f"/api/v1/marketing/calendar-entries/{eid}").status_code == 200


def test_scheduled_without_time_is_422(founder_client):
    client, _ = founder_client
    r = client.post("/api/v1/marketing/calendar-entries",
        json={"title": "X", "channel": "email", "status": "scheduled"})
    assert r.status_code == 422


def test_channels_seed_and_update(founder_client):
    client, _ = founder_client
    r = client.get("/api/v1/marketing/channels")
    assert r.status_code == 200 and len(r.json()["data"]) == 8
    r2 = client.patch("/api/v1/marketing/channels/email", json={"status": "active", "notes": "go"})
    assert r2.status_code == 200 and r2.json()["data"]["status"] == "active"


def test_overview_shape(founder_client):
    client, _ = founder_client
    data = client.get("/api/v1/marketing").json()["data"]
    assert set(data) == {"scheduled_this_week", "active_channels", "active_campaigns",
                         "top_channel_by_conversions", "ai_content_ideas"}


def test_rbac_non_marketing_role_forbidden(non_marketing_client):
    # a mentor/investor membership must be blocked from every marketing route
    client, _ = non_marketing_client
    assert client.get("/api/v1/marketing").status_code == 403
    assert client.get("/api/v1/marketing/channels").status_code == 403
    assert client.post("/api/v1/marketing/calendar-entries",
        json={"title": "X", "channel": "email"}).status_code == 403
```

> Fixture note: `founder_client` and `non_marketing_client` are illustrative names. Use the real authenticated-client fixtures/helpers already in the API test suite (see `tests/api/test_learning.py` / `tests/api/conftest.py`); mint a `founder` (or `team_member`) client for the allowed cases and a `mentor`/`investor` membership client for the RBAC case. What matters is the asserted behavior, not the fixture name.

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/api/test_marketing.py -v`
Expected: FAIL (routes 404 / module missing).

- [ ] **Step 3: Implement the endpoints**

```python
# app/api/v1/endpoints/marketing.py
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import ChannelKey, ContentStatus, MembershipRole
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.marketing import (
    CalendarEntryCreate,
    CalendarEntryUpdate,
    ChannelResponse,
    ChannelUpdate,
    OverviewResponse,
)
from app.services.marketing import service as svc

router = APIRouter()
_marketing = require_role(MembershipRole.founder, MembershipRole.team_member)


@router.get("", response_model=dict[str, Any])
def marketing_overview(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(OverviewResponse(**svc.overview(db, startup_id=membership.startup_id)).model_dump())


@router.post("/calendar-entries", response_model=dict[str, Any])
def create_entry(
    payload: CalendarEntryCreate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    entry = svc.create_entry(db, startup_id=membership.startup_id, created_by=user.id, data=payload)
    db.commit()
    return success_response(svc.serialize_entry(entry).model_dump())


@router.get("/calendar-entries", response_model=dict[str, Any])
def list_entries(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    date_from: datetime | None = Query(default=None, alias="from"),  # noqa: B008
    date_to: datetime | None = Query(default=None, alias="to"),  # noqa: B008
    channel: ChannelKey | None = Query(default=None),  # noqa: B008
    status: ContentStatus | None = Query(default=None),  # noqa: B008
) -> dict[str, Any]:
    rows = svc.list_entries(db, startup_id=membership.startup_id,
        date_from=date_from, date_to=date_to, channel=channel, status=status)
    return success_response({"entries": [svc.serialize_entry(e).model_dump() for e in rows]})


@router.get("/calendar-entries/{entry_id}", response_model=dict[str, Any])
def get_entry(
    entry_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(svc.serialize_entry(
        svc.get_entry(db, startup_id=membership.startup_id, entry_id=entry_id)).model_dump())


@router.patch("/calendar-entries/{entry_id}", response_model=dict[str, Any])
def update_entry(
    entry_id: uuid.UUID,
    payload: CalendarEntryUpdate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    entry = svc.update_entry(db, startup_id=membership.startup_id, entry_id=entry_id,
        actor_id=user.id, data=payload)
    db.commit()
    return success_response(svc.serialize_entry(entry).model_dump())


@router.delete("/calendar-entries/{entry_id}", response_model=dict[str, Any])
def delete_entry(
    entry_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    svc.delete_entry(db, startup_id=membership.startup_id, entry_id=entry_id)
    db.commit()
    return success_response({"deleted": True})


@router.get("/channels", response_model=dict[str, Any])
def list_channels(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = svc.list_channels(db, startup_id=membership.startup_id)
    db.commit()  # lazy-seed persists
    return success_response(
        [ChannelResponse.model_validate(r, from_attributes=True).model_dump() for r in rows])


@router.patch("/channels/{key}", response_model=dict[str, Any])
def update_channel(
    key: ChannelKey,
    payload: ChannelUpdate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.update_channel(db, startup_id=membership.startup_id, key=key, data=payload)
    db.commit()
    return success_response(ChannelResponse.model_validate(row, from_attributes=True).model_dump())
```

- [ ] **Step 4: Register the router**

In `app/api/v1/api.py`, add alongside the other includes:

```python
from app.api.v1.endpoints import marketing  # extend the existing endpoints import
api_router.include_router(marketing.router, prefix="/marketing", tags=["marketing"])
```

- [ ] **Step 5: Run the endpoint tests — verify they pass**

Run: `poetry run pytest tests/api/test_marketing.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/endpoints/marketing.py app/api/v1/api.py tests/api/test_marketing.py
git commit -m "feat(marketing): /marketing endpoints (calendar CRUD, channels, overview) + RBAC"
```

---

## Task 5: Published-post notification subscriber

**Files:**
- Modify: `app/services/notifications/registry.py`
- Test: `tests/services/notifications/test_marketing_notification.py`

**Interfaces:**
- Consumes: the `marketing.post.published` event emitted in Task 2 (`_emit_published`); the registry's `NotifSpec`, `_s`, `_members_minus_actor`, `SPECS`, `_handle`, `register`.
- Produces: a `SPECS["marketing.post.published"]` row whose title is `f"Scheduled post published: {payload['title']}"` and recipients are `_members_minus_actor` (all active members except the publisher, via `actor_id` in the payload).

- [ ] **Step 1: Write the failing test**

```python
# tests/services/notifications/test_marketing_notification.py
import uuid

from app.db.models.enums import ChannelKey, ContentStatus
from app.db.models.notification import Notification
from app.platform.events import DispatchingEventBus
from app.schemas.marketing import CalendarEntryCreate, CalendarEntryUpdate
from app.services.marketing import service as svc
from app.services.notifications import registry
from tests.factories import create_startup, create_user


def test_publishing_notifies_workspace_except_actor(db, monkeypatch):
    # Wire the registry onto a throwaway bus and point the marketing service at it.
    bus = DispatchingEventBus()
    registry.register(bus)
    monkeypatch.setattr(svc, "event_bus", bus)

    founder = create_user(db)
    s = create_startup(db, owner=founder)
    other = create_user(db)
    # add `other` as an active member of s (use the same helper the suite uses to add members;
    # see tests/factories.py / tests/api/conftest.py for add_member).
    from tests.factories import add_member  # if present; else create a Membership row directly
    add_member(db, startup=s, user=other)

    e = svc.create_entry(db, startup_id=s.id, created_by=founder.id, data=CalendarEntryCreate(
        title="Big launch", channel=ChannelKey.email, status=ContentStatus.draft))
    svc.update_entry(db, startup_id=s.id, entry_id=e.id, actor_id=founder.id,
        data=CalendarEntryUpdate(status=ContentStatus.published))

    notifs = db.query(Notification).filter_by(startup_id=s.id, type="marketing.post.published").all()
    recipients = {n.user_id for n in notifs}
    assert other.id in recipients          # workspace member notified
    assert founder.id not in recipients    # the actor (publisher) excluded
    assert all(n.title == "Scheduled post published: Big launch" for n in notifs)


def test_non_publish_update_creates_no_notification(db, monkeypatch):
    bus = DispatchingEventBus()
    registry.register(bus)
    monkeypatch.setattr(svc, "event_bus", bus)
    u = create_user(db)
    s = create_startup(db, owner=u)
    e = svc.create_entry(db, startup_id=s.id, created_by=u.id, data=CalendarEntryCreate(
        title="Draft", channel=ChannelKey.email, status=ContentStatus.draft))
    svc.update_entry(db, startup_id=s.id, entry_id=e.id, actor_id=u.id,
        data=CalendarEntryUpdate(title="Draft v2"))
    assert db.query(Notification).filter_by(type="marketing.post.published").count() == 0
```

> If `add_member` does not exist in `tests/factories.py`, create the `Membership` row inline (role `team_member`, status `active`) the way the API conftest does — inspect it and mirror.

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/services/notifications/test_marketing_notification.py -v`
Expected: FAIL (no spec row → no notification created).

- [ ] **Step 3: Add the spec row**

In `app/services/notifications/registry.py`, add to the `SPECS` dict (after the existing rows). Because the title is per-payload, construct the `NotifSpec` directly rather than via `_s`:

```python
    "marketing.post.published": NotifSpec(
        _members_minus_actor,
        lambda p: f"Scheduled post published: {p.get('title', 'a post')}",
        lambda _p: "",
    ),
```

(`_members_minus_actor` reads `actor_id` from the payload via `_actor`, so the publisher is excluded. No `categories.py` change — the event is unmapped, so `category_for` returns `None` and `email_enabled` safely returns `False`: in-app only for Slice 1.)

- [ ] **Step 4: Run the test — verify it passes**

Run: `poetry run pytest tests/services/notifications/test_marketing_notification.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/notifications/registry.py tests/services/notifications/test_marketing_notification.py
git commit -m "feat(marketing): in-app notification on marketing.post.published"
```

---

## Task 6: E2E journey + captures

**Files:**
- Create: `e2e/test_marketing.py`
- Produces: `e2e/_captures/marketing/*.json`

**Interfaces:**
- Consumes: the e2e harness fixtures (`base_url`, `make_verified_user`, `capture`) from `e2e/conftest.py`; the `/api/v1/marketing/*` routes.

- [ ] **Step 1: Write the e2e journey**

Model it on an existing journey (e.g. `e2e/test_journal.py`): onboard a verified founder + workspace, then exercise and capture:

```python
# e2e/test_marketing.py (shape — follow the existing harness's client/auth helpers)
def test_marketing_journey(base_url, make_verified_user, capture):
    # ... authenticate a founder client for a workspace (mirror test_journal.py) ...
    overview = get(client, "/api/v1/marketing")
    capture("marketing", "overview_before", overview)

    channels = get(client, "/api/v1/marketing/channels")
    assert len(channels["data"]) == 8
    capture("marketing", "channels", channels)

    updated_channel = patch(client, "/api/v1/marketing/channels/email",
        {"status": "active", "notes": "warming up"})
    capture("marketing", "channel_updated", updated_channel)

    created = post(client, "/api/v1/marketing/calendar-entries",
        {"title": "Launch week kickoff", "channel": "email", "status": "scheduled",
         "scheduled_at": "2026-10-01T09:00:00Z", "body": "Ship it"})
    capture("marketing", "entry_created", created)
    eid = created["data"]["id"]

    listed = get(client, "/api/v1/marketing/calendar-entries?from=2026-10-01T00:00:00Z&to=2026-10-31T00:00:00Z")
    capture("marketing", "entries_list", listed)

    published = patch(client, f"/api/v1/marketing/calendar-entries/{eid}", {"status": "published"})
    assert published["data"]["published_at"] is not None
    capture("marketing", "entry_published", published)

    overview_after = get(client, "/api/v1/marketing")
    capture("marketing", "overview_after", overview_after)
```

Use the exact request-helper style (`get`/`post`/`patch` wrappers, auth headers) that the existing e2e modules use — copy from `e2e/test_journal.py`.

- [ ] **Step 2: Run the e2e suite**

Run: `bash scripts/e2e_run.sh`
Expected: the marketing journey passes; capture files written under `e2e/_captures/marketing/`. (If the run fails on port 5432/6379, a squatting host service may need stopping — a prior task hit a Homebrew `postgresql@17` collision; report any machine-level change.)

- [ ] **Step 3: Commit**

```bash
git add e2e/test_marketing.py e2e/_captures/marketing/
git commit -m "test(e2e): marketing calendar + channels journey with captures"
```

---

## Task 7: FE guide + SOP + checklist

**Files:**
- Create: `docs/fe-integration-guide-marketing-calendar.md`
- Create: `docs/sop/2026-09-24-marketing-slice1.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

**Interfaces:**
- Consumes: the capture files from Task 6 (payloads pasted verbatim).

- [ ] **Step 1: FE guide**

Write `docs/fe-integration-guide-marketing-calendar.md` documenting every `/marketing` endpoint: method, auth (**founder/team_member only → 403 otherwise**), request + response bodies **pasted verbatim from `e2e/_captures/marketing/*.json`**, the `ChannelKey`/`ChannelStatus`/`ContentStatus` enum values, the **publish = `PATCH status:"published"`** flow (and its idempotency), the `422` when `status:"scheduled"` lacks `scheduled_at`, that `GET /channels` returns all 8 (seeded on first read), and the Overview's deferred-null fields with which slice fills each (`active_campaigns`→S2, `top_channel_by_conversions`→S5, `ai_content_ideas`→S3). Note the table names (`content_calendar`, `marketing_channels`). Match the tone/structure of an existing guide (e.g. `docs/fe-integration-guide-journal.md`).

- [ ] **Step 2: SOP**

Write `docs/sop/2026-09-24-marketing-slice1.md`: what shipped (calendar + channels + overview API, migration `0033`, `marketing.post.published` event + notification), why (Module 10 Slice 1 of 5 — the CRUD spine), how (mirrors journal/dashboard CRUD; AI deferred to Slice 3; grants deferred), files/migrations touched, verification (unit + e2e), and follow-ups (Slices 2–5, auto-publish scheduler, multi-channel, granular grants). Match the existing SOP style.

- [ ] **Step 3: Checklist**

In `docs/checklist/PROJECT_CHECKLIST.md`, add a Module 10 entry to the Upcoming/module section: mark **Slice 1 complete** (this branch), list Slices 2–5 as planned, and reflect the tally (Module 10 now in progress). Follow the file's existing per-module formatting.

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs(marketing): FE guide + SOP + checklist for Module 10 Slice 1"
```

---

## Final verification (before opening the PR)

- [ ] **Full local CI parity, all green:**

```bash
poetry run black --check app tests && poetry run isort --check-only app tests && \
poetry run ruff check app tests && poetry run mypy app && \
poetry run pylint app --fail-under=9.5 && poetry run bandit -q -r app && \
poetry run pytest --cov=app --cov-fail-under=95 && \
poetry run alembic upgrade head && poetry run alembic check && poetry run alembic heads && \
bash scripts/e2e_run.sh
```

Expected: every check passes; `alembic heads` shows the single head `0033_marketing_calendar_channels`.

- [ ] **No AI attribution** on any commit: `git log develop..HEAD --format='%an <%ae>%n%b'` shows no `Co-Authored-By`/`Claude-Session`/"Generated with".

---

## Self-Review

**Spec coverage:** entities+migration → Task 1; calendar CRUD + publish + event → Task 2; channels + overview → Task 3; endpoints + RBAC + router → Task 4; notification → Task 5; e2e/captures → Task 6; FE guide/SOP/checklist → Task 7. AI, campaigns, SEO, analytics correctly out of scope (later slices). The spec's "founders + creator" notification recipients is refined to `_members_minus_actor` (workspace minus publisher) to match the notifications-registry convention — recorded here as an intentional deviation.

**Placeholder scan:** every code/test step carries real content. Fixture names in Tasks 4–6 are explicitly flagged as illustrative with instructions to use the suite's real fixtures — the asserted behavior is concrete.

**Type consistency:** `serialize_entry`, `CalendarEntryCreate/Update/Response`, `ChannelUpdate/Response`, `OverviewResponse`, and the service signatures (`create_entry`/`update_entry(... actor_id ...)`/`list_channels`/`update_channel`/`overview`) match across producing (Tasks 2/3) and consuming (Task 4/5) tasks. `ChannelKey`/`ContentStatus`/`ChannelStatus` used consistently. The event name `marketing.post.published` matches between emit (Task 2), subscriber (Task 5), and e2e (Task 6).

**Review Focus:** all five items have owning tests — cross-tenant (Task 2 entries, Task 3 channels), publish idempotency (Task 2), scheduled-without-time (Task 2 + Task 4), RBAC (Task 4), channel lazy-seed idempotency (Task 3).

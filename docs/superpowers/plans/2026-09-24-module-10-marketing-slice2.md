# Module 10 Marketing Hub — Slice 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship campaign planning + reusable audience segments as backend API — segments CRUD (with persona link), campaigns CRUD, and a guarded draft→active→paused→completed lifecycle that emits `marketing.campaign.*` events + notifications.

**Architecture:** Extends the Slice 1 `marketing` module. New models (`AudienceSegment`, `Campaign`, `CampaignSegment` join) + migration `0034`; new focused service modules `segments.py` + `campaigns.py` (Slice 1's `service.py` stays calendar/channels); new schemas + endpoints on the existing `/marketing` router; two notifications-registry rows. AI channel-plan recommend and performance metrics are seamed to later slices.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 (`Mapped`, `Enum(X, native_enum=False, length=N)`, `JSONB`), Alembic, Postgres, pytest, Poetry.

**Spec:** `docs/superpowers/specs/2026-09-24-module-10-marketing-slice2-design.md`

## Global Constraints

- **No AI attribution** in any commit message, PR/issue body, or review comment. Binds every subagent; overrides any harness attribution reminder.
- **Reproduce CI locally and green before push** via the pinned toolchain (`poetry run …`): black, isort, ruff, mypy, pylint ≥ 9.5, bandit, pytest ≥ 95% coverage, **Migrations (fresh-DB round-trip + `alembic check` drift) — one new migration `0034`, single linear head off `0033_marketing_calendar_channels`**, e2e, **CodeQL**.
- **CodeQL test-hygiene (enforced required check):** in test code, never put a mutating call (`post`/`patch`/`delete`) inside an `assert` — extract to a variable first; never write implicit string concatenation inside a list/tuple literal — lift multi-fragment strings to a named variable. Both patterns fail CodeQL.
- **Enum style:** `enum.StrEnum` mapped via `Enum(Cls, native_enum=False, length=N)`. **FK columns** `index=True`. Models use `UUIDMixin, TimestampMixin, Base`. JSONB columns: `mapped_column(JSONB, nullable=False, default=dict)`.
- **Transaction discipline:** services end with `db.flush()` and never `commit`/`rollback`; **endpoints call `db.commit()`** before returning; event handlers never commit.
- **Access:** all marketing endpoints gated `require_role(MembershipRole.founder, MembershipRole.team_member)`; every read/write scoped by `membership.startup_id`; cross-tenant access → `NotFound`.
- **Money:** `budget` is an integer number of cents. **channel_mix** is `{ChannelKey value: percent 0–100}` (keys validated ∈ `ChannelKey`, values 0–100; sum not forced). **metrics** stays `{}` (Slice 5). AI channel-plan recommend deferred to Slice 3.
- SOP + checklist + FE guide updated in the same pass; FE-guide payloads verbatim from live e2e captures.

## Review Focus

1. **Cross-tenant `segment_ids` in campaign create/update** — a `segment_id` belonging to another workspace (or nonexistent) must yield `422`, never silently link or leak it. (Test in Task 3.)
2. **Illegal status transition** — e.g. `draft→completed`, `draft→paused`, `completed→active`, or re-launching a launched campaign must yield `422` and emit no event. (Test in Task 3.)
3. **`persona_id` referencing a non-persona record** — linking a `competitor`/`pricing`/`revenue_stream` record (or another tenant's record) as a segment persona must yield `422`. (Test in Task 2.)
4. **RBAC on every new route** — a `mentor`/`investor` membership must get `403` on all `/marketing/segments/*` and `/marketing/campaigns/*` routes. (Test in Task 4.)
5. **`channel_mix` with an unknown channel key or out-of-range percent** — must yield `422`. (Test in Task 3.)

---

## File Structure

- `app/db/models/enums.py` (modify) — add `CampaignObjective`, `CampaignStatus`.
- `app/db/models/marketing.py` (modify) — add `AudienceSegment`, `Campaign`, `CampaignSegment`; extend imports (`Date`, `Integer`, `JSONB`, `typing.Any`, the two new enums).
- `app/db/models/__init__.py` (modify) — register the three models.
- `alembic/versions/0034_marketing_campaigns_segments.py` (create).
- `app/schemas/marketing.py` (modify) — add segment + campaign schemas.
- `app/services/marketing/segments.py` (create), `app/services/marketing/campaigns.py` (create).
- `app/api/v1/endpoints/marketing.py` (modify) — add segment + campaign routes.
- `app/services/notifications/registry.py` (modify) — 2 `SPECS` rows.
- Tests under `tests/db/`, `tests/services/marketing/`, `tests/api/`, `tests/services/notifications/`, `e2e/`.
- Docs: `docs/fe-integration-guide-marketing-campaigns.md`, `docs/sop/2026-09-24-marketing-slice2.md`, `docs/checklist/PROJECT_CHECKLIST.md`.

---

## Task 1: Enums + models + migration 0034

**Files:**
- Modify: `app/db/models/enums.py`, `app/db/models/marketing.py`, `app/db/models/__init__.py`
- Create: `alembic/versions/0034_marketing_campaigns_segments.py`
- Test: `tests/db/test_marketing_models.py` (extend), `tests/test_marketing_migration.py` (extend)

**Interfaces:**
- Produces: `CampaignObjective(awareness|leads|sales|launch)`, `CampaignStatus(draft|active|paused|completed)`; `AudienceSegment` (`audience_segments`), `Campaign` (`campaigns`), `CampaignSegment` (`campaign_segments`, unique `(campaign_id, segment_id)`).

- [ ] **Step 1: Write the failing model tests**

Append to `tests/db/test_marketing_models.py`:

```python
def test_campaign_and_segment_persist(db):
    from app.db.models.enums import CampaignObjective, CampaignStatus
    from app.db.models.marketing import AudienceSegment, Campaign, CampaignSegment
    from tests.factories import create_startup, create_user

    s = create_startup(db, owner=create_user(db))
    seg = AudienceSegment(startup_id=s.id, name="SMB founders", definition={"rules": []}, est_size=1200)
    db.add(seg)
    camp = Campaign(
        startup_id=s.id, name="Q4 launch", objective=CampaignObjective.launch,
        budget=50000, channel_mix={"email": 60, "search": 40}, status=CampaignStatus.draft,
    )
    db.add(camp)
    db.flush()
    db.add(CampaignSegment(campaign_id=camp.id, segment_id=seg.id))
    db.flush()
    got = db.query(Campaign).filter_by(startup_id=s.id).one()
    assert got.objective == CampaignObjective.launch
    assert got.budget == 50000 and got.channel_mix == {"email": 60, "search": 40}
    assert got.metrics == {} and got.status == CampaignStatus.draft


def test_campaign_segment_unique(db):
    import pytest
    from sqlalchemy.exc import IntegrityError
    from app.db.models.enums import CampaignObjective
    from app.db.models.marketing import AudienceSegment, Campaign, CampaignSegment
    from tests.factories import create_startup, create_user

    s = create_startup(db, owner=create_user(db))
    seg = AudienceSegment(startup_id=s.id, name="A", definition={})
    camp = Campaign(startup_id=s.id, name="C", objective=CampaignObjective.leads)
    db.add_all([seg, camp])
    db.flush()
    db.add(CampaignSegment(campaign_id=camp.id, segment_id=seg.id))
    db.flush()
    db.add(CampaignSegment(campaign_id=camp.id, segment_id=seg.id))
    with pytest.raises(IntegrityError):
        db.flush()
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/db/test_marketing_models.py -k "campaign or segment" -v`
Expected: FAIL (imports missing).

- [ ] **Step 3: Add the enums**

Append to `app/db/models/enums.py`:

```python
class CampaignObjective(enum.StrEnum):
    awareness = "awareness"
    leads = "leads"
    sales = "sales"
    launch = "launch"


class CampaignStatus(enum.StrEnum):
    draft = "draft"
    active = "active"
    paused = "paused"
    completed = "completed"
```

- [ ] **Step 4: Add the models**

In `app/db/models/marketing.py`, extend imports and add the models:

```python
from typing import Any
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from app.db.models.enums import (
    CampaignObjective,
    CampaignStatus,
    ChannelKey,
    ChannelStatus,
    ContentStatus,
)
```

```python
class AudienceSegment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "audience_segments"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    est_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    persona_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("business_records.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )


class Campaign(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "campaigns"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    objective: Mapped[CampaignObjective] = mapped_column(
        Enum(CampaignObjective, native_enum=False, length=12), nullable=False
    )
    budget: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    channel_mix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, native_enum=False, length=12),
        nullable=False, default=CampaignStatus.draft,
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CampaignSegment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "campaign_segments"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    segment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("audience_segments.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    __table_args__ = (
        UniqueConstraint("campaign_id", "segment_id", name="uq_campaign_segment"),
    )
```

(Keep the existing `ContentCalendarEntry`/`MarketingChannel` classes; only extend imports and append.)

- [ ] **Step 5: Register the models**

In `app/db/models/__init__.py`, extend the marketing import:

```python
from app.db.models.marketing import (  # noqa: F401
    AudienceSegment, Campaign, CampaignSegment, ContentCalendarEntry, MarketingChannel,
)
```

- [ ] **Step 6: Run model tests — verify pass**

Run: `poetry run pytest tests/db/test_marketing_models.py -k "campaign or segment" -v`
Expected: PASS.

- [ ] **Step 7: Generate + adjust the migration**

Run `poetry run alembic revision --autogenerate -m "marketing_campaigns_segments"`, rename to `alembic/versions/0034_marketing_campaigns_segments.py`, set `revision = "0034_marketing_campaigns_segments"`, `down_revision = "0033_marketing_calendar_channels"`. `upgrade()` creates the three tables (adjust autogenerate to match; mirror `0033`):

```python
def upgrade() -> None:
    op.create_table(
        "audience_segments",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("est_size", sa.Integer(), nullable=True),
        sa.Column("persona_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], name=op.f("fk_audience_segments_startup_id_startups"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["persona_id"], ["business_records.id"], name=op.f("fk_audience_segments_persona_id_business_records"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audience_segments")),
    )
    op.create_index(op.f("ix_audience_segments_startup_id"), "audience_segments", ["startup_id"], unique=False)
    op.create_index(op.f("ix_audience_segments_persona_id"), "audience_segments", ["persona_id"], unique=False)
    op.create_table(
        "campaigns",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("objective", sa.Enum("awareness", "leads", "sales", "launch", native_enum=False, length=12), nullable=False),
        sa.Column("budget", sa.Integer(), server_default="0", nullable=False),
        sa.Column("channel_mix", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Enum("draft", "active", "paused", "completed", native_enum=False, length=12), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], name=op.f("fk_campaigns_startup_id_startups"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaigns")),
    )
    op.create_index(op.f("ix_campaigns_startup_id"), "campaigns", ["startup_id"], unique=False)
    op.create_table(
        "campaign_segments",
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("segment_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name=op.f("fk_campaign_segments_campaign_id_campaigns"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["segment_id"], ["audience_segments.id"], name=op.f("fk_campaign_segments_segment_id_audience_segments"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_segments")),
        sa.UniqueConstraint("campaign_id", "segment_id", name="uq_campaign_segment"),
    )
    op.create_index(op.f("ix_campaign_segments_campaign_id"), "campaign_segments", ["campaign_id"], unique=False)
    op.create_index(op.f("ix_campaign_segments_segment_id"), "campaign_segments", ["segment_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_campaign_segments_segment_id"), table_name="campaign_segments")
    op.drop_index(op.f("ix_campaign_segments_campaign_id"), table_name="campaign_segments")
    op.drop_table("campaign_segments")
    op.drop_index(op.f("ix_campaigns_startup_id"), table_name="campaigns")
    op.drop_table("campaigns")
    op.drop_index(op.f("ix_audience_segments_persona_id"), table_name="audience_segments")
    op.drop_index(op.f("ix_audience_segments_startup_id"), table_name="audience_segments")
    op.drop_table("audience_segments")
```

Ensure `from alembic import op`, `import sqlalchemy as sa`, and `from sqlalchemy.dialects import postgresql` are imported (autogenerate adds the last when JSONB is present).

- [ ] **Step 8: Extend the migration assertion test**

In `tests/test_marketing_migration.py`, add a second test (or extend) asserting `campaigns`, `audience_segments`, `campaign_segments` tables + the `uq_campaign_segment` constraint exist after `upgrade head`. **Lift the multi-fragment `-c` script into a named `check_script` variable** (never an implicit concat inside the `subprocess.run([...])` list — CodeQL).

- [ ] **Step 9: Verify round-trip, drift, single head**

Run: `poetry run alembic upgrade head && poetry run alembic downgrade -1 && poetry run alembic upgrade head && poetry run alembic check && poetry run alembic heads`
Expected: succeeds; no drift; single head `0034_marketing_campaigns_segments`. Then `poetry run pytest tests/db/test_marketing_models.py tests/test_marketing_migration.py -v` PASS.

- [ ] **Step 10: Commit**

```bash
git add app/db/models/enums.py app/db/models/marketing.py app/db/models/__init__.py \
  alembic/versions/0034_marketing_campaigns_segments.py tests/db/test_marketing_models.py tests/test_marketing_migration.py
git commit -m "feat(marketing): campaigns + audience_segments + join tables + enums (migration 0034)"
```

---

## Task 2: Segment schemas + segments service

**Files:**
- Modify: `app/schemas/marketing.py`
- Create: `app/services/marketing/segments.py`
- Test: `tests/services/marketing/test_segments_service.py`

**Interfaces:**
- Consumes: `AudienceSegment`, `Campaign`, `CampaignSegment` (Task 1); `BusinessRecord` + `RecordKind` (`app/db/models/business.py`, `app/db/models/enums.py`); `NotFound`/`AppError`; the existing `_validation` helper in `app/services/marketing/service.py`.
- Produces schemas: `SegmentCreate`, `SegmentUpdate`, `SegmentResponse`, `serialize_segment(seg) -> SegmentResponse`. Produces service fns: `create_segment(db, *, startup_id, data) -> AudienceSegment`; `list_segments(db, *, startup_id) -> list`; `get_segment(db, *, startup_id, segment_id) -> AudienceSegment`; `update_segment(db, *, startup_id, segment_id, data) -> AudienceSegment`; `delete_segment(db, *, startup_id, segment_id) -> None`; `segment_campaigns(db, *, startup_id, segment_id) -> list[Campaign]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/marketing/test_segments_service.py
import uuid
import pytest
from app.core.errors import AppError, NotFound
from app.db.models.business import BusinessRecord
from app.db.models.enums import CampaignObjective, RecordKind
from app.db.models.marketing import Campaign, CampaignSegment
from app.schemas.marketing import SegmentCreate, SegmentUpdate
from app.services.marketing import segments as svc
from tests.factories import create_startup, create_user


def _startup(db):
    return create_startup(db, owner=create_user(db))


def _persona(db, startup, kind=RecordKind.persona):
    rec = BusinessRecord(startup_id=startup.id, kind=kind, data={"name": "P"}, position=0)
    db.add(rec); db.flush()
    return rec


def test_create_and_get_segment(db):
    s = _startup(db)
    seg = svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="SMB", est_size=100))
    got = svc.get_segment(db, startup_id=s.id, segment_id=seg.id)
    assert got.name == "SMB" and got.est_size == 100


def test_create_with_valid_persona(db):
    s = _startup(db)
    p = _persona(db, s)
    seg = svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="X", persona_id=p.id))
    assert seg.persona_id == p.id


def test_persona_must_be_persona_kind(db):
    s = _startup(db)
    comp = _persona(db, s, kind=RecordKind.competitor)
    with pytest.raises(AppError) as ei:
        svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="X", persona_id=comp.id))
    assert ei.value.http_status == 422


def test_persona_other_tenant_rejected(db):
    s = _startup(db)
    other = _startup(db)
    p = _persona(db, other)
    with pytest.raises(AppError) as ei:
        svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="X", persona_id=p.id))
    assert ei.value.http_status == 422


def test_get_other_tenant_not_found(db):
    s = _startup(db)
    seg = svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="X"))
    with pytest.raises(NotFound):
        svc.get_segment(db, startup_id=_startup(db).id, segment_id=seg.id)


def test_segment_campaigns_lists_users(db):
    s = _startup(db)
    seg = svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="X"))
    camp = Campaign(startup_id=s.id, name="C", objective=CampaignObjective.leads)
    db.add(camp); db.flush()
    db.add(CampaignSegment(campaign_id=camp.id, segment_id=seg.id)); db.flush()
    used = svc.segment_campaigns(db, startup_id=s.id, segment_id=seg.id)
    assert [c.id for c in used] == [camp.id]


def test_delete_segment(db):
    s = _startup(db)
    seg = svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="X"))
    svc.delete_segment(db, startup_id=s.id, segment_id=seg.id)
    with pytest.raises(NotFound):
        svc.get_segment(db, startup_id=s.id, segment_id=seg.id)
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/services/marketing/test_segments_service.py -v`
Expected: FAIL (modules missing).

- [ ] **Step 3: Add the segment schemas**

Append to `app/schemas/marketing.py` (the file already imports `uuid`, `datetime`, `BaseModel`, `Field`, `field_validator`; add `Any` from typing and the new enums as needed):

```python
class SegmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    definition: dict[str, Any] = Field(default_factory=dict)
    est_size: int | None = Field(default=None, ge=0)
    persona_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be blank.")
        return v


class SegmentUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    definition: dict[str, Any] | None = None
    est_size: int | None = Field(default=None, ge=0)
    persona_id: uuid.UUID | None = None


class SegmentResponse(BaseModel):
    id: uuid.UUID
    startup_id: uuid.UUID
    name: str
    definition: dict[str, Any]
    est_size: int | None
    persona_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
```

(Add `from typing import Any` at the top of the file if not present.)

- [ ] **Step 4: Implement the segments service**

```python
# app/services/marketing/segments.py
import uuid

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.business import BusinessRecord
from app.db.models.enums import RecordKind
from app.db.models.marketing import AudienceSegment, Campaign, CampaignSegment
from app.schemas.marketing import SegmentCreate, SegmentResponse, SegmentUpdate
from app.services.marketing.service import _validation


def serialize_segment(seg: AudienceSegment) -> SegmentResponse:
    return SegmentResponse.model_validate(seg, from_attributes=True)


def _validate_persona(db: Session, startup_id: uuid.UUID, persona_id: uuid.UUID | None) -> None:
    if persona_id is None:
        return
    rec = (
        db.query(BusinessRecord)
        .filter_by(id=persona_id, startup_id=startup_id, kind=RecordKind.persona)
        .one_or_none()
    )
    if rec is None:
        raise _validation("persona_id", "persona_id must reference a persona in this workspace.")


def create_segment(db: Session, *, startup_id: uuid.UUID, data: SegmentCreate) -> AudienceSegment:
    _validate_persona(db, startup_id, data.persona_id)
    seg = AudienceSegment(
        startup_id=startup_id, name=data.name, definition=data.definition,
        est_size=data.est_size, persona_id=data.persona_id,
    )
    db.add(seg)
    db.flush()
    return seg


def get_segment(db: Session, *, startup_id: uuid.UUID, segment_id: uuid.UUID) -> AudienceSegment:
    seg = db.query(AudienceSegment).filter_by(id=segment_id, startup_id=startup_id).one_or_none()
    if seg is None:
        raise NotFound()
    return seg


def list_segments(db: Session, *, startup_id: uuid.UUID) -> list[AudienceSegment]:
    return (
        db.query(AudienceSegment)
        .filter_by(startup_id=startup_id)
        .order_by(AudienceSegment.created_at.desc())
        .all()
    )


def update_segment(
    db: Session, *, startup_id: uuid.UUID, segment_id: uuid.UUID, data: SegmentUpdate
) -> AudienceSegment:
    seg = get_segment(db, startup_id=startup_id, segment_id=segment_id)
    fields = data.model_dump(exclude_unset=True)
    if "persona_id" in fields:
        _validate_persona(db, startup_id, fields["persona_id"])
    for name, value in fields.items():
        setattr(seg, name, value)
    db.flush()
    return seg


def delete_segment(db: Session, *, startup_id: uuid.UUID, segment_id: uuid.UUID) -> None:
    seg = get_segment(db, startup_id=startup_id, segment_id=segment_id)
    db.delete(seg)
    db.flush()


def segment_campaigns(
    db: Session, *, startup_id: uuid.UUID, segment_id: uuid.UUID
) -> list[Campaign]:
    get_segment(db, startup_id=startup_id, segment_id=segment_id)  # tenancy check
    return (
        db.query(Campaign)
        .join(CampaignSegment, CampaignSegment.campaign_id == Campaign.id)
        .filter(CampaignSegment.segment_id == segment_id, Campaign.startup_id == startup_id)
        .order_by(Campaign.created_at.desc())
        .all()
    )
```

- [ ] **Step 5: Run tests — verify pass**

Run: `poetry run pytest tests/services/marketing/test_segments_service.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/schemas/marketing.py app/services/marketing/segments.py tests/services/marketing/test_segments_service.py
git commit -m "feat(marketing): audience segments service (CRUD, persona-kind validation, used-by)"
```

---

## Task 3: Campaign schemas + campaigns service (lifecycle + events)

**Files:**
- Modify: `app/schemas/marketing.py`
- Create: `app/services/marketing/campaigns.py`
- Test: `tests/services/marketing/test_campaigns_service.py`

**Interfaces:**
- Consumes: models + enums (Task 1); `AudienceSegment`/`CampaignSegment`; `event_bus`; `ChannelKey`; `_validation` (from `service.py`); `NotFound`.
- Produces schemas: `CampaignCreate`, `CampaignUpdate`, `CampaignResponse`, `serialize_campaign(c, segment_ids) -> CampaignResponse`. Produces service fns: `create_campaign(db, *, startup_id, data) -> Campaign`; `list_campaigns(db, *, startup_id)`; `get_campaign(db, *, startup_id, campaign_id) -> Campaign`; `campaign_segment_ids(db, campaign_id) -> list[uuid.UUID]`; `update_campaign(db, *, startup_id, campaign_id, actor_id, data) -> Campaign`; `delete_campaign(...)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/marketing/test_campaigns_service.py
import uuid
import pytest
from app.core.config import settings  # noqa: F401  (kept for parity; remove if unused)
from app.core.errors import AppError, NotFound
from app.db.models.enums import CampaignObjective, CampaignStatus
from app.platform import events as events_mod
from app.schemas.marketing import CampaignCreate, CampaignUpdate
from app.services.marketing import campaigns as svc
from app.services.marketing import segments as seg_svc
from app.schemas.marketing import SegmentCreate
from tests.factories import create_startup, create_user


def _startup(db):
    return create_startup(db, owner=create_user(db))


def _seg(db, s, name="X"):
    return seg_svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name=name))


def test_create_with_segments_and_channel_mix(db):
    s = _startup(db); u = create_user(db)
    seg = _seg(db, s)
    c = svc.create_campaign(db, startup_id=s.id, data=CampaignCreate(
        name="Q4", objective=CampaignObjective.launch, budget=50000,
        channel_mix={"email": 60, "search": 40}, segment_ids=[seg.id]))
    assert c.status == CampaignStatus.draft
    assert svc.campaign_segment_ids(db, c.id) == [seg.id]


def test_foreign_segment_id_rejected(db):
    s = _startup(db); other = _startup(db)
    foreign = _seg(db, other)
    with pytest.raises(AppError) as ei:
        svc.create_campaign(db, startup_id=s.id, data=CampaignCreate(
            name="C", objective=CampaignObjective.leads, segment_ids=[foreign.id]))
    assert ei.value.http_status == 422


def test_bad_channel_mix_rejected(db):
    s = _startup(db)
    with pytest.raises(Exception):  # pydantic ValidationError (422 at API)
        CampaignCreate(name="C", objective=CampaignObjective.leads, channel_mix={"email": 150})
    with pytest.raises(Exception):
        CampaignCreate(name="C", objective=CampaignObjective.leads, channel_mix={"not_a_channel": 10})


def test_launch_sets_timestamp_and_emits_once(db):
    s = _startup(db); u = create_user(db)
    c = svc.create_campaign(db, startup_id=s.id, data=CampaignCreate(name="C", objective=CampaignObjective.leads))
    seen = []
    real = events_mod.event_bus.publish
    def spy(dbx, e, p):
        if e == "marketing.campaign.launched": seen.append(p)
        return real(dbx, e, p)
    import pytest as _pt
    monkey = _pt.MonkeyPatch(); monkey.setattr(events_mod.event_bus, "publish", spy)
    try:
        svc.update_campaign(db, startup_id=s.id, campaign_id=c.id, actor_id=u.id,
            data=CampaignUpdate(status=CampaignStatus.active))
        assert c.launched_at is not None and len(seen) == 1 and seen[0]["actor_id"] == str(u.id)
    finally:
        monkey.undo()


def test_illegal_transition_rejected(db):
    s = _startup(db); u = create_user(db)
    c = svc.create_campaign(db, startup_id=s.id, data=CampaignCreate(name="C", objective=CampaignObjective.leads))
    with pytest.raises(AppError) as ei:  # draft -> completed is illegal
        svc.update_campaign(db, startup_id=s.id, campaign_id=c.id, actor_id=u.id,
            data=CampaignUpdate(status=CampaignStatus.completed))
    assert ei.value.http_status == 422


def test_full_lifecycle(db):
    s = _startup(db); u = create_user(db)
    c = svc.create_campaign(db, startup_id=s.id, data=CampaignCreate(name="C", objective=CampaignObjective.leads))
    svc.update_campaign(db, startup_id=s.id, campaign_id=c.id, actor_id=u.id, data=CampaignUpdate(status=CampaignStatus.active))
    svc.update_campaign(db, startup_id=s.id, campaign_id=c.id, actor_id=u.id, data=CampaignUpdate(status=CampaignStatus.paused))
    svc.update_campaign(db, startup_id=s.id, campaign_id=c.id, actor_id=u.id, data=CampaignUpdate(status=CampaignStatus.active))
    svc.update_campaign(db, startup_id=s.id, campaign_id=c.id, actor_id=u.id, data=CampaignUpdate(status=CampaignStatus.completed))
    assert c.status == CampaignStatus.completed and c.completed_at is not None


def test_get_other_tenant_not_found(db):
    s = _startup(db)
    c = svc.create_campaign(db, startup_id=s.id, data=CampaignCreate(name="C", objective=CampaignObjective.leads))
    with pytest.raises(NotFound):
        svc.get_campaign(db, startup_id=_startup(db).id, campaign_id=c.id)
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/services/marketing/test_campaigns_service.py -v`
Expected: FAIL (modules missing).

- [ ] **Step 3: Add the campaign schemas**

Append to `app/schemas/marketing.py` (add `from datetime import date` alongside the existing datetime import; import `CampaignObjective`, `CampaignStatus`, `ChannelKey` from enums):

```python
class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    objective: CampaignObjective
    budget: int = Field(default=0, ge=0)
    channel_mix: dict[str, float] = Field(default_factory=dict)
    segment_ids: list[uuid.UUID] = Field(default_factory=list)
    period_start: date | None = None
    period_end: date | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be blank.")
        return v

    @field_validator("channel_mix")
    @classmethod
    def _valid_channel_mix(cls, v: dict[str, float]) -> dict[str, float]:
        valid = {c.value for c in ChannelKey}
        for key, pct in v.items():
            if key not in valid:
                raise ValueError(f"Unknown channel: {key}")
            if not 0 <= pct <= 100:
                raise ValueError(f"channel_mix[{key}] must be between 0 and 100.")
        return v


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    objective: CampaignObjective | None = None
    budget: int | None = Field(default=None, ge=0)
    channel_mix: dict[str, float] | None = None
    status: CampaignStatus | None = None
    segment_ids: list[uuid.UUID] | None = None
    period_start: date | None = None
    period_end: date | None = None

    @field_validator("channel_mix")
    @classmethod
    def _valid_channel_mix(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return v
        valid = {c.value for c in ChannelKey}
        for key, pct in v.items():
            if key not in valid:
                raise ValueError(f"Unknown channel: {key}")
            if not 0 <= pct <= 100:
                raise ValueError(f"channel_mix[{key}] must be between 0 and 100.")
        return v


class CampaignResponse(BaseModel):
    id: uuid.UUID
    startup_id: uuid.UUID
    name: str
    objective: CampaignObjective
    budget: int
    channel_mix: dict[str, float]
    status: CampaignStatus
    metrics: dict[str, Any]
    segment_ids: list[uuid.UUID]
    period_start: date | None
    period_end: date | None
    launched_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 4: Implement the campaigns service**

```python
# app/services/marketing/campaigns.py
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import CampaignStatus
from app.db.models.marketing import AudienceSegment, Campaign, CampaignSegment
from app.platform.events import event_bus
from app.schemas.marketing import CampaignCreate, CampaignResponse, CampaignUpdate
from app.services.marketing.service import _validation

# legal (from, to) status transitions -> the event to emit (or None)
_TRANSITIONS: dict[tuple[CampaignStatus, CampaignStatus], str | None] = {
    (CampaignStatus.draft, CampaignStatus.active): "marketing.campaign.launched",
    (CampaignStatus.active, CampaignStatus.completed): "marketing.campaign.completed",
    (CampaignStatus.active, CampaignStatus.paused): None,
    (CampaignStatus.paused, CampaignStatus.active): None,
}


def campaign_segment_ids(db: Session, campaign_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (
        db.query(CampaignSegment.segment_id)
        .filter(CampaignSegment.campaign_id == campaign_id)
        .order_by(CampaignSegment.created_at)
        .all()
    )
    return [r[0] for r in rows]


def serialize_campaign(c: Campaign, segment_ids: list[uuid.UUID]) -> CampaignResponse:
    return CampaignResponse(
        id=c.id, startup_id=c.startup_id, name=c.name, objective=c.objective, budget=c.budget,
        channel_mix=c.channel_mix, status=c.status, metrics=c.metrics, segment_ids=segment_ids,
        period_start=c.period_start, period_end=c.period_end,
        launched_at=c.launched_at, completed_at=c.completed_at,
        created_at=c.created_at, updated_at=c.updated_at,
    )


def _validate_segment_ids(db: Session, startup_id: uuid.UUID, segment_ids: list[uuid.UUID]) -> None:
    if not segment_ids:
        return
    found = {
        r[0]
        for r in db.query(AudienceSegment.id)
        .filter(AudienceSegment.startup_id == startup_id, AudienceSegment.id.in_(segment_ids))
        .all()
    }
    missing = [str(sid) for sid in segment_ids if sid not in found]
    if missing:
        raise _validation("segment_ids", f"Unknown segment(s) for this workspace: {missing}")


def _set_segments(db: Session, campaign: Campaign, segment_ids: list[uuid.UUID]) -> None:
    db.query(CampaignSegment).filter(CampaignSegment.campaign_id == campaign.id).delete()
    for sid in dict.fromkeys(segment_ids):  # dedupe, preserve order
        db.add(CampaignSegment(campaign_id=campaign.id, segment_id=sid))
    db.flush()


def create_campaign(db: Session, *, startup_id: uuid.UUID, data: CampaignCreate) -> Campaign:
    _validate_segment_ids(db, startup_id, data.segment_ids)
    campaign = Campaign(
        startup_id=startup_id, name=data.name, objective=data.objective, budget=data.budget,
        channel_mix=data.channel_mix, status=CampaignStatus.draft,
        period_start=data.period_start, period_end=data.period_end,
    )
    db.add(campaign)
    db.flush()
    if data.segment_ids:
        _set_segments(db, campaign, data.segment_ids)
    return campaign


def get_campaign(db: Session, *, startup_id: uuid.UUID, campaign_id: uuid.UUID) -> Campaign:
    c = db.query(Campaign).filter_by(id=campaign_id, startup_id=startup_id).one_or_none()
    if c is None:
        raise NotFound()
    return c


def list_campaigns(db: Session, *, startup_id: uuid.UUID) -> list[Campaign]:
    return (
        db.query(Campaign).filter_by(startup_id=startup_id)
        .order_by(Campaign.created_at.desc()).all()
    )


def update_campaign(
    db: Session, *, startup_id: uuid.UUID, campaign_id: uuid.UUID, actor_id: uuid.UUID,
    data: CampaignUpdate,
) -> Campaign:
    campaign = get_campaign(db, startup_id=startup_id, campaign_id=campaign_id)
    fields = data.model_dump(exclude_unset=True)
    segment_ids = fields.pop("segment_ids", None)
    new_status = fields.pop("status", None)
    if segment_ids is not None:
        _validate_segment_ids(db, startup_id, segment_ids)
    for name, value in fields.items():
        setattr(campaign, name, value)
    event: str | None = None
    if new_status is not None and new_status != campaign.status:
        transition = (campaign.status, new_status)
        if transition not in _TRANSITIONS:
            raise _validation("status", f"Illegal transition {campaign.status.value} -> {new_status.value}.")
        event = _TRANSITIONS[transition]
        campaign.status = new_status
        if new_status == CampaignStatus.active and campaign.launched_at is None:
            campaign.launched_at = datetime.now(UTC)
        if new_status == CampaignStatus.completed:
            campaign.completed_at = datetime.now(UTC)
    if segment_ids is not None:
        _set_segments(db, campaign, segment_ids)
    db.flush()
    if event:
        event_bus.publish(db, event, {
            "startup_id": str(campaign.startup_id), "campaign_id": str(campaign.id),
            "name": campaign.name, "actor_id": str(actor_id),
        })
    return campaign


def delete_campaign(db: Session, *, startup_id: uuid.UUID, campaign_id: uuid.UUID) -> None:
    campaign = get_campaign(db, startup_id=startup_id, campaign_id=campaign_id)
    db.delete(campaign)
    db.flush()
```

- [ ] **Step 5: Run tests — verify pass**

Run: `poetry run pytest tests/services/marketing/test_campaigns_service.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/schemas/marketing.py app/services/marketing/campaigns.py tests/services/marketing/test_campaigns_service.py
git commit -m "feat(marketing): campaigns service (CRUD, guarded lifecycle, campaign.* events)"
```

---

## Task 4: Endpoints (segments + campaigns)

**Files:**
- Modify: `app/api/v1/endpoints/marketing.py`
- Test: `tests/api/test_marketing_campaigns.py`

**Interfaces:**
- Consumes: `segments` + `campaigns` service modules + their schemas (Tasks 2/3); the existing `_marketing` role dep and `success_response` already in `marketing.py`.
- Produces: `/marketing/segments*` and `/marketing/campaigns*` routes.

- [ ] **Step 1: Write the failing endpoint tests**

```python
# tests/api/test_marketing_campaigns.py
# Reuse the authenticated-client fixtures from the existing marketing API test module
# (see tests/api/test_marketing.py / tests/api/conftest.py) — a founder/team_member client
# for allowed cases and a mentor/investor membership client for the RBAC case.

def test_segment_and_campaign_crud(founder_client):
    client, _ = founder_client
    seg = client.post("/api/v1/marketing/segments", json={"name": "SMB", "est_size": 100})
    assert seg.status_code == 200
    sid = seg.json()["data"]["id"]
    camp = client.post("/api/v1/marketing/campaigns", json={
        "name": "Q4", "objective": "launch", "budget": 50000,
        "channel_mix": {"email": 60, "search": 40}, "segment_ids": [sid]})
    assert camp.status_code == 200
    cid = camp.json()["data"]["id"]
    assert client.get(f"/api/v1/marketing/campaigns/{cid}").json()["data"]["segment_ids"] == [sid]
    # launch
    launched = client.patch(f"/api/v1/marketing/campaigns/{cid}", json={"status": "active"})
    assert launched.status_code == 200 and launched.json()["data"]["launched_at"] is not None
    # used-by
    used = client.get(f"/api/v1/marketing/segments/{sid}/campaigns")
    assert used.status_code == 200 and any(c["id"] == cid for c in used.json()["data"]["campaigns"])


def test_illegal_transition_422(founder_client):
    client, _ = founder_client
    c = client.post("/api/v1/marketing/campaigns", json={"name": "C", "objective": "leads"})
    cid = c.json()["data"]["id"]
    r = client.patch(f"/api/v1/marketing/campaigns/{cid}", json={"status": "completed"})
    assert r.status_code == 422


def test_bad_channel_mix_422(founder_client):
    client, _ = founder_client
    r = client.post("/api/v1/marketing/campaigns",
        json={"name": "C", "objective": "leads", "channel_mix": {"email": 150}})
    assert r.status_code == 422


def test_rbac_non_marketing_role_forbidden(non_marketing_client):
    client, _ = non_marketing_client
    assert client.get("/api/v1/marketing/segments").status_code == 403
    assert client.get("/api/v1/marketing/campaigns").status_code == 403
    assert client.post("/api/v1/marketing/segments", json={"name": "X"}).status_code == 403
    assert client.post("/api/v1/marketing/campaigns",
        json={"name": "C", "objective": "leads"}).status_code == 403
```

> Fixture note: `founder_client` / `non_marketing_client` are illustrative — use the real fixtures/helpers from the marketing API test suite (mint a founder/team_member client and a mentor/investor client). **CodeQL hygiene:** extract any `client.post/patch/delete(...)` out of an `assert` into a variable (as done above).

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/api/test_marketing_campaigns.py -v`
Expected: FAIL (routes 404).

- [ ] **Step 3: Implement the endpoints**

In `app/api/v1/endpoints/marketing.py`, extend imports and append the routes (the router, `_marketing`, `success_response`, `Depends`, `get_verified_user`, `get_db`, `Membership`, `User` are already present):

```python
from app.schemas.marketing import (
    CampaignCreate,
    CampaignUpdate,
    SegmentCreate,
    SegmentUpdate,
)
from app.services.marketing import campaigns as campaigns_svc
from app.services.marketing import segments as segments_svc
```

```python
# ---- Audience segments ----
@router.post("/segments", response_model=dict[str, Any])
def create_segment(
    payload: SegmentCreate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    seg = segments_svc.create_segment(db, startup_id=membership.startup_id, data=payload)
    db.commit()
    return success_response(segments_svc.serialize_segment(seg).model_dump())


@router.get("/segments", response_model=dict[str, Any])
def list_segments(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = segments_svc.list_segments(db, startup_id=membership.startup_id)
    return success_response(
        {"segments": [segments_svc.serialize_segment(s).model_dump() for s in rows]})


@router.get("/segments/{segment_id}", response_model=dict[str, Any])
def get_segment(
    segment_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    seg = segments_svc.get_segment(db, startup_id=membership.startup_id, segment_id=segment_id)
    return success_response(segments_svc.serialize_segment(seg).model_dump())


@router.patch("/segments/{segment_id}", response_model=dict[str, Any])
def update_segment(
    segment_id: uuid.UUID,
    payload: SegmentUpdate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    seg = segments_svc.update_segment(
        db, startup_id=membership.startup_id, segment_id=segment_id, data=payload)
    db.commit()
    return success_response(segments_svc.serialize_segment(seg).model_dump())


@router.delete("/segments/{segment_id}", response_model=dict[str, Any])
def delete_segment(
    segment_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    segments_svc.delete_segment(db, startup_id=membership.startup_id, segment_id=segment_id)
    db.commit()
    return success_response({"deleted": True})


@router.get("/segments/{segment_id}/campaigns", response_model=dict[str, Any])
def segment_campaigns(
    segment_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = segments_svc.segment_campaigns(
        db, startup_id=membership.startup_id, segment_id=segment_id)
    data = [
        campaigns_svc.serialize_campaign(c, campaigns_svc.campaign_segment_ids(db, c.id)).model_dump()
        for c in rows
    ]
    return success_response({"campaigns": data})


# ---- Campaigns ----
def _campaign_body(db: Session, c) -> dict[str, Any]:
    return campaigns_svc.serialize_campaign(c, campaigns_svc.campaign_segment_ids(db, c.id)).model_dump()


@router.post("/campaigns", response_model=dict[str, Any])
def create_campaign(
    payload: CampaignCreate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    c = campaigns_svc.create_campaign(db, startup_id=membership.startup_id, data=payload)
    db.commit()
    return success_response(_campaign_body(db, c))


@router.get("/campaigns", response_model=dict[str, Any])
def list_campaigns(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = campaigns_svc.list_campaigns(db, startup_id=membership.startup_id)
    return success_response({"campaigns": [_campaign_body(db, c) for c in rows]})


@router.get("/campaigns/{campaign_id}", response_model=dict[str, Any])
def get_campaign(
    campaign_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    c = campaigns_svc.get_campaign(db, startup_id=membership.startup_id, campaign_id=campaign_id)
    return success_response(_campaign_body(db, c))


@router.patch("/campaigns/{campaign_id}", response_model=dict[str, Any])
def update_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignUpdate,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    c = campaigns_svc.update_campaign(
        db, startup_id=membership.startup_id, campaign_id=campaign_id, actor_id=user.id, data=payload)
    db.commit()
    return success_response(_campaign_body(db, c))


@router.delete("/campaigns/{campaign_id}", response_model=dict[str, Any])
def delete_campaign(
    campaign_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    campaigns_svc.delete_campaign(db, startup_id=membership.startup_id, campaign_id=campaign_id)
    db.commit()
    return success_response({"deleted": True})
```

- [ ] **Step 4: Run endpoint tests — verify pass**

Run: `poetry run pytest tests/api/test_marketing_campaigns.py -v`
Expected: PASS (existing `tests/api/test_marketing.py` still green).

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/endpoints/marketing.py tests/api/test_marketing_campaigns.py
git commit -m "feat(marketing): segment + campaign endpoints (CRUD, transitions, used-by) + RBAC"
```

---

## Task 5: Campaign lifecycle notifications

**Files:**
- Modify: `app/services/notifications/registry.py`
- Test: `tests/services/notifications/test_marketing_campaign_notification.py`

**Interfaces:**
- Consumes: the `marketing.campaign.launched` / `marketing.campaign.completed` events emitted in Task 3; the registry's `NotifSpec`, `_members_minus_actor`, `SPECS`.
- Produces: two `SPECS` rows with per-payload titles.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/notifications/test_marketing_campaign_notification.py
from app.db.models.enums import CampaignObjective, CampaignStatus, MembershipRole
from app.db.models.notification import Notification
from app.platform.events import DispatchingEventBus
from app.schemas.marketing import CampaignCreate, CampaignUpdate
from app.services.marketing import campaigns as svc
from app.services.notifications import registry
from tests.factories import create_membership, create_startup, create_user


def test_launch_notifies_workspace_except_actor(db, monkeypatch):
    bus = DispatchingEventBus()
    registry.register(bus)
    monkeypatch.setattr(svc, "event_bus", bus)
    founder = create_user(db)
    s = create_startup(db, owner=founder)
    other = create_user(db)
    create_membership(db, other, s, role=MembershipRole.team_member)
    c = svc.create_campaign(db, startup_id=s.id, data=CampaignCreate(name="Big", objective=CampaignObjective.launch))
    svc.update_campaign(db, startup_id=s.id, campaign_id=c.id, actor_id=founder.id,
        data=CampaignUpdate(status=CampaignStatus.active))
    notifs = db.query(Notification).filter_by(startup_id=s.id, type="marketing.campaign.launched").all()
    recipients = {n.user_id for n in notifs}
    assert other.id in recipients and founder.id not in recipients
    assert all(n.title == "Campaign launched: Big" for n in notifs)
```

> If `create_membership`'s signature differs, mirror the real one used elsewhere in the suite.

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/services/notifications/test_marketing_campaign_notification.py -v`
Expected: FAIL (no spec rows).

- [ ] **Step 3: Add the spec rows**

In `app/services/notifications/registry.py`, add to `SPECS`:

```python
    "marketing.campaign.launched": NotifSpec(
        _members_minus_actor,
        lambda p: f"Campaign launched: {p.get('name', 'a campaign')}",
        lambda _p: "",
    ),
    "marketing.campaign.completed": NotifSpec(
        _members_minus_actor,
        lambda p: f"Campaign completed: {p.get('name', 'a campaign')}",
        lambda _p: "",
    ),
```

(Unmapped in `categories.py` → in-app only; email deferred, same as Slice 1's `marketing.post.published`.)

- [ ] **Step 4: Run test — verify pass**

Run: `poetry run pytest tests/services/notifications/test_marketing_campaign_notification.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/notifications/registry.py tests/services/notifications/test_marketing_campaign_notification.py
git commit -m "feat(marketing): in-app notifications on campaign launched/completed"
```

---

## Task 6: E2E journey + captures

**Files:**
- Modify: `e2e/test_marketing.py` (extend, or add `e2e/test_marketing_campaigns.py`)
- Produces: `e2e/_captures/marketing/` campaign + segment captures.

**Interfaces:**
- Consumes: the e2e harness fixtures (`base_url`, `make_verified_user`, `capture`) + the request helpers used by `e2e/test_marketing.py`.

- [ ] **Step 1: Extend the live journey**

Add a segment→campaign→lifecycle journey (model it on the existing marketing journey in `e2e/test_marketing.py`): authenticate a founder client, then:

```python
    seg = post(client, "/api/v1/marketing/segments",
        {"name": "SMB founders", "est_size": 1200, "definition": {"rules": []}})
    capture("marketing", "segment_created", seg)
    sid = seg["data"]["id"]
    camp = post(client, "/api/v1/marketing/campaigns", {
        "name": "Q4 launch push", "objective": "launch", "budget": 50000,
        "channel_mix": {"email": 60, "search": 40}, "segment_ids": [sid]})
    capture("marketing", "campaign_created", camp)
    cid = camp["data"]["id"]
    capture("marketing", "campaigns_list", get(client, "/api/v1/marketing/campaigns"))
    launched = patch(client, f"/api/v1/marketing/campaigns/{cid}", {"status": "active"})
    assert launched["data"]["launched_at"] is not None
    capture("marketing", "campaign_launched", launched)
    capture("marketing", "campaign_paused", patch(client, f"/api/v1/marketing/campaigns/{cid}", {"status": "paused"}))
    patch(client, f"/api/v1/marketing/campaigns/{cid}", {"status": "active"})
    completed = patch(client, f"/api/v1/marketing/campaigns/{cid}", {"status": "completed"})
    capture("marketing", "campaign_completed", completed)
    capture("marketing", "segment_used_by", get(client, f"/api/v1/marketing/segments/{sid}/campaigns"))
```

Use the file's real request-helper style. **CodeQL:** keep mutating calls out of `assert`.

- [ ] **Step 2: Run e2e**

Run: `bash scripts/e2e_run.sh`
Expected: the marketing journey passes; new capture files written. (If it fails on ports 5432/6379, a squatting Homebrew `postgresql@17`/`redis` may need stopping — report any machine-level change.)

- [ ] **Step 3: Commit**

```bash
git add e2e/test_marketing.py e2e/_captures/marketing/
git commit -m "test(e2e): marketing campaigns + segments lifecycle journey with captures"
```

---

## Task 7: FE guide + SOP + checklist

**Files:**
- Create: `docs/fe-integration-guide-marketing-campaigns.md`
- Create: `docs/sop/2026-09-24-marketing-slice2.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

**Interfaces:**
- Consumes: the Task 6 capture files (payloads pasted verbatim).

- [ ] **Step 1: FE guide**

Write `docs/fe-integration-guide-marketing-campaigns.md`: every segment + campaign endpoint (method, auth founder/team_member → 403 otherwise), request + response bodies **verbatim from `e2e/_captures/marketing/segment_*.json` and `campaign_*.json`**, the `CampaignObjective`/`CampaignStatus` enum values, the `channel_mix` `{channel: percent}` shape (budget in **cents**; FE derives per-channel spend), the **guarded transition rules** (draft→active launches; active→completed; active↔paused; anything else → 422), the `422` on unknown channel key / out-of-range percent / foreign `segment_id` / non-persona `persona_id`, the used-by endpoint, and that `metrics` is `{}` until Slice 5. Cross-reference the Slice 1 calendar guide.

- [ ] **Step 2: SOP**

Write `docs/sop/2026-09-24-marketing-slice2.md`: what shipped (campaigns + segments API, migration `0034`, `marketing.campaign.launched|completed` events + notifications), why (Module 10 Slice 2 of 5), how (extends Slice 1's marketing module; new `campaigns.py`/`segments.py` services; percent channel_mix; cents budget; guarded lifecycle; persona-kind validation; join table), files/migrations touched, verification (unit + e2e), follow-ups (Slice 3 AI channel-plan recommend; Slice 5 metrics; campaign Content step; segment rule execution). Match the existing SOP style.

- [ ] **Step 3: Checklist**

In `docs/checklist/PROJECT_CHECKLIST.md`, update the Module 10 section: mark **Slice 2 complete** (this branch), leave Slices 3–5 planned. Follow the file's existing formatting; don't disturb unrelated sections.

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs(marketing): FE guide + SOP + checklist for Module 10 Slice 2"
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

Expected: every check passes; `alembic heads` shows the single head `0034_marketing_campaigns_segments`.

- [ ] **No AI attribution** on any commit: `git log develop..HEAD --format='%an <%ae>%n%b'` shows none.
- [ ] **CodeQL clean:** no mutating call inside an `assert`, no implicit string concatenation in a list literal, anywhere in the new test/code.

---

## Self-Review

**Spec coverage:** entities+migration → T1; segments (schemas+service+persona validation) → T2; campaigns (schemas+service+lifecycle+events) → T3; endpoints+RBAC → T4; notifications → T5; e2e/captures → T6; docs → T7. `channel_mix` percent + validation, budget cents, join table, guarded transitions, seamed AI/metrics all covered. AI channel-plan recommend and metrics correctly out of scope.

**Placeholder scan:** every code/test step carries real content; the only conditional note is Task 4/5/6 fixture/helper names (flagged as illustrative with instructions to use the suite's real ones).

**Type consistency:** `serialize_segment`, `serialize_campaign(c, segment_ids)`, `campaign_segment_ids`, `SegmentCreate/Update/Response`, `CampaignCreate/Update/Response`, and the service signatures match across producing (T2/T3) and consuming (T4/T5/T6) tasks. Event names `marketing.campaign.launched|completed` match between emit (T3), subscriber (T5), and e2e (T6). `_validation` reused from `service.py` (returns `AppError` with `http_status`).

**Review Focus:** all five have owning tests — cross-tenant segment_ids (T3), illegal transition (T3), non-persona persona_id (T2), RBAC 403 (T4), bad channel_mix (T3).

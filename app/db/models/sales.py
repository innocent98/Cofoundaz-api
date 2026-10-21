import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import (
    ActivityKind,
    ClosedReason,
    LeadSource,
    LeadStatus,
    StageKind,
)


def _startup_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def _user_fk() -> Mapped[uuid.UUID | None]:
    return mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class SalesSettings(TimestampMixin, Base):
    __tablename__ = "sales_settings"
    __table_args__ = (CheckConstraint("stall_days BETWEEN 3 AND 90", name="ck_sales_stall_days"),)

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    stall_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=14, server_default="14"
    )
    outreach_postal_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


class PipelineStage(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "pipeline_stages"
    __table_args__ = (
        CheckConstraint("win_probability BETWEEN 0 AND 100", name="ck_stage_win_probability"),
        Index(
            "uq_pipeline_stage_terminal_kind",
            "startup_id",
            "kind",
            unique=True,
            postgresql_where=text("kind IN ('won', 'lost') AND archived_at IS NULL"),
        ),
    )

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[StageKind] = mapped_column(
        Enum(StageKind, native_enum=False, length=10), nullable=False, default=StageKind.open
    )
    win_probability: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SalesAccount(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "sales_accounts"

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    website: Mapped[str | None] = mapped_column(String(300), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[uuid.UUID | None] = _user_fk()


Index(
    "uq_sales_account_name_ci",
    SalesAccount.startup_id,
    func.lower(SalesAccount.name),
    unique=True,
)


class SalesContact(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "sales_contacts"

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sales_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)


Index("ix_sales_contacts_startup_email_ci", SalesContact.startup_id, func.lower(SalesContact.email))


class SalesLead(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "sales_leads"

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    source: Mapped[LeadSource] = mapped_column(
        Enum(LeadSource, native_enum=False, length=10), nullable=False, default=LeadSource.manual
    )
    status: Mapped[LeadStatus] = mapped_column(
        Enum(LeadStatus, native_enum=False, length=15), nullable=False, default=LeadStatus.new
    )
    owner_id: Mapped[uuid.UUID | None] = _user_fk()
    converted_deal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("deals.id", ondelete="SET NULL"), nullable=True
    )
    converted_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sales_contacts.id", ondelete="SET NULL"), nullable=True
    )
    converted_account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sales_accounts.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (Index("ix_sales_leads_startup_status", "startup_id", "status"),)


Index("ix_sales_leads_startup_email_ci", SalesLead.startup_id, func.lower(SalesLead.email))


class Deal(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "deals"
    __table_args__ = (
        Index("ix_deals_startup_stage", "startup_id", "stage_id"),
        Index("ix_deals_startup_last_activity", "startup_id", "last_activity_at"),
    )

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sales_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sales_contacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    value_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="NGN", server_default="NGN"
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pipeline_stages.id", ondelete="RESTRICT"), nullable=False
    )
    next_step: Mapped[str | None] = mapped_column(String(300), nullable=True)
    next_step_due: Mapped[date | None] = mapped_column(Date, nullable=True)
    closed_reason: Mapped[ClosedReason | None] = mapped_column(
        Enum(ClosedReason, native_enum=False, length=12), nullable=True
    )
    closed_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_id: Mapped[uuid.UUID | None] = _user_fk()
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    stalled_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DealStageChange(UUIDMixin, Base):
    __tablename__ = "deal_stage_changes"

    deal_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pipeline_stages.id", ondelete="SET NULL"), nullable=True
    )
    to_stage_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pipeline_stages.id", ondelete="CASCADE"), nullable=False
    )
    changed_by: Mapped[uuid.UUID | None] = _user_fk()
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SalesActivity(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "sales_activities"
    __table_args__ = (
        CheckConstraint(
            "account_id IS NOT NULL OR contact_id IS NOT NULL OR deal_id IS NOT NULL",
            name="ck_sales_activity_target",
        ),
    )

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    kind: Mapped[ActivityKind] = mapped_column(
        Enum(ActivityKind, native_enum=False, length=10), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    author_id: Mapped[uuid.UUID | None] = _user_fk()
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sales_accounts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sales_contacts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

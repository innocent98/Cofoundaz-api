import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class CustomMetric(UUIDMixin, TimestampMixin, Base):
    """A founder-entered metric (D-ANA-2 b): users, MRR, churn, anything the platform cannot see.
    `key` is `custom.<slug>` and doubles as the catalog key and the `kpi_snapshots.metric`."""

    __tablename__ = "custom_metrics"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    unit: Mapped[str] = mapped_column(String(10), nullable=False)  # count | money | percent
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (UniqueConstraint("startup_id", "key", name="uq_custom_metric_key"),)


class CustomMetricPoint(UUIDMixin, TimestampMixin, Base):
    """One value per metric per month (`period` is the first day of the month)."""

    __tablename__ = "custom_metric_points"

    metric_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("custom_metrics.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    period: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    entered_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (UniqueConstraint("metric_id", "period", name="uq_custom_metric_point"),)


class AnalyticsReport(UUIDMixin, TimestampMixin, Base):
    """A saved report definition: blocks over the metric catalog (slice B). `version` is the
    optimistic lock (a stale PATCH is a 409). Delete is soft (`deleted_at`)."""

    __tablename__ = "analytics_reports"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    blocks_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    default_range_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    template_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_analytics_reports_startup_deleted", "startup_id", "deleted_at"),)


class AnalyticsReportShare(UUIDMixin, TimestampMixin, Base):
    """Founder-granted read of one report for one workspace member (mentor, business consultant,
    accountant, team member)."""

    __tablename__ = "analytics_report_shares"

    report_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("analytics_reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (UniqueConstraint("report_id", "user_id", name="uq_analytics_report_share"),)


class AnalyticsReportRun(UUIDMixin, TimestampMixin, Base):
    """One frozen run: the report's definition and every resolved number, as the requester was
    allowed to see them. Written once by the run job (pending -> ready | failed) and never edited
    after; it survives the report being edited or deleted (`report_id` goes null)."""

    __tablename__ = "analytics_report_runs"

    report_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("analytics_reports.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    tz: Mapped[str] = mapped_column(String(40), nullable=False, server_default="UTC")
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # pending | ready | failed
    snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    access_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(20), nullable=False)  # manual | schedule
    run_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_analytics_report_runs_startup", "startup_id", "created_at"),)

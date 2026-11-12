import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
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

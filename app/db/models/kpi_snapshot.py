import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class KpiSnapshot(UUIDMixin, TimestampMixin, Base):
    """One KPI value per workspace per metric per day (PRD 02.1 `kpi_snapshots`), captured by
    the nightly `scheduled.kpi.snapshot` job. Source of the dashboard tiles' week-over-week
    delta and sparkline, and of threshold-crossing alerts. `created_at` is the capture time."""

    __tablename__ = "kpi_snapshots"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    period: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)

    __table_args__ = (
        UniqueConstraint("startup_id", "metric", "period", name="uq_kpi_snapshot_metric_period"),
    )

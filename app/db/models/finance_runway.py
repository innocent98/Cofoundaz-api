import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class FinanceRunwaySettings(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "finance_runway_settings"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # user-editable assumptions (int32-bounded by the schema layer)
    mom_growth_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hiring_spend_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    one_off_costs_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # PRD 08.8 "Send to financial model": Business Builder revenue streams, used as the
    # 3-statement model's starting revenue while there are no revenue actuals (PRD 12.6).
    revenue_drivers: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # system-owned alert dedup state
    alert_is_low: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    alert_last_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import FinancialModelStatus


class FinancialModel(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "financial_models"

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
    )
    status: Mapped[FinancialModelStatus] = mapped_column(
        Enum(
            FinancialModelStatus,
            native_enum=False,
            length=12,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=FinancialModelStatus.generating,
    )
    horizon_months: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    assumptions: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    pnl: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    cash_flow: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    balance_sheet: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

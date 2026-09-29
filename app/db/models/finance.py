import uuid
from datetime import date

from sqlalchemy import Date, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import TransactionDirection, TransactionSource


class Transaction(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "transactions"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    direction: Mapped[TransactionDirection] = mapped_column(
        Enum(TransactionDirection, native_enum=False, length=8), nullable=False
    )
    source: Mapped[TransactionSource] = mapped_column(
        Enum(TransactionSource, native_enum=False, length=12),
        nullable=False,
        default=TransactionSource.manual,
    )
    external_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (Index("ix_transactions_startup_date", "startup_id", "date"),)

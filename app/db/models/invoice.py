import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import InvoiceStatus, InvoiceTerms


class Invoice(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "invoices"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    client_name: Mapped[str] = mapped_column(String(200), nullable=False)
    client_email: Mapped[str] = mapped_column(String(320), nullable=False)
    line_items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    subtotal_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tax_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    tax_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    # values_callable so the DB stores the enum *value* (e.g. "net_30"), matching the migration.
    terms: Mapped[InvoiceTerms] = mapped_column(
        Enum(
            InvoiceTerms,
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=InvoiceTerms.net_30,
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(
            InvoiceStatus,
            native_enum=False,
            length=8,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=InvoiceStatus.draft,
    )
    issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_remind: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )

    __table_args__ = (UniqueConstraint("startup_id", "number", name="uq_invoices_startup_number"),)

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class FinanceConnection(UUIDMixin, TimestampMixin, Base):
    """A founder's own Paystack/Flutterwave account (one live payment provider per workspace) or
    their linked bank account via Mono (one, beside the payment provider).

    Secrets are Fernet-encrypted and never returned; only key_last4, mode and account_name
    are exposed. provider/status hold the values of PaymentProvider/ConnectionStatus.
    """

    __tablename__ = "finance_connections"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Unguessable per-connection token used in the public webhook URL.
    public_id: Mapped[str] = mapped_column(String(43), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    key_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_hash_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    account_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Mono only: SHA-256 of the linked bank account id, so an inbound webhook can find its
    # connection without decrypting every row (the id itself stays Fernet-encrypted in
    # key_ciphertext).
    provider_account_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    sync_cursor: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    connected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("startup_id", "provider", name="uq_finance_connections_startup_provider"),
        # One live PAYMENT provider per workspace (Paystack or Flutterwave). The bank link (Mono)
        # sits beside it: D-FIN-10 needs both at once to tell a payout from other deposits.
        Index(
            "uq_finance_connections_one_live",
            "startup_id",
            unique=True,
            postgresql_where=text("status <> 'disconnected' AND provider <> 'mono'"),
        ),
    )


class ProviderEvent(UUIDMixin, TimestampMixin, Base):
    """Webhook delivery ledger, used to dedupe repeated or concurrent deliveries."""

    __tablename__ = "provider_events"

    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("finance_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)

    __table_args__ = (
        UniqueConstraint("connection_id", "event_id", name="uq_provider_events_connection_event"),
    )

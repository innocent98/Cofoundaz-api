import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import ApprovalAction, ApprovalStatus


class StaffApprovalRequest(UUIDMixin, TimestampMixin, Base):
    """A dangerous platform action one Super Admin asks a different Super Admin to approve.

    `requested_by` can never be `decided_by`. Nothing runs until approval; approval runs the action
    in the same transaction that marks the row approved.
    """

    __tablename__ = "staff_approval_requests"

    action: Mapped[ApprovalAction] = mapped_column(
        Enum(ApprovalAction, native_enum=False, length=30), nullable=False
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, native_enum=False, length=20),
        nullable=False,
        default=ApprovalStatus.pending,
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # Action parameters, e.g. the maintenance message.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # When set, only this Super Admin may decide the request.
    designated_approver: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the action did when it ran, e.g. `{"sessions_revoked": 12}`.
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        # One open request per person per action, so a requester can't pile them up.
        Index(
            "uq_staff_approval_pending_per_requester",
            "action",
            "requested_by",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_staff_approval_status_created", "status", "created_at"),
    )

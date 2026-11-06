"""Funding Hub slice C (PRD 14.3): the cap table. Holders, the entries they hold (shares, options,
SAFEs, notes), saved round scenarios and an audit trail of every change.

Founder only (D-FUND-5). Money is integer minor units in the round currency (D-FUND-7); shares are
integers. Every row carries `startup_id`; every query filters by it.
"""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


def _startup_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )


class CapHolder(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "cap_holders"

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('founder','employee','advisor','investor','pool')", name="ck_cap_holder_kind"
        ),
        Index("ix_cap_holders_startup", "startup_id"),
        # One unallocated option pool per workspace.
        Index(
            "uq_cap_holder_one_pool",
            "startup_id",
            unique=True,
            postgresql_where=text("kind = 'pool'"),
        ),
    )


class CapEntry(UUIDMixin, TimestampMixin, Base):
    """Stock and options carry `shares`; SAFEs and notes carry `amount_minor` and have no shares
    until a round converts them."""

    __tablename__ = "cap_entries"

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    holder_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cap_holders.id", ondelete="CASCADE"), nullable=False
    )
    security: Mapped[str] = mapped_column(String(10), nullable=False)
    shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    price_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # per share
    amount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    valuation_cap_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    discount_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    safe_kind: Mapped[str | None] = mapped_column(String(10), nullable=True)
    mfn: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    interest_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    vesting_cliff_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vesting_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vesting_start_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "security IN ('common','preferred','option','safe','note')",
            name="ck_cap_entry_security",
        ),
        CheckConstraint(
            "(security IN ('common','preferred','option') AND shares > 0"
            " AND amount_minor IS NULL AND valuation_cap_minor IS NULL AND discount_bps IS NULL"
            " AND safe_kind IS NULL AND interest_bps IS NULL AND NOT mfn)"
            " OR (security IN ('safe','note') AND shares IS NULL AND amount_minor > 0"
            " AND price_minor IS NULL AND safe_kind IS NOT NULL"
            " AND vesting_months IS NULL AND vesting_start_on IS NULL)",
            name="ck_cap_entry_shape",
        ),
        CheckConstraint("safe_kind IN ('post_money','pre_money')", name="ck_cap_entry_safe_kind"),
        CheckConstraint(
            "discount_bps IS NULL OR (discount_bps > 0 AND discount_bps < 10000)",
            name="ck_cap_entry_discount",
        ),
        CheckConstraint(
            "valuation_cap_minor IS NULL OR valuation_cap_minor > 0", name="ck_cap_entry_cap"
        ),
        CheckConstraint(
            "interest_bps IS NULL OR (interest_bps >= 0 AND interest_bps <= 10000"
            " AND security = 'note')",
            name="ck_cap_entry_interest",
        ),
        CheckConstraint("price_minor IS NULL OR price_minor >= 0", name="ck_cap_entry_price"),
        CheckConstraint(
            "(vesting_months IS NULL) = (vesting_start_on IS NULL)", name="ck_cap_entry_vesting_set"
        ),
        CheckConstraint(
            "vesting_months IS NULL OR (vesting_months > 0 AND vesting_cliff_months >= 0"
            " AND vesting_cliff_months <= vesting_months)",
            name="ck_cap_entry_vesting",
        ),
        Index("ix_cap_entries_startup", "startup_id"),
        Index("ix_cap_entries_holder", "holder_id"),
    )


class CapScenario(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "cap_scenarios"

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (Index("ix_cap_scenarios_startup", "startup_id", "created_at"),)


class CapAudit(UUIDMixin, TimestampMixin, Base):
    """Every create, update and delete of a holder, entry or scenario, with before and after. Kept
    here, not in the platform `audit_log`, because it holds equity data that must stay
    founder-only."""

    __tablename__ = "cap_audit"

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint("entity_type IN ('holder','entry','scenario')", name="ck_cap_audit_entity"),
        CheckConstraint("action IN ('create','update','delete')", name="ck_cap_audit_action"),
        Index("ix_cap_audit_startup", "startup_id", "created_at"),
    )

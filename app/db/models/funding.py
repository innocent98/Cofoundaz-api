"""Funding Hub slice A (PRD Module 14): the raise (one round) and the investor pipeline.

Money is integer minor units in the round's currency (D-FUND-7: one currency per round, no FX).
Every row carries `startup_id`; every query filters by it.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class FundingRound(UUIDMixin, TimestampMixin, Base):
    """The raise. D-FUND-L1: one open (planning or open) round per workspace, history is kept."""

    __tablename__ = "funding_rounds"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="planning")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    target_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pre_money_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('planning','open','closed')", name="ck_funding_round_status"),
        CheckConstraint("currency IN ('NGN','USD')", name="ck_funding_round_currency"),
        CheckConstraint(
            "target_minor IS NULL OR target_minor >= 0", name="ck_funding_round_target_nonneg"
        ),
        CheckConstraint(
            "pre_money_minor IS NULL OR pre_money_minor >= 0", name="ck_funding_round_pre_nonneg"
        ),
        Index("ix_funding_rounds_startup", "startup_id"),
        # Database-level guarantee behind D-FUND-L1: at most one unfinished round per workspace.
        Index(
            "uq_funding_round_one_active",
            "startup_id",
            unique=True,
            postgresql_where=text("status <> 'closed'"),
        ),
    )


class Investor(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "investors"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    round_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("funding_rounds.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    firm: Mapped[str | None] = mapped_column(String(160), nullable=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False, default="angel")
    stage: Mapped[str] = mapped_column(String(20), nullable=False, default="research")
    warm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    check_min_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    check_max_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    committed_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    next_step: Mapped[str | None] = mapped_column(String(300), nullable=True)
    next_step_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_touch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    thesis_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    # Reserved for the curated directory (D-FUND-1, follows this slice); no FK until it exists.
    directory_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    passed_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    stage_entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Set once the founder confirms the money arrived (D-FUND-L3); blocks a double ledger entry.
    recorded_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint("type IN ('angel','vc','syndicate','other')", name="ck_investor_type"),
        CheckConstraint(
            "stage IN ('research','outreach','meeting','diligence','term_sheet','committed',"
            "'passed')",
            name="ck_investor_stage",
        ),
        CheckConstraint("source IN ('manual','directory')", name="ck_investor_source"),
        CheckConstraint(
            "passed_reason IS NULL OR passed_reason IN "
            "('valuation','stage_mismatch','sector_conflict','ghosted','other')",
            name="ck_investor_passed_reason",
        ),
        CheckConstraint(
            "(stage = 'passed') = (passed_reason IS NOT NULL)", name="ck_investor_passed_has_reason"
        ),
        CheckConstraint(
            "check_min_minor IS NULL OR check_min_minor >= 0", name="ck_investor_check_min"
        ),
        CheckConstraint(
            "check_max_minor IS NULL OR check_max_minor >= 0", name="ck_investor_check_max"
        ),
        CheckConstraint(
            "check_min_minor IS NULL OR check_max_minor IS NULL "
            "OR check_max_minor >= check_min_minor",
            name="ck_investor_check_order",
        ),
        CheckConstraint(
            "committed_minor IS NULL OR committed_minor >= 0", name="ck_investor_committed"
        ),
        Index("ix_investors_startup_round", "startup_id", "round_id"),
        Index("ix_investors_round_stage", "round_id", "stage", "position"),
    )


class InvestorEvent(UUIDMixin, TimestampMixin, Base):
    """The interaction log. `stage_change` rows are written by the system and feed analytics."""

    __tablename__ = "investor_events"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    investor_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("investors.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('stage_change','note','email','call','meeting')",
            name="ck_investor_event_kind",
        ),
        Index("ix_investor_events_investor_time", "investor_id", "occurred_at"),
        Index("ix_investor_events_startup", "startup_id"),
    )

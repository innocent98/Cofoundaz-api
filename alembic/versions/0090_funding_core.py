"""Funding Hub slice A: rounds, investors, interaction log

Revision ID: 0090_funding_core
Revises: 0089_readiness_scores
Create Date: 2026-11-05

Adds `funding_rounds` (one unfinished round per workspace, enforced by a partial unique index),
`investors` (the pipeline, fixed seven stages) and `investor_events` (interaction log and stage
history). Downgrade drops all three.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0090_funding_core"
down_revision = "0089_readiness_scores"
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _startup() -> sa.Column:
    return sa.Column(
        "startup_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
    )


def _stamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "funding_rounds",
        _id(),
        _startup(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("target_minor", sa.BigInteger(), nullable=True),
        sa.Column("pre_money_minor", sa.BigInteger(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.CheckConstraint(
            "status IN ('planning','open','closed')", name="ck_funding_round_status"
        ),
        sa.CheckConstraint("currency IN ('NGN','USD')", name="ck_funding_round_currency"),
        sa.CheckConstraint(
            "target_minor IS NULL OR target_minor >= 0", name="ck_funding_round_target_nonneg"
        ),
        sa.CheckConstraint(
            "pre_money_minor IS NULL OR pre_money_minor >= 0", name="ck_funding_round_pre_nonneg"
        ),
    )
    op.create_index("ix_funding_rounds_startup", "funding_rounds", ["startup_id"])
    op.create_index(
        "uq_funding_round_one_active",
        "funding_rounds",
        ["startup_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'closed'"),
    )

    op.create_table(
        "investors",
        _id(),
        _startup(),
        sa.Column(
            "round_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("funding_rounds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("firm", sa.String(160), nullable=True),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("warm", sa.Boolean(), nullable=False),
        sa.Column("check_min_minor", sa.BigInteger(), nullable=True),
        sa.Column("check_max_minor", sa.BigInteger(), nullable=True),
        sa.Column("committed_minor", sa.BigInteger(), nullable=True),
        sa.Column("next_step", sa.String(300), nullable=True),
        sa.Column("next_step_on", sa.Date(), nullable=True),
        sa.Column("last_touch_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("thesis_notes", sa.Text(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("directory_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("passed_reason", sa.String(20), nullable=True),
        sa.Column("stage_entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "recorded_transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_stamps(),
        sa.CheckConstraint("type IN ('angel','vc','syndicate','other')", name="ck_investor_type"),
        sa.CheckConstraint(
            "stage IN ('research','outreach','meeting','diligence','term_sheet','committed',"
            "'passed')",
            name="ck_investor_stage",
        ),
        sa.CheckConstraint("source IN ('manual','directory')", name="ck_investor_source"),
        sa.CheckConstraint(
            "passed_reason IS NULL OR passed_reason IN "
            "('valuation','stage_mismatch','sector_conflict','ghosted','other')",
            name="ck_investor_passed_reason",
        ),
        sa.CheckConstraint(
            "(stage = 'passed') = (passed_reason IS NOT NULL)", name="ck_investor_passed_has_reason"
        ),
        sa.CheckConstraint(
            "check_min_minor IS NULL OR check_min_minor >= 0", name="ck_investor_check_min"
        ),
        sa.CheckConstraint(
            "check_max_minor IS NULL OR check_max_minor >= 0", name="ck_investor_check_max"
        ),
        sa.CheckConstraint(
            "check_min_minor IS NULL OR check_max_minor IS NULL "
            "OR check_max_minor >= check_min_minor",
            name="ck_investor_check_order",
        ),
        sa.CheckConstraint(
            "committed_minor IS NULL OR committed_minor >= 0", name="ck_investor_committed"
        ),
    )
    op.create_index("ix_investors_startup_round", "investors", ["startup_id", "round_id"])
    op.create_index("ix_investors_round_stage", "investors", ["round_id", "stage", "position"])

    op.create_table(
        "investor_events",
        _id(),
        _startup(),
        sa.Column(
            "investor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("from_stage", sa.String(20), nullable=True),
        sa.Column("to_stage", sa.String(20), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_stamps(),
        sa.CheckConstraint(
            "kind IN ('stage_change','note','email','call','meeting')",
            name="ck_investor_event_kind",
        ),
    )
    op.create_index(
        "ix_investor_events_investor_time", "investor_events", ["investor_id", "occurred_at"]
    )
    op.create_index("ix_investor_events_startup", "investor_events", ["startup_id"])


def downgrade() -> None:
    op.drop_table("investor_events")
    op.drop_table("investors")
    op.drop_table("funding_rounds")

"""Funding Hub slice C: the cap table

Revision ID: 0099_funding_cap_table
Revises: 0098_ai_orchestrator
Create Date: 2026-11-06

Adds `cap_holders`, `cap_entries` (stock, options, SAFEs, notes; the CHECKs keep the shape of each
security honest), `cap_scenarios` (saved round models) and `cap_audit` (before/after of every
change). Downgrade drops all four.

CHAIN NOTE: chained on develop's head, 0098 (AI orchestrator). Auth (0096 or its successor) is
also in flight; whichever merges second gets a one-line re-chain.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0099_funding_cap_table"
down_revision = "0098_ai_orchestrator"
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


def _user_fk(name: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


def upgrade() -> None:
    op.create_table(
        "cap_holders",
        _id(),
        _startup(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_stamps(),
        sa.CheckConstraint(
            "kind IN ('founder','employee','advisor','investor','pool')", name="ck_cap_holder_kind"
        ),
    )
    op.create_index("ix_cap_holders_startup", "cap_holders", ["startup_id"])
    op.create_index(
        "uq_cap_holder_one_pool",
        "cap_holders",
        ["startup_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'pool'"),
    )

    op.create_table(
        "cap_entries",
        _id(),
        _startup(),
        sa.Column(
            "holder_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cap_holders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("security", sa.String(10), nullable=False),
        sa.Column("shares", sa.BigInteger(), nullable=True),
        sa.Column("price_minor", sa.BigInteger(), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("valuation_cap_minor", sa.BigInteger(), nullable=True),
        sa.Column("discount_bps", sa.Integer(), nullable=True),
        sa.Column("safe_kind", sa.String(10), nullable=True),
        sa.Column("mfn", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("interest_bps", sa.Integer(), nullable=True),
        sa.Column("issued_on", sa.Date(), nullable=True),
        sa.Column("vesting_cliff_months", sa.Integer(), nullable=True),
        sa.Column("vesting_months", sa.Integer(), nullable=True),
        sa.Column("vesting_start_on", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_stamps(),
        sa.CheckConstraint(
            "security IN ('common','preferred','option','safe','note')", name="ck_cap_entry_security"
        ),
        sa.CheckConstraint(
            "(security IN ('common','preferred','option') AND shares > 0"
            " AND amount_minor IS NULL AND valuation_cap_minor IS NULL AND discount_bps IS NULL"
            " AND safe_kind IS NULL AND interest_bps IS NULL AND NOT mfn)"
            " OR (security IN ('safe','note') AND shares IS NULL AND amount_minor > 0"
            " AND price_minor IS NULL AND safe_kind IS NOT NULL"
            " AND vesting_months IS NULL AND vesting_start_on IS NULL)",
            name="ck_cap_entry_shape",
        ),
        sa.CheckConstraint("safe_kind IN ('post_money','pre_money')", name="ck_cap_entry_safe_kind"),
        sa.CheckConstraint(
            "discount_bps IS NULL OR (discount_bps > 0 AND discount_bps < 10000)",
            name="ck_cap_entry_discount",
        ),
        sa.CheckConstraint(
            "valuation_cap_minor IS NULL OR valuation_cap_minor > 0", name="ck_cap_entry_cap"
        ),
        sa.CheckConstraint(
            "interest_bps IS NULL OR (interest_bps >= 0 AND interest_bps <= 10000"
            " AND security = 'note')",
            name="ck_cap_entry_interest",
        ),
        sa.CheckConstraint("price_minor IS NULL OR price_minor >= 0", name="ck_cap_entry_price"),
        sa.CheckConstraint(
            "(vesting_months IS NULL) = (vesting_start_on IS NULL)", name="ck_cap_entry_vesting_set"
        ),
        sa.CheckConstraint(
            "vesting_months IS NULL OR (vesting_months > 0 AND vesting_cliff_months >= 0"
            " AND vesting_cliff_months <= vesting_months)",
            name="ck_cap_entry_vesting",
        ),
    )
    op.create_index("ix_cap_entries_startup", "cap_entries", ["startup_id"])
    op.create_index("ix_cap_entries_holder", "cap_entries", ["holder_id"])

    op.create_table(
        "cap_scenarios",
        _id(),
        _startup(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        _user_fk("created_by_id"),
        *_stamps(),
    )
    op.create_index("ix_cap_scenarios_startup", "cap_scenarios", ["startup_id", "created_at"])

    op.create_table(
        "cap_audit",
        _id(),
        _startup(),
        _user_fk("actor_id"),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        *_stamps(),
        sa.CheckConstraint(
            "entity_type IN ('holder','entry','scenario')", name="ck_cap_audit_entity"
        ),
        sa.CheckConstraint("action IN ('create','update','delete')", name="ck_cap_audit_action"),
    )
    op.create_index("ix_cap_audit_startup", "cap_audit", ["startup_id", "created_at"])


def downgrade() -> None:
    op.drop_table("cap_audit")
    op.drop_table("cap_scenarios")
    op.drop_table("cap_entries")
    op.drop_table("cap_holders")

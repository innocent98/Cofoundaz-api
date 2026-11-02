"""finance runway settings

Revision ID: 0040_finance_runway
Revises: 0039_finance_transactions
Create Date: 2026-09-29

Module 12 Slice 2 (Runway & Scenarios): one settings row per startup holding the user-editable
scenario assumptions plus the system-owned low-runway alert dedup state. One new table,
additive, no lock on existing tables. startup_id is uniquely indexed (unique=True + index=True
on the model collapses to a single unique index, so no separate unique constraint).
"""

from alembic import op
import sqlalchemy as sa

revision = "0040_finance_runway"
down_revision = "0039_finance_transactions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finance_runway_settings",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("mom_growth_percent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("hiring_spend_minor", sa.Integer(), server_default="0", nullable=False),
        sa.Column("one_off_costs_minor", sa.Integer(), server_default="0", nullable=False),
        sa.Column("alert_is_low", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("alert_last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["startup_id"],
            ["startups.id"],
            name=op.f("fk_finance_runway_settings_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finance_runway_settings")),
    )
    op.create_index(
        op.f("ix_finance_runway_settings_startup_id"),
        "finance_runway_settings",
        ["startup_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_finance_runway_settings_startup_id"), table_name="finance_runway_settings")
    op.drop_table("finance_runway_settings")

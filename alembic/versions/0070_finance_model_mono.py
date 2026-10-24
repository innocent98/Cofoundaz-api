"""Finance model assumptions + Mono bank link

Revision ID: 0070_finance_model_mono
Revises: 0069_academy_content
Create Date: 2026-10-24

1. financial_models gains the inputs a recalculation needs so it never has to call the AI:
   `starting` (the ledger figures the projection began from), `overrides` (the founder's edits
   on top of the AI's assumptions), `overrides_rev` / `calculated_rev` (edited-since-last-
   calculated is overrides_rev <> calculated_rev) and `recalculated_at`. Existing models keep
   NULL starting figures; the first recalculation rebuilds them from the ledger.
2. finance_connections gains `provider_account_hash` (SHA-256 of the Mono account id, so an
   inbound webhook finds its connection while the id itself stays encrypted) and the
   one-live-connection index now only covers payment providers, because the Mono bank link has
   to coexist with Paystack/Flutterwave (D-FIN-10).

Downgrade deletes Mono connections (the old index cannot hold two live connections per
workspace); ledger rows they imported stay, detached from the connection.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0070_finance_model_mono"
down_revision = "0069_academy_content"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "financial_models", sa.Column("starting", postgresql.JSONB(astext_type=sa.Text()))
    )
    op.add_column(
        "financial_models", sa.Column("overrides", postgresql.JSONB(astext_type=sa.Text()))
    )
    op.add_column(
        "financial_models",
        sa.Column("overrides_rev", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "financial_models",
        sa.Column("calculated_rev", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("financial_models", sa.Column("recalculated_at", sa.DateTime(timezone=True)))

    op.add_column("finance_connections", sa.Column("provider_account_hash", sa.String(length=64)))
    op.create_index(
        "ix_finance_connections_provider_account_hash",
        "finance_connections",
        ["provider_account_hash"],
    )
    op.drop_index("uq_finance_connections_one_live", table_name="finance_connections")
    op.create_index(
        "uq_finance_connections_one_live",
        "finance_connections",
        ["startup_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'disconnected' AND provider <> 'mono'"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM finance_connections WHERE provider = 'mono'")
    op.drop_index("uq_finance_connections_one_live", table_name="finance_connections")
    op.create_index(
        "uq_finance_connections_one_live",
        "finance_connections",
        ["startup_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'disconnected'"),
    )
    op.drop_index("ix_finance_connections_provider_account_hash", table_name="finance_connections")
    op.drop_column("finance_connections", "provider_account_hash")

    op.drop_column("financial_models", "recalculated_at")
    op.drop_column("financial_models", "calculated_rev")
    op.drop_column("financial_models", "overrides_rev")
    op.drop_column("financial_models", "overrides")
    op.drop_column("financial_models", "starting")

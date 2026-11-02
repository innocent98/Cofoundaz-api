"""Feature flags (Admin slice D)

Revision ID: 0084_feature_flags
Revises: 0083_platform_staff
Create Date: 2026-10-31

Adds `feature_flags`. Downgrade drops the table.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0084_feature_flags"
down_revision = "0083_platform_staff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feature_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "stage",
            sa.Enum("beta", "ga", "internal", name="flagstage", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("rollout_pct", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "workspace_allowlist",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("rollout_pct BETWEEN 0 AND 100", name="ck_feature_flags_rollout_pct"),
        sa.UniqueConstraint("key", name="uq_feature_flags_key"),
    )


def downgrade() -> None:
    op.drop_table("feature_flags")

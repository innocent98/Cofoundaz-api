"""marketing_ai_generations

Revision ID: 0035_marketing_ai_generations
Revises: 0034_campaigns_segments
Create Date: 2026-09-24

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0035_marketing_ai_generations"
down_revision = "0034_campaigns_segments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketing_ai_generations",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum("copy", "plan_week", native_enum=False, length=12),
            nullable=False,
        ),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum("generating", "ready", "failed", native_enum=False, length=12),
            nullable=False,
        ),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["startup_id"],
            ["startups.id"],
            name=op.f("fk_marketing_ai_generations_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_marketing_ai_generations_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_ai_generations")),
    )
    op.create_index(
        op.f("ix_marketing_ai_generations_startup_id"),
        "marketing_ai_generations",
        ["startup_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_marketing_ai_generations_created_by"),
        "marketing_ai_generations",
        ["created_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_marketing_ai_generations_kind"),
        "marketing_ai_generations",
        ["kind"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_marketing_ai_generations_kind"), table_name="marketing_ai_generations")
    op.drop_index(
        op.f("ix_marketing_ai_generations_created_by"), table_name="marketing_ai_generations"
    )
    op.drop_index(
        op.f("ix_marketing_ai_generations_startup_id"), table_name="marketing_ai_generations"
    )
    op.drop_table("marketing_ai_generations")

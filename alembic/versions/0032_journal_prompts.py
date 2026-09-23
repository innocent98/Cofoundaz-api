"""journal_prompts

Revision ID: 0032_journal_prompts
Revises: 0031_learning_recommendations
Create Date: 2026-09-23

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0032_journal_prompts"
down_revision = "0031_learning_recommendations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "journal_prompts",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("founder_id", sa.UUID(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("prompt", sa.String(length=300), nullable=False),
        sa.Column(
            "status", sa.Enum("generating", "ready", native_enum=False, length=12), nullable=False
        ),
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
            name=op.f("fk_journal_prompts_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["founder_id"],
            ["users.id"],
            name=op.f("fk_journal_prompts_founder_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_prompts")),
        sa.UniqueConstraint(
            "startup_id", "founder_id", "date", name="uq_journal_prompts_startup_founder_date"
        ),
    )
    op.create_index(
        op.f("ix_journal_prompts_startup_id"), "journal_prompts", ["startup_id"], unique=False
    )
    op.create_index(
        op.f("ix_journal_prompts_founder_id"), "journal_prompts", ["founder_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_journal_prompts_founder_id"), table_name="journal_prompts")
    op.drop_index(op.f("ix_journal_prompts_startup_id"), table_name="journal_prompts")
    op.drop_table("journal_prompts")

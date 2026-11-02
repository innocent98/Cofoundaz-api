"""marketing_calendar_channels

Revision ID: 0033_marketing_calendar_channels
Revises: 0032_journal_prompts
Create Date: 2026-09-24

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0033_marketing_calendar_channels"
down_revision = "0032_journal_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_calendar",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "channel",
            sa.Enum(
                "organic_social",
                "paid_social",
                "search",
                "email",
                "content_seo",
                "partnerships",
                "events",
                "referral",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("draft", "scheduled", "published", native_enum=False, length=12),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("media_ref", sa.String(length=500), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_content_calendar_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_content_calendar_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_calendar")),
    )
    op.create_index(
        op.f("ix_content_calendar_startup_id"), "content_calendar", ["startup_id"], unique=False
    )
    op.create_index(
        op.f("ix_content_calendar_created_by"), "content_calendar", ["created_by"], unique=False
    )
    op.create_table(
        "marketing_channels",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column(
            "key",
            sa.Enum(
                "organic_social",
                "paid_social",
                "search",
                "email",
                "content_seo",
                "partnerships",
                "events",
                "referral",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("active", "testing", "paused", "not_started", native_enum=False, length=12),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
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
            name=op.f("fk_marketing_channels_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_channels")),
        sa.UniqueConstraint("startup_id", "key", name="uq_marketing_channel_startup_key"),
    )
    op.create_index(
        op.f("ix_marketing_channels_startup_id"),
        "marketing_channels",
        ["startup_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_marketing_channels_startup_id"), table_name="marketing_channels")
    op.drop_table("marketing_channels")
    op.drop_index(op.f("ix_content_calendar_created_by"), table_name="content_calendar")
    op.drop_index(op.f("ix_content_calendar_startup_id"), table_name="content_calendar")
    op.drop_table("content_calendar")

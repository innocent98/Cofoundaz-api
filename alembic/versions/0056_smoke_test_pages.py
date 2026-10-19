"""smoke-test hosted pages: experiment slug + published_at, smoke_test_events

Revision ID: 0056_smoke_test_pages
Revises: 0055_mission_roadmap_backfill
Create Date: 2026-10-19

Validation Hub slice (a), D-VAL-1: a smoke test is served at /p/{slug} on the app domain and
records real visits, CTA/preorder clicks and email signups. smoke_test_slugs keeps every claimed
slug reserved to its workspace so a released URL can't be taken over by another workspace.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0056_smoke_test_pages"
down_revision = "0055_mission_roadmap_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("experiments", sa.Column("slug", sa.String(length=60), nullable=True))
    op.add_column(
        "experiments", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint("uq_experiments_slug", "experiments", ["slug"])
    op.create_table(
        "smoke_test_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("startup_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("visitor_hash", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
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
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_smoke_test_events_startup_id", "smoke_test_events", ["startup_id"])
    op.create_index(
        "ix_smoke_events_experiment_kind", "smoke_test_events", ["experiment_id", "kind"]
    )
    op.create_index(
        "uq_smoke_signup_email",
        "smoke_test_events",
        ["experiment_id", "email"],
        unique=True,
        postgresql_where=sa.text("kind = 'signup'"),
    )

    op.create_table(
        "smoke_test_slugs",
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("startup_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("slug"),
    )
    op.create_index("ix_smoke_test_slugs_startup_id", "smoke_test_slugs", ["startup_id"])


def downgrade() -> None:
    op.drop_index("ix_smoke_test_slugs_startup_id", table_name="smoke_test_slugs")
    op.drop_table("smoke_test_slugs")
    op.drop_index("uq_smoke_signup_email", table_name="smoke_test_events")
    op.drop_index("ix_smoke_events_experiment_kind", table_name="smoke_test_events")
    op.drop_index("ix_smoke_test_events_startup_id", table_name="smoke_test_events")
    op.drop_table("smoke_test_events")
    op.drop_constraint("uq_experiments_slug", "experiments", type_="unique")
    op.drop_column("experiments", "published_at")
    op.drop_column("experiments", "slug")

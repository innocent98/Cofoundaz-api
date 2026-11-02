"""business canvas versions + last edited by

Revision ID: 0052_business_canvas_versions
Revises: 0051_comments
Create Date: 2026-10-19

PRD 08 editor header ("last edited", "version link"), D-BB-1: one snapshot per canvas save,
capped per canvas by the service, plus `updated_by_id` on canvases and records. The new
columns are nullable with no default, so adding them is a metadata-only change; every FK is
indexed (house rule).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0052_business_canvas_versions"
down_revision = "0051_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("business_canvases", "business_records"):
        op.add_column(table, sa.Column("updated_by_id", sa.UUID(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_updated_by_id_users", table, "users", ["updated_by_id"], ["id"],
            ondelete="SET NULL",
        )
        op.create_index(op.f(f"ix_{table}_updated_by_id"), table, ["updated_by_id"], unique=False)

    op.create_table(
        "business_canvas_versions",
        sa.Column("canvas_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("blocks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("edited_by_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["canvas_id"], ["business_canvases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["edited_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canvas_id", "version", name="uq_business_canvas_version"),
    )
    op.create_index(
        op.f("ix_business_canvas_versions_canvas_id"),
        "business_canvas_versions",
        ["canvas_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_business_canvas_versions_edited_by_id"),
        "business_canvas_versions",
        ["edited_by_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_business_canvas_versions_edited_by_id"), table_name="business_canvas_versions"
    )
    op.drop_index(
        op.f("ix_business_canvas_versions_canvas_id"), table_name="business_canvas_versions"
    )
    op.drop_table("business_canvas_versions")
    for table in ("business_records", "business_canvases"):
        op.drop_index(op.f(f"ix_{table}_updated_by_id"), table_name=table)
        op.drop_constraint(f"fk_{table}_updated_by_id_users", table, type_="foreignkey")
        op.drop_column(table, "updated_by_id")

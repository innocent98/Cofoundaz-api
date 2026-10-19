"""comment anchor

Revision ID: 0053_comment_anchor
Revises: 0052_business_canvas_versions
Create Date: 2026-10-19

PRD 08 "comment threads on sections": a nullable `anchor` (a Business Builder canvas block key)
on comments. Nullable with no default, so the add is metadata-only.
"""

import sqlalchemy as sa

from alembic import op

revision = "0053_comment_anchor"
down_revision = "0052_business_canvas_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("comments", sa.Column("anchor", sa.String(length=60), nullable=True))


def downgrade() -> None:
    op.drop_column("comments", "anchor")

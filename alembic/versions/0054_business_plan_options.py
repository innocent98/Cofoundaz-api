"""business plan options/progress/error, canvas meta, finance revenue drivers

Revision ID: 0054_business_plan_options
Revises: 0053_comment_anchor
Create Date: 2026-10-19

PRD 08.11 (plan options, stage progress, failed reason), 08.5 (Value Proposition fit-lines in
canvas `meta`) and 08.8 / 12.6 (revenue drivers sent from the Business Builder revenue model to
the Finance 3-statement model). All nullable with no default: metadata-only adds.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0054_business_plan_options"
down_revision = "0053_comment_anchor"
branch_labels = None
depends_on = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column("business_plans", sa.Column("options", _JSONB, nullable=True))
    op.add_column("business_plans", sa.Column("progress", _JSONB, nullable=True))
    op.add_column("business_plans", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("business_canvases", sa.Column("meta", _JSONB, nullable=True))
    op.add_column("finance_runway_settings", sa.Column("revenue_drivers", _JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("finance_runway_settings", "revenue_drivers")
    op.drop_column("business_canvases", "meta")
    op.drop_column("business_plans", "error")
    op.drop_column("business_plans", "progress")
    op.drop_column("business_plans", "options")

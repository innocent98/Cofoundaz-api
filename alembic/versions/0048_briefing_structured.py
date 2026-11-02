"""briefing structured items

Revision ID: 0048_briefing_structured
Revises: 0047_ai_chat
Create Date: 2026-10-17

Dashboard briefing v2 (Module 02, PRD 02.1): the AI briefing gains structured rows so the
dashboard can render Risks/Opportunities as rows with a CTA, and the briefing as per-action
"Do it" buttons. Three JSONB columns on the existing `daily_briefings` table, each NOT NULL
with a `'[]'` server default so existing rows backfill to "no items" in the same statement
(no table rewrite on Postgres 11+ for a constant default). The legacy prose columns
(`risks`, `opportunities`) are kept and still written, for older readers.

MIGRATION-HEAD COORDINATION: chains off 0047_ai_chat. If Victoria's Module 13 lands first,
rebase this `down_revision` onto hers (single-head rule).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0048_briefing_structured"
down_revision = "0047_ai_chat"
branch_labels = None
depends_on = None

_COLUMNS = ("risk_items", "opportunity_items", "actions")


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column(
            "daily_briefings",
            sa.Column(
                name,
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'[]'::jsonb"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("daily_briefings", name)

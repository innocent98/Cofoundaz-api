"""Auth & Onboarding gaps: sign-up consent record

Revision ID: 0096_auth_onboarding_gaps
Revises: 0098_ai_orchestrator
Create Date: 2026-11-05

Adds `users.terms_accepted_at` and `users.terms_version` (gap 14: the Terms/Privacy checkbox is
stored with a timestamp and the document version). Both are nullable: existing accounts and
Google-created accounts have no recorded consent. The other fixes in this slice (lockout per
(email, IP), per-route rate limits, MFA ticket attempts) live in Redis and need no schema.
Downgrade drops both columns.

CHAIN NOTE: chained on develop's head, 0098_ai_orchestrator (after 0093 to 0095 merged).
"""

import sqlalchemy as sa

from alembic import op

revision = "0096_auth_onboarding_gaps"
down_revision = "0098_ai_orchestrator"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("terms_accepted_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("terms_version", sa.String(length=32)))


def downgrade() -> None:
    op.drop_column("users", "terms_version")
    op.drop_column("users", "terms_accepted_at")

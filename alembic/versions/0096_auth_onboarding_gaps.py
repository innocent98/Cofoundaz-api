"""Auth & Onboarding gaps: sign-up consent record

Revision ID: 0096_auth_onboarding_gaps
Revises: 0092_marketplace_providers
Create Date: 2026-11-05

Adds `users.terms_accepted_at` and `users.terms_version` (gap 14: the Terms/Privacy checkbox is
stored with a timestamp and the document version). Both are nullable: existing accounts and
Google-created accounts have no recorded consent. The other fixes in this slice (lockout per
(email, IP), per-route rate limits, MFA ticket attempts) live in Redis and need no schema.
Downgrade drops both columns.

CHAIN NOTE: chained on 0092 for now. 0093, 0094 and 0095 are in flight in other PRs; the lead
re-chains this file (down_revision) once they merge.
"""

import sqlalchemy as sa

from alembic import op

revision = "0096_auth_onboarding_gaps"
down_revision = "0092_marketplace_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("terms_accepted_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("terms_version", sa.String(length=32)))


def downgrade() -> None:
    op.drop_column("users", "terms_version")
    op.drop_column("users", "terms_accepted_at")

"""Marketplace slice B: listings, availability

Revision ID: 0094_marketplace_listings
Revises: 0095_funding_data_room
Create Date: 2026-11-05

Marketplace slice B (listings and directory; no money). Adds `marketplace_listings`,
`marketplace_availability_rules`, `marketplace_availability_exceptions` and two availability
settings on `marketplace_providers` (`min_notice_h`, `buffer_min`). The new `content_reports`
item types (`listing`, `provider`) fit the existing non-native enum column, so they need no DDL.
Downgrade drops the tables and the two columns.

CHAIN NOTE: chained on 0095 (Funding data room), develop's head when last merged. The revision
number 0094 is kept (it was claimed first); the file order is by `down_revision`, not by number.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0094_marketplace_listings"
down_revision = "0095_funding_data_room"
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _stamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _provider_fk() -> sa.Column:
    return sa.Column(
        "provider_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("marketplace_providers.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.add_column(
        "marketplace_providers",
        sa.Column("min_notice_h", sa.Integer(), server_default=sa.text("24"), nullable=False),
    )
    op.add_column(
        "marketplace_providers",
        sa.Column("buffer_min", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_table(
        "marketplace_listings",
        _id(),
        _provider_fk(),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("price_minor", sa.BigInteger(), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=True),
        sa.Column("delivery_days", sa.Integer(), nullable=True),
        sa.Column("deliverables_json", postgresql.JSONB(), nullable=False),
        sa.Column("revisions_included", sa.Integer(), server_default=sa.text("2"), nullable=False),
        sa.Column("meeting_url", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("pending_revision_json", postgresql.JSONB(), nullable=True),
        sa.Column("revision_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prescreen_json", postgresql.JSONB(), nullable=False),
        sa.Column("review_message", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "unlisted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("unlisted_reason", sa.Text(), nullable=True),
        sa.Column("unlisted_prior_status", sa.String(20), nullable=True),
        sa.Column("unlisted_via_report_id", postgresql.UUID(as_uuid=True), nullable=True),
        *_stamps(),
    )
    op.create_index("ix_marketplace_listings_provider_id", "marketplace_listings", ["provider_id"])
    op.create_index("ix_marketplace_listings_category", "marketplace_listings", ["category"])
    op.create_index("ix_marketplace_listings_status", "marketplace_listings", ["status"])
    op.create_table(
        "marketplace_availability_rules",
        _id(),
        _provider_fk(),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("slot_min", sa.Integer(), nullable=False),
        *_stamps(),
    )
    op.create_index(
        "ix_marketplace_availability_rules_provider_id",
        "marketplace_availability_rules",
        ["provider_id"],
    )
    op.create_table(
        "marketplace_availability_exceptions",
        _id(),
        _provider_fk(),
        sa.Column("on_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        *_stamps(),
    )
    op.create_index(
        "ix_marketplace_availability_exceptions_provider_id",
        "marketplace_availability_exceptions",
        ["provider_id"],
    )


def downgrade() -> None:
    op.drop_table("marketplace_availability_exceptions")
    op.drop_table("marketplace_availability_rules")
    op.drop_table("marketplace_listings")
    op.drop_column("marketplace_providers", "buffer_min")
    op.drop_column("marketplace_providers", "min_notice_h")

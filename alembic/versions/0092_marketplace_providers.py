"""Marketplace slice A: providers, files, portfolio

Revision ID: 0092_marketplace_providers
Revises: 0090_funding_core
Create Date: 2026-11-05

Marketplace slice A (providers and vetting; no money). Adds `marketplace_providers` (an expert,
a table of its own like `platform_staff`), `marketplace_files` (private uploads for people with no
workspace) and `marketplace_provider_portfolio`. Downgrade drops all three.

CHAIN NOTE: `down_revision` is develop's head when last merged (0090, Funding). 0091 (Readiness
deck review) is an open PR; the lead re-chains this migration when it merges.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0092_marketplace_providers"
down_revision = "0090_funding_core"
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


def _user_fk(name: str, *, nullable: bool, ondelete: str, **kw: object) -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete=ondelete),
        nullable=nullable,
        **kw,
    )


def upgrade() -> None:
    op.create_table(
        "marketplace_providers",
        _id(),
        _user_fk("user_id", nullable=True, ondelete="CASCADE", unique=True),
        sa.Column("email", postgresql.CITEXT(), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=True),
        sa.Column("headline", sa.String(160), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("category", sa.String(20), nullable=True),
        sa.Column("categories", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("languages", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("location", sa.String(120), nullable=True),
        sa.Column("avatar_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invite_token_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("invite_expires_at", sa.DateTime(timezone=True), nullable=True),
        _user_fk("invited_by", nullable=True, ondelete="SET NULL"),
        sa.Column("terms_version", sa.String(40), nullable=True),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("vetting_json", postgresql.JSONB(), nullable=False),
        sa.Column("licence_expires_on", sa.Date(), nullable=True),
        sa.Column("vetted_at", sa.DateTime(timezone=True), nullable=True),
        _user_fk("vetted_by", nullable=True, ondelete="SET NULL"),
        sa.Column("review_message", sa.Text(), nullable=True),
        sa.Column("suspended_reason", sa.Text(), nullable=True),
        sa.Column("commission_bps", sa.Integer(), nullable=True),
        sa.Column("rating_avg", sa.Numeric(3, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("rating_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_stamps(),
    )
    op.create_index("ix_marketplace_providers_status", "marketplace_providers", ["status"])
    op.create_index("ix_marketplace_providers_category", "marketplace_providers", ["category"])

    op.create_table(
        "marketplace_files",
        _id(),
        _user_fk("owner_user_id", nullable=False, ondelete="CASCADE"),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(300), nullable=False, unique=True),
        sa.Column("scan_status", sa.String(20), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
    )
    op.create_index("ix_marketplace_files_owner_user_id", "marketplace_files", ["owner_user_id"])
    op.create_index(
        "ix_marketplace_files_owner_live",
        "marketplace_files",
        ["owner_user_id", "purpose"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_foreign_key(
        "fk_marketplace_providers_avatar_file_id",
        "marketplace_providers",
        "marketplace_files",
        ["avatar_file_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "marketplace_provider_portfolio",
        _id(),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("marketplace_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("marketplace_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        *_stamps(),
    )
    op.create_index(
        "ix_marketplace_provider_portfolio_provider_id",
        "marketplace_provider_portfolio",
        ["provider_id"],
    )


def downgrade() -> None:
    op.drop_table("marketplace_provider_portfolio")
    op.drop_constraint(
        "fk_marketplace_providers_avatar_file_id", "marketplace_providers", type_="foreignkey"
    )
    op.drop_table("marketplace_files")
    op.drop_table("marketplace_providers")

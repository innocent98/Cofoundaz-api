"""Funding Hub slice B: the data room

Revision ID: 0095_funding_data_room
Revises: 0092_marketplace_providers
Create Date: 2026-11-05

Adds `data_room_folders`, `data_room_items` (references into Documents, exactly one target),
`data_room_shares` (token hash only; email-gated; revocable), `data_room_challenges` (emailed
one-time codes, keyed hash only) and `data_room_views` (the access log). Downgrade drops all five.

CHAIN NOTE: chained on 0092 for now; 0093 (Readiness Q&A) and 0094 (Marketplace listings) are in
flight and the lead re-chains this one if they land first.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0095_funding_data_room"
down_revision = "0092_marketplace_providers"
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _startup() -> sa.Column:
    return sa.Column(
        "startup_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
    )


def _stamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _fk(name: str, target: str, ondelete: str, nullable: bool) -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "data_room_folders",
        _id(),
        _startup(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_standard", sa.Boolean(), nullable=False),
        *_stamps(),
    )
    op.create_index(
        "ix_data_room_folders_startup", "data_room_folders", ["startup_id", "position"]
    )

    op.create_table(
        "data_room_items",
        _id(),
        _startup(),
        _fk("folder_id", "data_room_folders.id", "CASCADE", False),
        _fk("document_id", "documents.id", "CASCADE", True),
        _fk("file_id", "document_files.id", "CASCADE", True),
        sa.Column("position", sa.Integer(), nullable=False),
        _fk("added_by_id", "users.id", "SET NULL", True),
        *_stamps(),
        sa.CheckConstraint(
            "(document_id IS NULL) <> (file_id IS NULL)", name="ck_data_room_item_one_target"
        ),
    )
    op.create_index("ix_data_room_items_folder", "data_room_items", ["folder_id", "position"])
    op.create_index("ix_data_room_items_startup", "data_room_items", ["startup_id"])
    op.create_index(
        "uq_data_room_item_doc",
        "data_room_items",
        ["folder_id", "document_id"],
        unique=True,
        postgresql_where=sa.text("document_id IS NOT NULL"),
    )
    op.create_index(
        "uq_data_room_item_file",
        "data_room_items",
        ["folder_id", "file_id"],
        unique=True,
        postgresql_where=sa.text("file_id IS NOT NULL"),
    )

    op.create_table(
        "data_room_shares",
        _id(),
        _startup(),
        _fk("round_id", "funding_rounds.id", "SET NULL", True),
        _fk("investor_id", "investors.id", "SET NULL", True),
        _fk("created_by_id", "users.id", "SET NULL", True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("allow_download", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
    )
    op.create_index("uq_data_room_share_token", "data_room_shares", ["token_hash"], unique=True)
    op.create_index("ix_data_room_shares_startup", "data_room_shares", ["startup_id", "created_at"])
    op.create_index("ix_data_room_shares_investor", "data_room_shares", ["investor_id"])

    op.create_table(
        "data_room_challenges",
        _id(),
        _fk("share_id", "data_room_shares.id", "CASCADE", False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        *_stamps(),
    )
    op.create_index(
        "ix_data_room_challenges_share", "data_room_challenges", ["share_id", "created_at"]
    )

    op.create_table(
        "data_room_views",
        _id(),
        _startup(),
        _fk("share_id", "data_room_shares.id", "CASCADE", False),
        _fk("item_id", "data_room_items.id", "SET NULL", True),
        sa.Column("item_name", sa.String(255), nullable=False),
        sa.Column("viewer_email", sa.String(320), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=False),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        *_stamps(),
    )
    op.create_index("ix_data_room_views_startup", "data_room_views", ["startup_id", "started_at"])
    op.create_index("ix_data_room_views_share", "data_room_views", ["share_id", "started_at"])


def downgrade() -> None:
    op.drop_table("data_room_views")
    op.drop_table("data_room_challenges")
    op.drop_table("data_room_shares")
    op.drop_table("data_room_items")
    op.drop_table("data_room_folders")

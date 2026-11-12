"""Funding Hub slice D: term sheets

Revision ID: 0102_funding_term_sheets
Revises: 0103_readiness_narrative
Create Date: 2026-11-07

Adds `term_sheets` (typed or extracted from a PDF; extracted values are only proposals until the
founder confirms them) and `term_sheet_audit` (before and after of every change). Downgrade drops
both.

CHAIN NOTE: chained on 0103 (Readiness narrative); 0101 is reserved for Marketplace C, so the lead re-chains this one if
that lands first.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0102_funding_term_sheets"
down_revision = "0103_readiness_narrative"
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


def _fk(name: str, target: str, ondelete: str = "SET NULL") -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete=ondelete),
        nullable=True,
    )


def upgrade() -> None:
    op.create_table(
        "term_sheets",
        _id(),
        _startup(),
        _fk("investor_id", "investors.id"),
        sa.Column("label", sa.String(160), nullable=False),
        _fk("source_file_id", "document_files.id"),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.String(40), nullable=True),
        _fk("job_id", "jobs.id"),
        sa.Column(
            "terms", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("proposed", postgresql.JSONB(), nullable=True),
        sa.Column(
            "flags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("extracted_by_ai", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        _fk("confirmed_by_id", "users.id"),
        _fk("created_by_id", "users.id"),
        *_stamps(),
        sa.CheckConstraint(
            "status IN ('extracting','needs_confirmation','confirmed','failed')",
            name="ck_term_sheet_status",
        ),
        sa.CheckConstraint(
            "(status = 'confirmed') = (confirmed_at IS NOT NULL)", name="ck_term_sheet_confirmed"
        ),
    )
    op.create_index("ix_term_sheets_startup", "term_sheets", ["startup_id", "created_at"])
    op.create_index("ix_term_sheets_investor", "term_sheets", ["investor_id"])
    op.create_index("ix_term_sheets_sha", "term_sheets", ["startup_id", "content_sha256"])

    op.create_table(
        "term_sheet_audit",
        _id(),
        _startup(),
        sa.Column("term_sheet_id", postgresql.UUID(as_uuid=True), nullable=False),
        _fk("actor_id", "users.id"),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        *_stamps(),
        sa.CheckConstraint(
            "action IN ('create','confirm','update','delete')", name="ck_term_sheet_audit_action"
        ),
    )
    op.create_index("ix_term_sheet_audit_startup", "term_sheet_audit", ["startup_id", "created_at"])


def downgrade() -> None:
    op.drop_table("term_sheet_audit")
    op.drop_table("term_sheets")

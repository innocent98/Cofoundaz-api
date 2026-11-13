"""Funding Hub slice E: grants catalogue, matches, applications

Revision ID: 0107_funding_grants
Revises: 0106_legal
Create Date: 2026-11-13

Adds `grants` (shared catalogue, imported from content/funding), `grant_matches` (weekly
suggestions per workspace), `grant_applications` (the tracker) and `grant_application_answers`
(per question drafts). Downgrade drops all four.

CHAIN NOTE: chained on 0106_legal; 0104 (Marketplace D) is reserved, so the lead re-chains this one
if it lands first.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0107_funding_grants"
down_revision = "0106_legal"
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


def _text(name: str) -> sa.Column:
    return sa.Column(name, sa.Text(), nullable=False, server_default="")


def upgrade() -> None:
    op.create_table(
        "grants",
        _id(),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("funder", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(200), nullable=False),
        sa.Column("amount_min_minor", sa.BigInteger(), nullable=True),
        sa.Column("amount_max_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("amount_note", sa.Text(), nullable=True),
        _text("deadline_text"),
        _text("status_text"),
        _text("regions"),
        _text("sectors"),
        _text("stages"),
        _text("eligibility"),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.Column("verified_on", sa.Date(), nullable=False),
        sa.Column("verification", sa.String(10), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "country_codes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("pan_africa", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("in_kind", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_stamps(),
        sa.CheckConstraint("verification IN ('verified','partial')", name="ck_grant_verification"),
    )
    op.create_table(
        "grant_matches",
        _id(),
        _startup(),
        sa.Column(
            "grant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("why", sa.String(300), nullable=False, server_default=""),
        sa.Column("ai_scored", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("state", sa.String(20), nullable=False, server_default="suggested"),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=False),
        _fk("decided_by_id", "users.id"),
        *_stamps(),
        sa.UniqueConstraint("startup_id", "grant_id", name="uq_grant_match"),
        sa.CheckConstraint(
            "state IN ('suggested','dismissed','not_relevant','applying')", name="ck_match_state"
        ),
        sa.CheckConstraint("score BETWEEN 0 AND 100", name="ck_match_score"),
    )
    op.create_index("ix_grant_matches_startup", "grant_matches", ["startup_id", "state", "score"])
    op.create_table(
        "grant_applications",
        _id(),
        _startup(),
        _fk("grant_id", "grants.id"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("funder", sa.String(200), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="planning"),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        _fk("created_by_id", "users.id"),
        *_stamps(),
        sa.CheckConstraint(
            "status IN ('planning','drafting','submitted','awarded','rejected','withdrawn')",
            name="ck_grant_app_status",
        ),
    )
    op.create_index(
        "uq_grant_app_grant",
        "grant_applications",
        ["startup_id", "grant_id"],
        unique=True,
        postgresql_where=sa.text("grant_id IS NOT NULL"),
    )
    op.create_index("ix_grant_apps_startup", "grant_applications", ["startup_id", "status"])
    op.create_table(
        "grant_application_answers",
        _id(),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("grant_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _startup(),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("question", sa.String(1000), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False, server_default=""),
        sa.Column("ai_drafted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("draft_status", sa.String(12), nullable=True),
        _fk("job_id", "jobs.id"),
        *_stamps(),
        sa.CheckConstraint(
            "draft_status IS NULL OR draft_status IN ('queued','failed')", name="ck_answer_draft"
        ),
    )
    op.create_index(
        "ix_grant_answers_app", "grant_application_answers", ["application_id", "position"]
    )


def downgrade() -> None:
    op.drop_table("grant_application_answers")
    op.drop_table("grant_applications")
    op.drop_table("grant_matches")
    op.drop_table("grants")

"""seo tools

Revision ID: 0037_seo_tools
Revises: 0036_channel_fit_notes
Create Date: 2026-09-29

Module 10 Slice 4 (SEO Tools): three new tables — seo_keywords, tracked_pages
(unique startup_id+url), brand_positioning (unique startup_id). All additive,
no lock on existing tables. content_gap generation kind reuses marketing_ai_generations
(no enum-length change; "content_gap" is 11 chars, fits the length=12 column).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0037_seo_tools"
down_revision = "0036_channel_fit_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "seo_keywords",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("keyword", sa.String(length=200), nullable=False),
        sa.Column("volume", sa.String(length=20), nullable=True),
        sa.Column("difficulty", sa.Integer(), nullable=True),
        sa.Column("current_rank", sa.Integer(), nullable=True),
        sa.Column("target_page", sa.String(length=500), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], name=op.f("fk_seo_keywords_startup_id_startups"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seo_keywords")),
    )
    op.create_index(op.f("ix_seo_keywords_startup_id"), "seo_keywords", ["startup_id"], unique=False)
    op.create_table(
        "tracked_pages",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("checklist", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], name=op.f("fk_tracked_pages_startup_id_startups"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tracked_pages")),
        sa.UniqueConstraint("startup_id", "url", name="uq_tracked_page_startup_url"),
    )
    op.create_index(op.f("ix_tracked_pages_startup_id"), "tracked_pages", ["startup_id"], unique=False)
    op.create_table(
        "brand_positioning",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("audience", sa.String(length=300), nullable=True),
        sa.Column("need", sa.String(length=300), nullable=True),
        sa.Column("product", sa.String(length=300), nullable=True),
        sa.Column("category", sa.String(length=300), nullable=True),
        sa.Column("differentiator", sa.String(length=300), nullable=True),
        sa.Column("statement", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], name=op.f("fk_brand_positioning_startup_id_startups"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brand_positioning")),
    )
    op.create_index(op.f("ix_brand_positioning_startup_id"), "brand_positioning", ["startup_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_brand_positioning_startup_id"), table_name="brand_positioning")
    op.drop_table("brand_positioning")
    op.drop_index(op.f("ix_tracked_pages_startup_id"), table_name="tracked_pages")
    op.drop_table("tracked_pages")
    op.drop_index(op.f("ix_seo_keywords_startup_id"), table_name="seo_keywords")
    op.drop_table("seo_keywords")

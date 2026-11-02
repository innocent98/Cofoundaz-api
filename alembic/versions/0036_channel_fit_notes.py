"""marketing channel fit notes

Revision ID: 0036_channel_fit_notes
Revises: 0035_marketing_ai_generations
Create Date: 2026-09-28

Adds two nullable columns to marketing_channels for the per-channel AI fit note
(Module 10 Slice 3b): ai_fit_note (the note text) and fit_note_generated_at (when
it was last generated). Both nullable, no backfill — brief ADD COLUMN only.
"""

from alembic import op
import sqlalchemy as sa

revision = "0036_channel_fit_notes"
down_revision = "0035_marketing_ai_generations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("marketing_channels", sa.Column("ai_fit_note", sa.Text(), nullable=True))
    op.add_column(
        "marketing_channels",
        sa.Column("fit_note_generated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("marketing_channels", "fit_note_generated_at")
    op.drop_column("marketing_channels", "ai_fit_note")

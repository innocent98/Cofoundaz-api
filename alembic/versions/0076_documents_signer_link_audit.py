"""Documents BE-D review: audit of signer links handed to editors

Revision ID: 0076_documents_signer_link_audit
Revises: 0075_documents_esign_v2
Create Date: 2026-10-25

signature_signers.link_copies (JSONB, default []): one entry per time the signer's link was handed
to a workspace member (create response, copy-link route). Downgrade drops the column.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0076_documents_signer_link_audit"
down_revision = "0075_documents_esign_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "signature_signers",
        sa.Column(
            "link_copies",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("signature_signers", "link_copies")

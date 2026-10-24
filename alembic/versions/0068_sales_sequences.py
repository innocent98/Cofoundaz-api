"""Sales slice B (sequences): sequences, enrollments, sends, suppressions, drafts, IMAP ledger

Revision ID: 0068_sales_sequences
Revises: 0066_finance_invoice_automation
Create Date: 2026-10-22

Six new tables, all additive. `down_revision` is 0066 because Finance slice (c) builds 0067 in
parallel; whichever PR merges second re-chains onto the other.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0068_sales_sequences"
down_revision = "0066_finance_invoice_automation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_imap_processed",
        sa.Column("uidvalidity", sa.Integer(), nullable=False),
        sa.Column("uid", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sales_imap_processed")),
        sa.UniqueConstraint("uidvalidity", "uid", name="uq_imap_uidvalidity_uid"),
    )
    op.create_table(
        "email_sequences",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("steps_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stop_on_stage_change", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active", "paused", "archived", name="sequencestatus", native_enum=False, length=10
            ),
            nullable=False,
        ),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_email_sequences_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["startup_id"],
            ["startups.id"],
            name=op.f("fk_email_sequences_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_sequences")),
    )
    op.create_index(
        op.f("ix_email_sequences_startup_id"), "email_sequences", ["startup_id"], unique=False
    )
    op.create_table(
        "sales_suppressions",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "unsubscribed", "bounced", "complained", name="suppressionreason", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["startup_id"],
            ["startups.id"],
            name=op.f("fk_sales_suppressions_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sales_suppressions")),
        sa.UniqueConstraint("startup_id", "email", name="uq_suppression_startup_email"),
    )
    op.create_index(
        op.f("ix_sales_suppressions_startup_id"), "sales_suppressions", ["startup_id"], unique=False
    )
    op.create_table(
        "sales_drafts",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("sequence_step", "follow_up", name="draftkind", native_enum=False, length=14),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("generating", "ready", "failed", name="draftstatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("sequence_id", sa.UUID(), nullable=True),
        sa.Column("step_index", sa.Integer(), nullable=True),
        sa.Column("deal_id", sa.UUID(), nullable=True),
        sa.Column("output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"],
            ["deals.id"],
            name=op.f("fk_sales_drafts_deal_id_deals"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_sales_drafts_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sequence_id"],
            ["email_sequences.id"],
            name=op.f("fk_sales_drafts_sequence_id_email_sequences"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["startup_id"],
            ["startups.id"],
            name=op.f("fk_sales_drafts_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sales_drafts")),
    )
    op.create_index(op.f("ix_sales_drafts_deal_id"), "sales_drafts", ["deal_id"], unique=False)
    op.create_index(
        op.f("ix_sales_drafts_startup_id"), "sales_drafts", ["startup_id"], unique=False
    )
    op.create_table(
        "sequence_enrollments",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("sequence_id", sa.UUID(), nullable=False),
        sa.Column("lead_id", sa.UUID(), nullable=True),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("company", sa.String(length=200), nullable=False),
        sa.Column("enrolled_by", sa.UUID(), nullable=True),
        sa.Column("sender_name", sa.String(length=200), nullable=False),
        sa.Column("sender_email", sa.String(length=320), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "completed",
                "replied",
                "unsubscribed",
                "bounced",
                "stopped",
                name="enrollmentstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("stopped_reason", sa.String(length=40), nullable=True),
        sa.Column("current_step", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_send_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["sales_contacts.id"],
            name=op.f("fk_sequence_enrollments_contact_id_sales_contacts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["enrolled_by"],
            ["users.id"],
            name=op.f("fk_sequence_enrollments_enrolled_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["sales_leads.id"],
            name=op.f("fk_sequence_enrollments_lead_id_sales_leads"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sequence_id"],
            ["email_sequences.id"],
            name=op.f("fk_sequence_enrollments_sequence_id_email_sequences"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["startup_id"],
            ["startups.id"],
            name=op.f("fk_sequence_enrollments_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sequence_enrollments")),
    )
    op.create_index(
        "ix_enrollments_due",
        "sequence_enrollments",
        ["next_send_at"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        op.f("ix_sequence_enrollments_contact_id"),
        "sequence_enrollments",
        ["contact_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sequence_enrollments_sequence_id"),
        "sequence_enrollments",
        ["sequence_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sequence_enrollments_startup_id"),
        "sequence_enrollments",
        ["startup_id"],
        unique=False,
    )
    op.create_index(
        "uq_enrollment_active_email",
        "sequence_enrollments",
        ["sequence_id", "email"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "sequence_sends",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("enrollment_id", sa.UUID(), nullable=True),
        sa.Column("step_index", sa.Integer(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum("sequence", "follow_up", name="sendkind", native_enum=False, length=10),
            nullable=False,
        ),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("deal_id", sa.UUID(), nullable=True),
        sa.Column("sent_by", sa.UUID(), nullable=True),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "sent",
                "failed",
                "bounced",
                "unknown",
                name="sendstatus",
                native_enum=False,
                length=10,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="1", nullable=False),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("bounce_code", sa.String(length=20), nullable=True),
        sa.Column(
            "reserved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"],
            ["deals.id"],
            name=op.f("fk_sequence_sends_deal_id_deals"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["enrollment_id"],
            ["sequence_enrollments.id"],
            name=op.f("fk_sequence_sends_enrollment_id_sequence_enrollments"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sent_by"],
            ["users.id"],
            name=op.f("fk_sequence_sends_sent_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["startup_id"],
            ["startups.id"],
            name=op.f("fk_sequence_sends_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sequence_sends")),
        sa.UniqueConstraint("enrollment_id", "step_index", name="uq_send_enrollment_step"),
        sa.UniqueConstraint("message_id", name=op.f("uq_sequence_sends_message_id")),
    )
    op.create_index(
        "ix_sends_recipient",
        "sequence_sends",
        ["startup_id", "recipient_email", "reserved_at"],
        unique=False,
    )
    op.create_index("ix_sends_reserved", "sequence_sends", ["reserved_at"], unique=False)
    op.create_index(
        "ix_sends_startup_reserved", "sequence_sends", ["startup_id", "reserved_at"], unique=False
    )
    op.create_index(
        op.f("ix_sequence_sends_enrollment_id"), "sequence_sends", ["enrollment_id"], unique=False
    )
    op.create_index(
        op.f("ix_sequence_sends_startup_id"), "sequence_sends", ["startup_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sequence_sends_startup_id"), table_name="sequence_sends")
    op.drop_index(op.f("ix_sequence_sends_enrollment_id"), table_name="sequence_sends")
    op.drop_index("ix_sends_startup_reserved", table_name="sequence_sends")
    op.drop_index("ix_sends_reserved", table_name="sequence_sends")
    op.drop_index("ix_sends_recipient", table_name="sequence_sends")
    op.drop_table("sequence_sends")
    op.drop_index(
        "uq_enrollment_active_email",
        table_name="sequence_enrollments",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_index(op.f("ix_sequence_enrollments_startup_id"), table_name="sequence_enrollments")
    op.drop_index(op.f("ix_sequence_enrollments_sequence_id"), table_name="sequence_enrollments")
    op.drop_index(op.f("ix_sequence_enrollments_contact_id"), table_name="sequence_enrollments")
    op.drop_index(
        "ix_enrollments_due",
        table_name="sequence_enrollments",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_table("sequence_enrollments")
    op.drop_index(op.f("ix_sales_drafts_startup_id"), table_name="sales_drafts")
    op.drop_index(op.f("ix_sales_drafts_deal_id"), table_name="sales_drafts")
    op.drop_table("sales_drafts")
    op.drop_index(op.f("ix_sales_suppressions_startup_id"), table_name="sales_suppressions")
    op.drop_table("sales_suppressions")
    op.drop_index(op.f("ix_email_sequences_startup_id"), table_name="email_sequences")
    op.drop_table("email_sequences")
    op.drop_table("sales_imap_processed")

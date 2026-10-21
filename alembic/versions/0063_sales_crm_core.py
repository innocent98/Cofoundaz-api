"""Sales slice A (CRM core): settings, stages, accounts, contacts, leads, deals, activities

Revision ID: 0063_sales_crm_core
Revises: 0062_marketing_prd_b
Create Date: 2026-10-21

Eight new tables, all startup-scoped. Additive only.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0063_sales_crm_core"
down_revision = "0062_marketing_prd_b"
branch_labels = None
depends_on = None


def _uuid(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _startup_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE")


def _user_fk(col: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([col], ["users.id"], ondelete="SET NULL")


def upgrade() -> None:
    op.create_table(
        "sales_settings",
        _uuid("startup_id"),
        sa.Column("stall_days", sa.Integer(), server_default="14", nullable=False),
        sa.Column("outreach_postal_address", sa.Text(), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_reason", sa.String(length=200), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _startup_fk(),
        sa.PrimaryKeyConstraint("startup_id"),
        sa.CheckConstraint("stall_days BETWEEN 3 AND 90", name="ck_sales_stall_days"),
    )

    op.create_table(
        "pipeline_stages",
        _uuid("id"),
        _uuid("startup_id"),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("open", "won", "lost", name="stagekind", native_enum=False, length=10),
            nullable=False,
        ),
        sa.Column("win_probability", sa.Integer(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _startup_fk(),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("win_probability BETWEEN 0 AND 100", name="ck_stage_win_probability"),
    )
    op.create_index("ix_pipeline_stages_startup_id", "pipeline_stages", ["startup_id"])
    op.create_index(
        "uq_pipeline_stage_terminal_kind",
        "pipeline_stages",
        ["startup_id", "kind"],
        unique=True,
        postgresql_where=sa.text("kind IN ('won', 'lost') AND archived_at IS NULL"),
    )

    op.create_table(
        "sales_accounts",
        _uuid("id"),
        _uuid("startup_id"),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("website", sa.String(length=300), nullable=True),
        sa.Column("industry", sa.String(length=100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        _uuid("owner_id", nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _startup_fk(),
        _user_fk("owner_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_accounts_startup_id", "sales_accounts", ["startup_id"])
    op.create_index("ix_sales_accounts_owner_id", "sales_accounts", ["owner_id"])
    op.create_index(
        "uq_sales_account_name_ci",
        "sales_accounts",
        ["startup_id", sa.text("lower(name)")],
        unique=True,
    )

    op.create_table(
        "sales_contacts",
        _uuid("id"),
        _uuid("startup_id"),
        _uuid("account_id", nullable=True),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("title", sa.String(length=100), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _startup_fk(),
        sa.ForeignKeyConstraint(["account_id"], ["sales_accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_contacts_startup_id", "sales_contacts", ["startup_id"])
    op.create_index("ix_sales_contacts_account_id", "sales_contacts", ["account_id"])
    op.create_index(
        "ix_sales_contacts_startup_email_ci",
        "sales_contacts",
        ["startup_id", sa.text("lower(email)")],
    )

    op.create_table(
        "deals",
        _uuid("id"),
        _uuid("startup_id"),
        sa.Column("name", sa.String(length=200), nullable=False),
        _uuid("account_id", nullable=True),
        _uuid("contact_id", nullable=True),
        sa.Column("value_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="NGN", nullable=False),
        _uuid("stage_id"),
        sa.Column("next_step", sa.String(length=300), nullable=True),
        sa.Column("next_step_due", sa.Date(), nullable=True),
        sa.Column(
            "closed_reason",
            sa.Enum(
                "price",
                "timing",
                "competitor",
                "no_budget",
                "ghosted",
                "other",
                name="closedreason",
                native_enum=False,
                length=12,
            ),
            nullable=True,
        ),
        sa.Column("closed_note", sa.Text(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        _uuid("owner_id", nullable=True),
        _ts("last_activity_at"),
        sa.Column("stalled_notified_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _startup_fk(),
        sa.ForeignKeyConstraint(["account_id"], ["sales_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["contact_id"], ["sales_contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["stage_id"], ["pipeline_stages.id"], ondelete="RESTRICT"),
        _user_fk("owner_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deals_startup_id", "deals", ["startup_id"])
    op.create_index("ix_deals_account_id", "deals", ["account_id"])
    op.create_index("ix_deals_contact_id", "deals", ["contact_id"])
    op.create_index("ix_deals_owner_id", "deals", ["owner_id"])
    op.create_index("ix_deals_startup_stage", "deals", ["startup_id", "stage_id"])
    op.create_index("ix_deals_startup_last_activity", "deals", ["startup_id", "last_activity_at"])

    op.create_table(
        "deal_stage_changes",
        _uuid("id"),
        _uuid("deal_id"),
        _uuid("from_stage_id", nullable=True),
        _uuid("to_stage_id"),
        _uuid("changed_by", nullable=True),
        _ts("changed_at"),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["from_stage_id"], ["pipeline_stages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["to_stage_id"], ["pipeline_stages.id"], ondelete="CASCADE"),
        _user_fk("changed_by"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deal_stage_changes_deal_id", "deal_stage_changes", ["deal_id"])
    op.create_index("ix_deal_stage_changes_changed_by", "deal_stage_changes", ["changed_by"])

    op.create_table(
        "sales_leads",
        _uuid("id"),
        _uuid("startup_id"),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("company", sa.String(length=200), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "manual",
                "csv",
                "referral",
                "inbound",
                "event",
                "other",
                name="leadsource",
                native_enum=False,
                length=10,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "new",
                "working",
                "qualified",
                "disqualified",
                "converted",
                name="leadstatus",
                native_enum=False,
                length=15,
            ),
            nullable=False,
        ),
        _uuid("owner_id", nullable=True),
        _uuid("converted_deal_id", nullable=True),
        _uuid("converted_contact_id", nullable=True),
        _uuid("converted_account_id", nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _startup_fk(),
        _user_fk("owner_id"),
        sa.ForeignKeyConstraint(["converted_deal_id"], ["deals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["converted_contact_id"], ["sales_contacts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["converted_account_id"], ["sales_accounts.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_leads_startup_id", "sales_leads", ["startup_id"])
    op.create_index("ix_sales_leads_owner_id", "sales_leads", ["owner_id"])
    op.create_index("ix_sales_leads_startup_status", "sales_leads", ["startup_id", "status"])
    op.create_index(
        "ix_sales_leads_startup_email_ci", "sales_leads", ["startup_id", sa.text("lower(email)")]
    )

    op.create_table(
        "sales_activities",
        _uuid("id"),
        _uuid("startup_id"),
        sa.Column(
            "kind",
            sa.Enum(
                "call",
                "email",
                "meeting",
                "note",
                name="activitykind",
                native_enum=False,
                length=10,
            ),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        _ts("occurred_at"),
        _uuid("author_id", nullable=True),
        _uuid("account_id", nullable=True),
        _uuid("contact_id", nullable=True),
        _uuid("deal_id", nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _startup_fk(),
        _user_fk("author_id"),
        sa.ForeignKeyConstraint(["account_id"], ["sales_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["sales_contacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "account_id IS NOT NULL OR contact_id IS NOT NULL OR deal_id IS NOT NULL",
            name="ck_sales_activity_target",
        ),
    )
    op.create_index("ix_sales_activities_startup_id", "sales_activities", ["startup_id"])
    op.create_index("ix_sales_activities_author_id", "sales_activities", ["author_id"])
    op.create_index("ix_sales_activities_account_id", "sales_activities", ["account_id"])
    op.create_index("ix_sales_activities_contact_id", "sales_activities", ["contact_id"])
    op.create_index("ix_sales_activities_deal_id", "sales_activities", ["deal_id"])


def downgrade() -> None:
    # Dropping a table drops its indexes; order follows FK dependencies in reverse.
    op.drop_table("sales_activities")
    op.drop_table("sales_leads")
    op.drop_table("deal_stage_changes")
    op.drop_table("deals")
    op.drop_table("sales_contacts")
    op.drop_table("sales_accounts")
    op.drop_table("pipeline_stages")
    op.drop_table("sales_settings")

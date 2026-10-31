"""Platform staff access (Admin slice A): platform_staff, audit reason/details, append-only audit_log

Revision ID: 0083_platform_staff
Revises: 0082_ai_preferences
Create Date: 2026-10-31

Adds `platform_staff`, two nullable columns on `audit_log` (`reason`, `details`), and a trigger
that rejects UPDATE and DELETE on `audit_log` (D-ADM-11). TRUNCATE is not blocked.
Downgrade removes the trigger and function first, then the columns, then the table.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0083_platform_staff"
down_revision = "0082_ai_preferences"
branch_labels = None
depends_on = None


FUNCTION_SQL = """
CREATE FUNCTION audit_log_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql
"""

TRIGGER_SQL = """
CREATE TRIGGER audit_log_append_only
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_reject_change()
"""


def upgrade() -> None:
    op.create_table(
        "platform_staff",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.Enum("admin", "super_admin", name="staffrole", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("scopes", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column(
            "invited_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", name="uq_platform_staff_user_id"),
    )
    op.add_column("audit_log", sa.Column("reason", sa.Text(), nullable=True))
    op.add_column(
        "audit_log", sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.execute(FUNCTION_SQL)
    op.execute(TRIGGER_SQL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_reject_change()")
    op.drop_column("audit_log", "details")
    op.drop_column("audit_log", "reason")
    op.drop_table("platform_staff")

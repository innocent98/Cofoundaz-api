"""Academy content tables, private notes, resume positions, certificate PDFs and credential IDs

Revision ID: 0069_academy_content
Revises: 0068_sales_sequences
Create Date: 2026-10-22

Moves the Academy catalog from code into the database (courses, lessons, paths, articles are
upserted by slug from ``content/academy/``), and adds:

- ``lesson_notes`` (private, one row per user and lesson) and ``lesson_positions`` (resume point),
- ``certificate_files`` (the rendered PDF) and, on ``certificates``, ``holder_name``,
  ``verify_code`` (``CFZ-XXXX-XXXX-XXXX``, unique) and ``revoked_at``. Existing certificates are
  backfilled with a holder name and a code.
- ``learning_recommendations`` becomes one row per (startup, hero course) with a ``context_key``.
  It only caches reasons, so existing rows are dropped and regenerate on the next read.

Chained on 0068 after Finance (c) 0067 and Sales B 0068 merged.
"""

import secrets

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0069_academy_content"
down_revision = "0068_sales_sequences"
branch_labels = None
depends_on = None

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I: a code is read aloud and typed


def _code() -> str:
    parts = ["".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(3)]
    return "CFZ-" + "-".join(parts)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    ]


def _review() -> sa.Column:
    return sa.Column(
        "review_status",
        sa.Enum("draft", "reviewed", native_enum=False, length=12),
        server_default="draft",
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "academy_courses",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("outcomes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "level",
            sa.Enum("beginner", "intermediate", "advanced", native_enum=False, length=12),
            nullable=False,
        ),
        sa.Column("stage_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("gap_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        _review(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_academy_courses")),
    )
    op.create_table(
        "academy_lessons",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("course_id", sa.String(length=80), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("video_id", sa.String(length=11), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["academy_courses.id"],
            name=op.f("fk_academy_lessons_course_id_academy_courses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_academy_lessons")),
        sa.UniqueConstraint("course_id", "position", name="uq_academy_lessons_order"),
    )
    op.create_index(
        op.f("ix_academy_lessons_course_id"), "academy_lessons", ["course_id"], unique=False
    )
    op.create_table(
        "academy_paths",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("course_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        _review(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_academy_paths")),
    )
    op.create_table(
        "academy_articles",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("read_min", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        _review(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_academy_articles")),
    )

    for table, extra in (
        (
            "lesson_positions",
            [
                sa.Column("course_id", sa.Text(), nullable=False),
                sa.Column("lesson_id", sa.Text(), nullable=False),
                sa.Column("position_s", sa.Integer(), nullable=False),
            ],
        ),
        (
            "lesson_notes",
            [
                sa.Column("lesson_id", sa.Text(), nullable=False),
                sa.Column("body", sa.Text(), nullable=False),
            ],
        ),
    ):
        constraints: list[sa.schema.SchemaItem] = [
            sa.UniqueConstraint(
                "startup_id",
                "user_id",
                "lesson_id",
                name=f"uq_{table}_startup_user_lesson",
            )
        ]
        if table == "lesson_positions":
            constraints.append(
                sa.CheckConstraint(
                    "position_s >= 0", name=op.f("ck_lesson_positions_position_non_negative")
                )
            )
        op.create_table(
            table,
            sa.Column("startup_id", sa.UUID(), nullable=False),
            sa.Column("user_id", sa.UUID(), nullable=False),
            *extra,
            sa.Column("id", sa.UUID(), nullable=False),
            *_timestamps(),
            sa.ForeignKeyConstraint(
                ["startup_id"],
                ["startups.id"],
                name=op.f(f"fk_{table}_startup_id_startups"),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                name=op.f(f"fk_{table}_user_id_users"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
            *constraints,
        )
        op.create_index(op.f(f"ix_{table}_startup_id"), table, ["startup_id"], unique=False)
        op.create_index(op.f(f"ix_{table}_user_id"), table, ["user_id"], unique=False)

    # certificates: add nullable, backfill, then tighten.
    op.add_column("certificates", sa.Column("holder_name", sa.Text(), nullable=True))
    op.add_column("certificates", sa.Column("verify_code", sa.String(length=18), nullable=True))
    op.add_column("certificates", sa.Column("revoked_at", sa.DateTime(timezone=True)))
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT c.id, COALESCE(NULLIF(p.full_name, ''), 'Cofoundaz member') "
            "FROM certificates c LEFT JOIN user_profiles p ON p.user_id = c.user_id"
        )
    ).fetchall()
    used: set[str] = set()
    for cert_id, name in rows:
        code = _code()
        while code in used:
            code = _code()
        used.add(code)
        bind.execute(
            sa.text("UPDATE certificates SET holder_name = :n, verify_code = :c WHERE id = :i"),
            {"n": name, "c": code, "i": cert_id},
        )
    op.alter_column("certificates", "holder_name", nullable=False)
    op.alter_column("certificates", "verify_code", nullable=False)
    op.create_unique_constraint(op.f("uq_certificates_verify_code"), "certificates", ["verify_code"])

    op.create_table(
        "certificate_files",
        sa.Column("certificate_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["certificate_id"],
            ["certificates.id"],
            name=op.f("fk_certificate_files_certificate_id_certificates"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("certificate_id", name=op.f("pk_certificate_files")),
    )

    # learning_recommendations: a cache keyed per hero course. Rows regenerate on the next read.
    op.execute("DELETE FROM learning_recommendations")
    op.drop_constraint("uq_learning_reco_startup", "learning_recommendations", type_="unique")
    op.add_column(
        "learning_recommendations", sa.Column("course_id", sa.String(length=80), nullable=False)
    )
    op.add_column(
        "learning_recommendations", sa.Column("context_key", sa.String(length=64), nullable=False)
    )
    op.create_unique_constraint(
        "uq_learning_reco_startup_course", "learning_recommendations", ["startup_id", "course_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_learning_reco_startup_course", "learning_recommendations", type_="unique")
    op.execute("DELETE FROM learning_recommendations")
    op.drop_column("learning_recommendations", "context_key")
    op.drop_column("learning_recommendations", "course_id")
    op.create_unique_constraint("uq_learning_reco_startup", "learning_recommendations", ["startup_id"])

    op.drop_table("certificate_files")
    op.drop_constraint(op.f("uq_certificates_verify_code"), "certificates", type_="unique")
    op.drop_column("certificates", "revoked_at")
    op.drop_column("certificates", "verify_code")
    op.drop_column("certificates", "holder_name")

    for table in ("lesson_notes", "lesson_positions"):
        op.drop_index(op.f(f"ix_{table}_user_id"), table_name=table)
        op.drop_index(op.f(f"ix_{table}_startup_id"), table_name=table)
        op.drop_table(table)
    op.drop_table("academy_articles")
    op.drop_table("academy_paths")
    op.drop_index(op.f("ix_academy_lessons_course_id"), table_name="academy_lessons")
    op.drop_table("academy_lessons")
    op.drop_table("academy_courses")

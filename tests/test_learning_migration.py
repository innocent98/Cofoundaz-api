import re
import subprocess
import uuid
from pathlib import Path

import sqlalchemy as sa

from app.core.config import settings

REV = "0069_academy_content"
NEW_TABLES = {
    "academy_courses",
    "academy_lessons",
    "academy_paths",
    "academy_articles",
    "lesson_notes",
    "lesson_positions",
    "certificate_files",
}


def _alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["poetry", "run", "alembic", *args], capture_output=True, text=True)


def _prev() -> str:
    """The revision this migration sits on (it re-chains when a sibling lands first)."""
    source = Path("alembic/versions/0069_academy_content.py").read_text()
    match = re.search(r'^down_revision = "([^"]+)"', source, re.MULTILINE)
    assert match is not None
    return match.group(1)


def _engine() -> sa.Engine:
    return sa.create_engine(settings.DATABASE_URL)


def test_exactly_one_alembic_head():
    r = _alembic("heads")
    assert r.returncode == 0, r.stderr
    assert r.stdout.count("(head)") == 1, r.stdout


def test_upgrade_creates_the_academy_schema():
    assert _alembic("upgrade", "head").returncode == 0
    e = _engine()
    try:
        insp = sa.inspect(e)
        assert NEW_TABLES <= set(insp.get_table_names())
        uq = {x["name"] for x in insp.get_unique_constraints("certificates")}
        assert {"uq_certificates_verify_code", "uq_certificates_credential_code"} <= uq
        cols = {c["name"]: c for c in insp.get_columns("certificates")}
        assert cols["holder_name"]["nullable"] is False
        assert cols["verify_code"]["nullable"] is False
        assert cols["revoked_at"]["nullable"] is True
        uq = {x["name"] for x in insp.get_unique_constraints("lesson_notes")}
        assert "uq_lesson_notes_startup_user_lesson" in uq
        uq = {x["name"] for x in insp.get_unique_constraints("learning_recommendations")}
        assert uq == {"uq_learning_reco_startup_course"}
        ck = {x["name"] for x in insp.get_check_constraints("lesson_positions")}
        assert "ck_lesson_positions_position_non_negative" in ck
        assert "ck_enrollments_progress_range" in {
            x["name"] for x in insp.get_check_constraints("enrollments")
        }
    finally:
        e.dispose()


def test_existing_certificates_are_backfilled_and_the_round_trip_is_clean():
    assert _alembic("upgrade", "head").returncode == 0
    assert _alembic("downgrade", _prev()).returncode == 0
    user_id, other_id, startup_id = (uuid.uuid4() for _ in range(3))
    e = _engine()
    try:
        with e.begin() as conn:
            for uid, email, name in (
                (user_id, "named@example.com", "Ada Founder"),
                (other_id, "anon@example.com", None),
            ):
                conn.execute(
                    sa.text(
                        "INSERT INTO users (id, email, status, mfa_type, failed_login_count) "
                        "VALUES (:i, :e, 'active', 'none', 0)"
                    ),
                    {"i": uid, "e": email},
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO user_profiles (user_id, role_title, full_name) "
                        "VALUES (:i, 'Founder', :n)"
                    ),
                    {"i": uid, "n": name},
                )
            conn.execute(
                sa.text("INSERT INTO startups (id, name, created_by) VALUES (:s, 'Acme', :u)"),
                {"s": startup_id, "u": user_id},
            )
            for n, uid in enumerate((user_id, other_id)):
                conn.execute(
                    sa.text(
                        "INSERT INTO certificates (id, startup_id, user_id, course_id, "
                        "credential_code, issued_at) VALUES (:i, :s, :u, :c, :k, now())"
                    ),
                    {
                        "i": uuid.uuid4(),
                        "s": startup_id,
                        "u": uid,
                        "c": "build-scope-the-mvp",
                        "k": f"old-{n}",
                    },
                )
            conn.execute(
                sa.text(
                    "INSERT INTO learning_recommendations (id, startup_id, stage, reason, status) "
                    "VALUES (:i, :s, 'idea', 'old', 'ready')"
                ),
                {"i": uuid.uuid4(), "s": startup_id},
            )
        assert _alembic("upgrade", "head").returncode == 0
        with e.begin() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT holder_name, verify_code FROM certificates WHERE startup_id = :s "
                    "ORDER BY credential_code"
                ),
                {"s": startup_id},
            ).fetchall()
            assert [r[0] for r in rows] == ["Ada Founder", "Cofoundaz member"]
            codes = [r[1] for r in rows]
            assert len(set(codes)) == 2 and all(
                c.startswith("CFZ-") and len(c) == 18 for c in codes
            )
            # the reason cache was reset (it regenerates per hero course on the next read)
            left = conn.execute(
                sa.text("SELECT count(*) FROM learning_recommendations WHERE startup_id = :s"),
                {"s": startup_id},
            ).scalar()
            assert left == 0
        assert _alembic("downgrade", _prev()).returncode == 0
        assert _alembic("upgrade", "head").returncode == 0
    finally:
        with e.begin() as conn:
            conn.execute(sa.text("DELETE FROM startups WHERE id = :s"), {"s": startup_id})
            conn.execute(
                sa.text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": user_id, "b": other_id}
            )
        e.dispose()

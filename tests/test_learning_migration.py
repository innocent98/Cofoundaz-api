import subprocess


def test_learning_migration_applies():
    r = subprocess.run(
        ["poetry", "run", "alembic", "upgrade", "head"], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    chk = subprocess.run(
        [
            "poetry",
            "run",
            "python",
            "-c",
            "from sqlalchemy import create_engine, inspect; from app.core.config import settings; "
            "e=create_engine(settings.DATABASE_URL); i=inspect(e); n=set(i.get_table_names()); "
            "assert {'enrollments','lesson_progress','certificates','learning_recommendations'} <= n, n; "
            "uq={x['name'] for x in i.get_unique_constraints('enrollments')}; "
            "assert 'uq_enrollments_startup_user_course' in uq, uq; "
            "uq={x['name'] for x in i.get_unique_constraints('lesson_progress')}; "
            "assert 'uq_lesson_progress_startup_user_lesson' in uq, uq; "
            "uq={x['name'] for x in i.get_unique_constraints('certificates')}; "
            "assert 'uq_certificates_startup_user_course' in uq, uq; "
            "assert 'uq_certificates_credential_code' in uq, uq; "
            "ck={x['name'] for x in i.get_check_constraints('enrollments')}; "
            "assert 'ck_enrollments_progress_range' in ck, ck; "
            "uq={x['name'] for x in i.get_unique_constraints('learning_recommendations')}; "
            "assert 'uq_learning_reco_startup' in uq, uq; "
            "print('ok')",
        ],
        capture_output=True,
        text=True,
    )
    assert chk.returncode == 0, chk.stderr


def test_exactly_one_alembic_head():
    r = subprocess.run(["poetry", "run", "alembic", "heads"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.count("(head)") == 1, r.stdout

import subprocess


def test_assessment_migration_applies():
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
            "assert {'assessments','assessment_answers','assessment_results'} <= n; "
            "idx=[x['name'] for x in i.get_indexes('assessments')]; "
            "assert 'uq_assessments_startup_in_progress' in idx; print('ok')",
        ],
        capture_output=True,
        text=True,
    )
    assert chk.returncode == 0, chk.stderr

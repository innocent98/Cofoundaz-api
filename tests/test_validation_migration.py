import subprocess


def test_validation_migration_applies():
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
            "assert {'assumptions','experiments','interviews','surveys','survey_responses'} <= n, n; "
            "uq={x['name'] for x in i.get_unique_constraints('surveys')}; "
            "assert 'uq_surveys_token_hash' in uq, uq; "
            "cols={c['name'] for c in i.get_columns('survey_responses')}; "
            "assert 'user_id' not in cols, cols; "
            "assert {'survey_id','startup_id','answers','submitted_at'} <= cols, cols; "
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

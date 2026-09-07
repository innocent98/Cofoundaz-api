import subprocess


def test_journal_migration_applies():
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
            "assert {'journal_entries','mood_logs'} <= n, n; "
            "uq={x['name'] for x in i.get_unique_constraints('journal_entries')}; "
            "assert 'uq_journal_entries_startup_founder_date' in uq, uq; "
            "ck={x['name'] for x in i.get_check_constraints('journal_entries')}; "
            "assert 'ck_journal_entries_stress_range' in ck, ck; "
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

import subprocess


def test_marketing_migration_applies():
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
            "assert {'content_calendar','marketing_channels'} <= n, n; "
            "uq={x['name'] for x in i.get_unique_constraints('marketing_channels')}; "
            "assert 'uq_marketing_channel_startup_key' in uq, uq; "
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

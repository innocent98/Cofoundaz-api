import subprocess


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["poetry", "run", *args], capture_output=True, text=True)


def test_stage_at_time_migration_applies_and_round_trips():
    up = _run("alembic", "upgrade", "head")
    assert up.returncode == 0, up.stderr
    chk = _run(
        "python",
        "-c",
        "from sqlalchemy import create_engine, inspect; from app.core.config import settings; "
        "i=inspect(create_engine(settings.DATABASE_URL)); "
        "c={x['name']: x for x in i.get_columns('assessments')}; "
        "assert 'stage_at_time' in c, sorted(c); "
        "assert c['stage_at_time']['nullable'] is True; "
        "print('ok')",
    )
    assert chk.returncode == 0, chk.stderr

    # One step below THIS revision, not below head: later migrations stack on top of it.
    down = _run("alembic", "downgrade", "0060_assessment_stage_at_time-1")
    assert down.returncode == 0, down.stderr
    gone = _run(
        "python",
        "-c",
        "from sqlalchemy import create_engine, inspect; from app.core.config import settings; "
        "i=inspect(create_engine(settings.DATABASE_URL)); "
        "assert 'stage_at_time' not in {x['name'] for x in i.get_columns('assessments')}; "
        "print('ok')",
    )
    assert gone.returncode == 0, gone.stderr

    back = _run("alembic", "upgrade", "head")
    assert back.returncode == 0, back.stderr

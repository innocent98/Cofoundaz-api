import subprocess

_REV = "0096_auth_onboarding_gaps"
_COLS_SCRIPT = (
    "from sqlalchemy import create_engine, inspect; from app.core.config import settings; "
    "c={x['name']: x for x in inspect(create_engine(settings.DATABASE_URL))"
    ".get_columns('users')}; "
)


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["poetry", "run", *args], capture_output=True, text=True)


def test_terms_consent_columns_apply_and_round_trip():
    up = _run("alembic", "upgrade", "head")
    assert up.returncode == 0, up.stderr
    present = _run(
        "python",
        "-c",
        _COLS_SCRIPT + "assert c['terms_accepted_at']['nullable'] is True, c; "
        "assert c['terms_version']['nullable'] is True, c; print('ok')",
    )
    assert present.returncode == 0, present.stderr

    # One step below THIS revision, not below head: later migrations stack on top of it.
    down = _run("alembic", "downgrade", f"{_REV}-1")
    assert down.returncode == 0, down.stderr
    gone = _run(
        "python",
        "-c",
        _COLS_SCRIPT + "assert 'terms_accepted_at' not in c and 'terms_version' not in c; "
        "print('ok')",
    )
    assert gone.returncode == 0, gone.stderr

    back = _run("alembic", "upgrade", "head")
    assert back.returncode == 0, back.stderr

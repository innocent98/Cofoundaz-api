import subprocess


def test_onboarding_migration_applies():
    r = subprocess.run(
        ["poetry", "run", "alembic", "upgrade", "head"], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    check = subprocess.run(
        [
            "poetry",
            "run",
            "python",
            "-c",
            "from sqlalchemy import create_engine, inspect; from app.core.config import settings; "
            "e=create_engine(settings.DATABASE_URL); i=inspect(e); "
            "assert 'invitations' in i.get_table_names(); "
            "cols={c['name']: c for c in i.get_columns('startup_profiles')}; "
            "assert 'assessment_pending' in cols; "
            "nm={c['name']: c for c in i.get_columns('startups')}['name']; "
            "assert nm['nullable'] is True; print('ok')",
        ],
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr

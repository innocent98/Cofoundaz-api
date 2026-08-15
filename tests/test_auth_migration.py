import subprocess


def test_auth_tables_present_after_upgrade():
    result = subprocess.run(
        ["poetry", "run", "alembic", "upgrade", "head"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    check = subprocess.run(
        [
            "poetry",
            "run",
            "python",
            "-c",
            "from sqlalchemy import create_engine, inspect; "
            "from app.core.config import settings; "
            "e=create_engine(settings.DATABASE_URL); i=inspect(e); "
            "names=set(i.get_table_names()); "
            "req={'auth_sessions','auth_tokens','mfa_backup_codes','oauth_accounts'}; "
            "assert req <= names, req - names; print('ok')",
        ],
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr

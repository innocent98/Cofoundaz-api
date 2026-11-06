import subprocess
import sys


def _alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], capture_output=True, text=True, check=False
    )


def test_migration_chain_single_head_includes_0097():
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    assert "0097_ai_conversation_archive" in _alembic("history").stdout

import subprocess
import sys


def test_migration_chain_single_head_includes_0098():
    heads = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"], capture_output=True, text=True, check=False
    )
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    hist = subprocess.run(
        [sys.executable, "-m", "alembic", "history"], capture_output=True, text=True, check=False
    )
    assert "0098_ai_orchestrator" in hist.stdout

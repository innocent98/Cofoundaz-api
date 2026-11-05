import subprocess


def _alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["poetry", "run", "alembic", *args], capture_output=True, text=True, check=False
    )


def test_migration_chain_single_head_includes_0089():
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    history = _alembic("history")
    assert "0089_readiness_scores" in history.stdout

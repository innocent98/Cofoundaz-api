import subprocess


def _alembic(*args: str) -> subprocess.CompletedProcess:
    cmd = ["poetry", "run", "alembic", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_migration_chain_single_head_includes_0036():
    # Invariant: exactly one head (no divergent branches). Do NOT assert 0036 is *the*
    # head — later slices (e.g. 0037) legitimately chain off it and become the head.
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    # 0036 must still be reachable in the linear history.
    history = _alembic("history")
    assert history.returncode == 0, history.stderr
    assert "0036_channel_fit_notes" in history.stdout


def test_upgrade_then_downgrade_round_trips():
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    down = _alembic("downgrade", "0035_marketing_ai_generations")
    assert down.returncode == 0, down.stderr
    reup = _alembic("upgrade", "head")
    assert reup.returncode == 0, reup.stderr

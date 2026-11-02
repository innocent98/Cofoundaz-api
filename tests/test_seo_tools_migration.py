import subprocess


def _alembic(*args: str) -> subprocess.CompletedProcess:
    cmd = ["poetry", "run", "alembic", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_migration_chain_single_head_includes_0037():
    # Invariant: exactly one head (no divergent branches). Do NOT assert 0037 is *the*
    # head — a later slice (0038+) will legitimately chain off it and become the head.
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    # 0037 must still be reachable in the linear history.
    history = _alembic("history")
    assert history.returncode == 0, history.stderr
    assert "0037_seo_tools" in history.stdout


def test_upgrade_then_downgrade_round_trips():
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    down = _alembic("downgrade", "0036_channel_fit_notes")
    assert down.returncode == 0, down.stderr
    reup = _alembic("upgrade", "head")
    assert reup.returncode == 0, reup.stderr

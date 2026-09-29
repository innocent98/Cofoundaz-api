import subprocess


def _alembic(*args: str) -> subprocess.CompletedProcess:
    cmd = ["poetry", "run", "alembic", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_single_head_is_0037():
    result = _alembic("heads")
    assert result.returncode == 0, result.stderr
    assert "0037_seo_tools" in result.stdout
    assert result.stdout.count("(head)") == 1


def test_upgrade_then_downgrade_round_trips():
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    down = _alembic("downgrade", "0036_channel_fit_notes")
    assert down.returncode == 0, down.stderr
    reup = _alembic("upgrade", "head")
    assert reup.returncode == 0, reup.stderr

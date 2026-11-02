import subprocess

REVISION = "0087_legal"
PREVIOUS = "0086_admin_danger_zone"


def _alembic(*args: str) -> subprocess.CompletedProcess:
    cmd = ["poetry", "run", "alembic", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_migration_chain_single_head_includes_legal():
    # Asserts "one head, and my revision is somewhere in the chain" rather than "my revision IS
    # the head", so this survives another module merging ahead of us. The id is matched in FULL:
    # a loose "_legal" substring would also match any future legal-prefixed revision and make
    # this test unfailable. If the migration is renumbered, update REVISION with it.
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.count("(head)") == 1
    history = _alembic("history")
    assert history.returncode == 0, history.stderr
    assert REVISION in history.stdout


def test_upgrade_then_downgrade_round_trips():
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    down = _alembic("downgrade", PREVIOUS)
    assert down.returncode == 0, down.stderr
    reup = _alembic("upgrade", "head")
    assert reup.returncode == 0, reup.stderr

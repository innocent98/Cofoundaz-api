from sqlalchemy import text


def test_db_fixture_is_postgres(db):
    version = db.execute(text("SELECT version()")).scalar()
    assert "PostgreSQL" in version


def test_rollback_isolation_first(db):
    db.execute(text("CREATE TEMP TABLE t_iso (n int)"))
    db.execute(text("INSERT INTO t_iso VALUES (1)"))
    assert db.execute(text("SELECT count(*) FROM t_iso")).scalar() == 1


def test_rollback_isolation_second(db):
    # Previous test's TEMP table must not survive rollback.
    exists = db.execute(text("SELECT to_regclass('t_iso')")).scalar()
    assert exists is None

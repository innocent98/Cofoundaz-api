from app.platform.audit import write_audit


def test_write_audit_persists(db):
    row = write_audit(db, "auth.login.success", ip="1.2.3.4")
    db.flush()
    assert row.action == "auth.login.success"
    assert row.ip == "1.2.3.4"

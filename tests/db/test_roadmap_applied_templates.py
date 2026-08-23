from tests.factories import create_roadmap, create_startup, create_user


def test_applied_template_keys_defaults_empty(db):
    startup = create_startup(db, owner=create_user(db))
    r = create_roadmap(db, startup)
    db.refresh(r)
    assert r.applied_template_keys == []


def test_applied_template_keys_roundtrips(db):
    startup = create_startup(db, owner=create_user(db))
    r = create_roadmap(db, startup)
    r.applied_template_keys = ["mvp-build"]
    db.flush()
    db.refresh(r)
    assert r.applied_template_keys == ["mvp-build"]

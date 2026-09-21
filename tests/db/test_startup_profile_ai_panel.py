from app.db.models.startup import StartupProfile
from tests.factories import create_startup, create_user


def test_startup_profile_ai_panel_nullable(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    assert s.profile.ai_panel is None  # nullable, defaults to NULL
    s.profile.ai_panel = "Got it — a fintech startup."
    db.flush()
    got = db.query(StartupProfile).filter_by(startup_id=s.id).one()
    assert got.ai_panel == "Got it — a fintech startup."

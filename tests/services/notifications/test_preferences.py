from datetime import UTC, datetime

from app.services.notifications.categories import CATEGORIES, category_for
from app.services.notifications.preferences import (
    effective_preferences, email_enabled, set_preferences,
)
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    return u, create_startup(db, owner=u)


def test_category_for_maps_events():
    assert category_for("document.shared") == "documents"
    assert category_for("mission.completed") == "roadmap_missions"
    assert category_for("workspace.member.joined") == "team"
    assert category_for("unknown.event") is None


def test_effective_defaults_all_on_when_unset(db):
    u, s = _ctx(db)
    eff = effective_preferences(db, user_id=u.id, startup_id=s.id)
    assert eff["master_email"] is True
    assert set(eff["categories"]) == set(CATEGORIES)
    assert all(eff["categories"].values())


def test_set_and_email_enabled(db):
    u, s = _ctx(db)
    set_preferences(db, user_id=u.id, startup_id=s.id, master_email=True,
                    categories={"documents": False})
    assert email_enabled(db, user_id=u.id, startup_id=s.id, category="documents") is False
    assert email_enabled(db, user_id=u.id, startup_id=s.id, category="team") is True
    # master off overrides everything
    set_preferences(db, user_id=u.id, startup_id=s.id, master_email=False, categories=None)
    assert email_enabled(db, user_id=u.id, startup_id=s.id, category="team") is False
    # uncategorized -> never emails
    assert email_enabled(db, user_id=u.id, startup_id=s.id, category=None) is False

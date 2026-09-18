from datetime import UTC, datetime

from app.db.models.notification import Notification
from app.platform.events import event_bus
from app.services.notifications.categories import category_for
from app.services.notifications.registry import register
from tests.factories import create_membership, create_startup, create_user


def _ws(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    return u, s


def test_scheduled_events_notify_active_members_and_map_categories(db):
    register()
    u, s = _ws(db)
    for event in ("mission.ready", "roadmap.milestone.overdue", "assessment.quarterly.due"):
        event_bus.publish(db, event, {"startup_id": str(s.id)})
    assert db.query(Notification).filter_by(user_id=u.id).count() == 3
    assert category_for("mission.ready") == "roadmap_missions"
    assert category_for("roadmap.milestone.overdue") == "roadmap_missions"
    assert category_for("assessment.quarterly.due") == "health_assessment"

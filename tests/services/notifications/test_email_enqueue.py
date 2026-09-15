from datetime import UTC, datetime

from app.db.models.job import Job
from app.db.models.notification import Notification
from app.platform.events import event_bus
from app.services.notifications.preferences import set_preferences
from app.services.notifications.registry import register
from tests.factories import create_membership, create_startup, create_user


def _two_members(db):
    a = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=a)
    create_membership(db, a, s)
    b = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, b, s)
    return a, b, s


def _email_jobs(db):
    return db.query(Job).filter(Job.type == "email.notification").all()


def test_enqueues_one_email_job_per_opted_in_recipient(db):
    register()
    a, b, s = _two_members(db)
    # A shares; recipient is B (members minus actor). B keeps default (documents ON).
    event_bus.publish(db, "document.shared", {"startup_id": str(s.id), "shared_by_id": str(a.id)})
    jobs = _email_jobs(db)
    assert len(jobs) == 1
    nid = jobs[0].payload["notification_id"]
    assert db.query(Notification).filter_by(id=nid, user_id=b.id).one()


def test_category_off_still_creates_inapp_but_no_email(db):
    register()
    a, b, s = _two_members(db)
    set_preferences(db, user_id=b.id, startup_id=s.id, master_email=True,
                    categories={"documents": False})
    event_bus.publish(db, "document.shared", {"startup_id": str(s.id), "shared_by_id": str(a.id)})
    assert _email_jobs(db) == []
    assert db.query(Notification).filter_by(user_id=b.id).count() == 1  # in-app still created


def test_rolled_back_action_leaves_no_email_job(db):
    register()
    a, _b, s = _two_members(db)
    with db.begin_nested() as sp:
        event_bus.publish(db, "document.shared", {"startup_id": str(s.id), "shared_by_id": str(a.id)})
        sp.rollback()
    assert _email_jobs(db) == []

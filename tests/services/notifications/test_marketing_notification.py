from app.db.models.enums import ChannelKey, ContentStatus, MembershipRole
from app.db.models.notification import Notification
from app.platform.events import DispatchingEventBus
from app.schemas.marketing import CalendarEntryCreate, CalendarEntryUpdate
from app.services.marketing import service as svc
from app.services.notifications import registry
from tests.factories import create_membership, create_startup, create_user


def test_publishing_notifies_workspace_except_actor(db, monkeypatch):
    # Wire the registry onto a throwaway bus and point the marketing service at it.
    bus = DispatchingEventBus()
    registry.register(bus)
    monkeypatch.setattr(svc, "event_bus", bus)

    founder = create_user(db)
    s = create_startup(db, owner=founder)
    other = create_user(db)
    create_membership(db, other, s, role=MembershipRole.team_member)

    e = svc.create_entry(
        db,
        startup_id=s.id,
        created_by=founder.id,
        data=CalendarEntryCreate(
            title="Big launch", channel=ChannelKey.email, status=ContentStatus.draft
        ),
    )
    svc.update_entry(
        db,
        startup_id=s.id,
        entry_id=e.id,
        actor_id=founder.id,
        data=CalendarEntryUpdate(status=ContentStatus.published),
    )

    notifs = (
        db.query(Notification).filter_by(startup_id=s.id, type="marketing.post.published").all()
    )
    recipients = {n.user_id for n in notifs}
    assert other.id in recipients  # workspace member notified
    assert founder.id not in recipients  # the actor (publisher) excluded
    assert all(n.title == "Scheduled post published: Big launch" for n in notifs)


def test_non_publish_update_creates_no_notification(db, monkeypatch):
    bus = DispatchingEventBus()
    registry.register(bus)
    monkeypatch.setattr(svc, "event_bus", bus)

    u = create_user(db)
    s = create_startup(db, owner=u)
    e = svc.create_entry(
        db,
        startup_id=s.id,
        created_by=u.id,
        data=CalendarEntryCreate(
            title="Draft", channel=ChannelKey.email, status=ContentStatus.draft
        ),
    )
    svc.update_entry(
        db,
        startup_id=s.id,
        entry_id=e.id,
        actor_id=u.id,
        data=CalendarEntryUpdate(title="Draft v2"),
    )
    assert db.query(Notification).filter_by(type="marketing.post.published").count() == 0

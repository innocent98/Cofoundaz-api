from app.db.models.enums import CampaignObjective, CampaignStatus, MembershipRole
from app.db.models.notification import Notification
from app.platform.events import DispatchingEventBus
from app.schemas.marketing import CampaignCreate, CampaignUpdate
from app.services.marketing import campaigns as svc
from app.services.notifications import registry
from tests.factories import create_membership, create_startup, create_user


def test_launch_notifies_workspace_except_actor(db, monkeypatch):
    bus = DispatchingEventBus()
    registry.register(bus)
    monkeypatch.setattr(svc, "event_bus", bus)
    founder = create_user(db)
    s = create_startup(db, owner=founder)
    other = create_user(db)
    create_membership(db, other, s, role=MembershipRole.team_member)
    c = svc.create_campaign(
        db, startup_id=s.id, data=CampaignCreate(name="Big", objective=CampaignObjective.launch)
    )
    svc.update_campaign(
        db,
        startup_id=s.id,
        campaign_id=c.id,
        actor_id=founder.id,
        data=CampaignUpdate(status=CampaignStatus.active),
    )
    notifs = (
        db.query(Notification).filter_by(startup_id=s.id, type="marketing.campaign.launched").all()
    )
    recipients = {n.user_id for n in notifs}
    assert other.id in recipients and founder.id not in recipients
    assert all(n.title == "Campaign launched: Big" for n in notifs)


def test_completion_notifies_workspace_except_actor(db, monkeypatch):
    bus = DispatchingEventBus()
    registry.register(bus)
    monkeypatch.setattr(svc, "event_bus", bus)
    founder = create_user(db)
    s = create_startup(db, owner=founder)
    other = create_user(db)
    create_membership(db, other, s, role=MembershipRole.team_member)
    c = svc.create_campaign(
        db, startup_id=s.id, data=CampaignCreate(name="Big", objective=CampaignObjective.launch)
    )
    svc.update_campaign(
        db,
        startup_id=s.id,
        campaign_id=c.id,
        actor_id=founder.id,
        data=CampaignUpdate(status=CampaignStatus.active),
    )
    svc.update_campaign(
        db,
        startup_id=s.id,
        campaign_id=c.id,
        actor_id=founder.id,
        data=CampaignUpdate(status=CampaignStatus.completed),
    )
    notifs = (
        db.query(Notification).filter_by(startup_id=s.id, type="marketing.campaign.completed").all()
    )
    recipients = {n.user_id for n in notifs}
    assert other.id in recipients and founder.id not in recipients
    assert all(n.title == "Campaign completed: Big" for n in notifs)

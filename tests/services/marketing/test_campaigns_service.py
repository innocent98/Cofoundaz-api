import pytest
from pydantic import ValidationError

from app.core.errors import AppError, NotFound
from app.db.models.enums import CampaignObjective, CampaignStatus
from app.platform import events as events_mod
from app.schemas.marketing import CampaignCreate, CampaignUpdate, SegmentCreate
from app.services.marketing import campaigns as svc
from app.services.marketing import segments as seg_svc
from tests.factories import create_startup, create_user


def _startup(db):
    return create_startup(db, owner=create_user(db))


def _seg(db, s, name="X"):
    return seg_svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name=name))


def test_create_with_segments_and_channel_mix(db):
    s = _startup(db)
    seg = _seg(db, s)
    c = svc.create_campaign(
        db,
        startup_id=s.id,
        data=CampaignCreate(
            name="Q4",
            objective=CampaignObjective.launch,
            budget=50000,
            channel_mix={"email": 60, "search": 40},
            segment_ids=[seg.id],
        ),
    )
    assert c.status == CampaignStatus.draft
    assert svc.campaign_segment_ids(db, c.id) == [seg.id]


def test_foreign_segment_id_rejected(db):
    s = _startup(db)
    other = _startup(db)
    foreign = _seg(db, other)
    with pytest.raises(AppError) as ei:
        svc.create_campaign(
            db,
            startup_id=s.id,
            data=CampaignCreate(
                name="C", objective=CampaignObjective.leads, segment_ids=[foreign.id]
            ),
        )
    assert ei.value.http_status == 422


def test_bad_channel_mix_rejected(db):
    with pytest.raises(ValidationError):  # 422 at the API boundary
        CampaignCreate(name="C", objective=CampaignObjective.leads, channel_mix={"email": 150})
    with pytest.raises(ValidationError):
        CampaignCreate(
            name="C", objective=CampaignObjective.leads, channel_mix={"not_a_channel": 10}
        )


def test_launch_sets_timestamp_and_emits_once(db, monkeypatch):
    s = _startup(db)
    u = create_user(db)
    c = svc.create_campaign(
        db, startup_id=s.id, data=CampaignCreate(name="C", objective=CampaignObjective.leads)
    )
    seen = []
    real = events_mod.event_bus.publish

    def spy(dbx, e, p):
        if e == "marketing.campaign.launched":
            seen.append(p)
        return real(dbx, e, p)

    monkeypatch.setattr(events_mod.event_bus, "publish", spy)
    svc.update_campaign(
        db,
        startup_id=s.id,
        campaign_id=c.id,
        actor_id=u.id,
        data=CampaignUpdate(status=CampaignStatus.active),
    )
    assert c.launched_at is not None
    assert len(seen) == 1
    assert seen[0]["actor_id"] == str(u.id)


def test_illegal_transition_rejected(db):
    s = _startup(db)
    u = create_user(db)
    c = svc.create_campaign(
        db, startup_id=s.id, data=CampaignCreate(name="C", objective=CampaignObjective.leads)
    )
    with pytest.raises(AppError) as ei:  # draft -> completed is illegal
        svc.update_campaign(
            db,
            startup_id=s.id,
            campaign_id=c.id,
            actor_id=u.id,
            data=CampaignUpdate(status=CampaignStatus.completed),
        )
    assert ei.value.http_status == 422


def test_illegal_transition_emits_no_event_and_leaves_timestamps(db, monkeypatch):
    s = _startup(db)
    u = create_user(db)
    c = svc.create_campaign(
        db, startup_id=s.id, data=CampaignCreate(name="C", objective=CampaignObjective.leads)
    )
    seen = []
    real = events_mod.event_bus.publish

    def spy(dbx, e, p):
        if e.startswith("marketing.campaign."):
            seen.append(p)
        return real(dbx, e, p)

    monkeypatch.setattr(events_mod.event_bus, "publish", spy)
    with pytest.raises(AppError) as ei:  # draft -> completed is illegal
        svc.update_campaign(
            db,
            startup_id=s.id,
            campaign_id=c.id,
            actor_id=u.id,
            data=CampaignUpdate(status=CampaignStatus.completed),
        )
    assert ei.value.http_status == 422
    assert seen == []
    assert c.status == CampaignStatus.draft
    assert c.launched_at is None
    assert c.completed_at is None


def test_reissue_current_status_emits_no_event(db, monkeypatch):
    s = _startup(db)
    u = create_user(db)
    c = svc.create_campaign(
        db, startup_id=s.id, data=CampaignCreate(name="C", objective=CampaignObjective.leads)
    )
    seen = []
    real = events_mod.event_bus.publish

    def spy(dbx, e, p):
        if e.startswith("marketing.campaign."):
            seen.append(p)
        return real(dbx, e, p)

    monkeypatch.setattr(events_mod.event_bus, "publish", spy)

    # Re-issuing the current status (draft -> draft) must be a no-op.
    svc.update_campaign(
        db,
        startup_id=s.id,
        campaign_id=c.id,
        actor_id=u.id,
        data=CampaignUpdate(status=CampaignStatus.draft),
    )
    assert seen == []
    assert c.launched_at is None
    assert c.completed_at is None

    svc.update_campaign(
        db,
        startup_id=s.id,
        campaign_id=c.id,
        actor_id=u.id,
        data=CampaignUpdate(status=CampaignStatus.active),
    )
    assert len(seen) == 1
    launched_at = c.launched_at
    assert launched_at is not None

    # Re-issuing the current status (active -> active) must not re-fire the event
    # or move the timestamp already set by the first launch.
    svc.update_campaign(
        db,
        startup_id=s.id,
        campaign_id=c.id,
        actor_id=u.id,
        data=CampaignUpdate(status=CampaignStatus.active),
    )
    assert len(seen) == 1
    assert c.launched_at == launched_at
    assert c.completed_at is None


def test_full_lifecycle(db):
    s = _startup(db)
    u = create_user(db)
    c = svc.create_campaign(
        db, startup_id=s.id, data=CampaignCreate(name="C", objective=CampaignObjective.leads)
    )
    svc.update_campaign(
        db,
        startup_id=s.id,
        campaign_id=c.id,
        actor_id=u.id,
        data=CampaignUpdate(status=CampaignStatus.active),
    )
    svc.update_campaign(
        db,
        startup_id=s.id,
        campaign_id=c.id,
        actor_id=u.id,
        data=CampaignUpdate(status=CampaignStatus.paused),
    )
    svc.update_campaign(
        db,
        startup_id=s.id,
        campaign_id=c.id,
        actor_id=u.id,
        data=CampaignUpdate(status=CampaignStatus.active),
    )
    svc.update_campaign(
        db,
        startup_id=s.id,
        campaign_id=c.id,
        actor_id=u.id,
        data=CampaignUpdate(status=CampaignStatus.completed),
    )
    assert c.status == CampaignStatus.completed
    assert c.completed_at is not None


def test_get_other_tenant_not_found(db):
    s = _startup(db)
    c = svc.create_campaign(
        db, startup_id=s.id, data=CampaignCreate(name="C", objective=CampaignObjective.leads)
    )
    with pytest.raises(NotFound):
        svc.get_campaign(db, startup_id=_startup(db).id, campaign_id=c.id)

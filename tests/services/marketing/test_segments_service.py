import pytest

from app.core.errors import AppError, NotFound
from app.db.models.business import BusinessRecord
from app.db.models.enums import CampaignObjective, RecordKind
from app.db.models.marketing import Campaign, CampaignSegment
from app.schemas.marketing import SegmentCreate
from app.services.marketing import segments as svc
from tests.factories import create_startup, create_user


def _startup(db):
    return create_startup(db, owner=create_user(db))


def _persona(db, startup, kind=RecordKind.persona):
    rec = BusinessRecord(startup_id=startup.id, kind=kind, data={"name": "P"}, position=0)
    db.add(rec)
    db.flush()
    return rec


def test_create_and_get_segment(db):
    s = _startup(db)
    seg = svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="SMB", est_size=100))
    got = svc.get_segment(db, startup_id=s.id, segment_id=seg.id)
    assert got.name == "SMB" and got.est_size == 100


def test_create_with_valid_persona(db):
    s = _startup(db)
    p = _persona(db, s)
    seg = svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="X", persona_id=p.id))
    assert seg.persona_id == p.id


def test_persona_must_be_persona_kind(db):
    s = _startup(db)
    comp = _persona(db, s, kind=RecordKind.competitor)
    with pytest.raises(AppError) as ei:
        svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="X", persona_id=comp.id))
    assert ei.value.http_status == 422


def test_persona_other_tenant_rejected(db):
    s = _startup(db)
    other = _startup(db)
    p = _persona(db, other)
    with pytest.raises(AppError) as ei:
        svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="X", persona_id=p.id))
    assert ei.value.http_status == 422


def test_get_other_tenant_not_found(db):
    s = _startup(db)
    seg = svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="X"))
    with pytest.raises(NotFound):
        svc.get_segment(db, startup_id=_startup(db).id, segment_id=seg.id)


def test_segment_campaigns_lists_users(db):
    s = _startup(db)
    seg = svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="X"))
    camp = Campaign(startup_id=s.id, name="C", objective=CampaignObjective.leads)
    db.add(camp)
    db.flush()
    db.add(CampaignSegment(campaign_id=camp.id, segment_id=seg.id))
    db.flush()
    used = svc.segment_campaigns(db, startup_id=s.id, segment_id=seg.id)
    assert [c.id for c in used] == [camp.id]


def test_delete_segment(db):
    s = _startup(db)
    seg = svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="X"))
    svc.delete_segment(db, startup_id=s.id, segment_id=seg.id)
    with pytest.raises(NotFound):
        svc.get_segment(db, startup_id=s.id, segment_id=seg.id)

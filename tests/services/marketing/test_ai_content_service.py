import pytest

from app.core.errors import AppError, NotFound
from app.db.models.enums import MarketingGenerationKind, MarketingGenerationStatus
from app.db.models.job import Job
from app.schemas.marketing import CopyGenerateRequest, SegmentCreate
from app.services.marketing import ai_content as svc
from app.services.marketing import segments as seg_svc
from tests.factories import create_startup, create_user


def _startup(db):
    return create_startup(db, owner=create_user(db))


def _req(**kw):
    base = {"asset_type": "ad", "tone": "bold", "key_message": "Ship it"}
    base.update(kw)
    return CopyGenerateRequest(**base)


def test_create_copy_generation_enqueues(db):
    s = _startup(db)
    u = create_user(db)
    g = svc.create_copy_generation(db, startup_id=s.id, created_by=u.id, data=_req())
    assert g.kind == MarketingGenerationKind.copy
    assert g.status == MarketingGenerationStatus.generating
    assert g.inputs["asset_type"] == "ad"
    jobs = db.query(Job).filter_by(type="ai.marketing.copy", startup_id=s.id).all()
    assert len(jobs) == 1
    assert jobs[0].payload["generation_id"] == str(g.id)


def test_copy_with_valid_segment(db):
    s = _startup(db)
    seg = seg_svc.create_segment(db, startup_id=s.id, data=SegmentCreate(name="SMB"))
    g = svc.create_copy_generation(
        db,
        startup_id=s.id,
        created_by=create_user(db).id,
        data=_req(audience_segment_id=seg.id),
    )
    assert g.inputs["audience_segment_id"] == str(seg.id)


def test_copy_foreign_segment_rejected(db):
    s = _startup(db)
    other = _startup(db)
    seg = seg_svc.create_segment(db, startup_id=other.id, data=SegmentCreate(name="X"))
    with pytest.raises(AppError) as ei:
        svc.create_copy_generation(
            db,
            startup_id=s.id,
            created_by=create_user(db).id,
            data=_req(audience_segment_id=seg.id),
        )
    assert ei.value.http_status == 422


def test_create_plan_week_enqueues(db):
    s = _startup(db)
    g = svc.create_plan_week_generation(db, startup_id=s.id, created_by=create_user(db).id)
    assert g.kind == MarketingGenerationKind.plan_week
    assert db.query(Job).filter_by(type="ai.marketing.plan_week", startup_id=s.id).count() == 1


def test_get_generation_kind_mismatch_not_found(db):
    s = _startup(db)
    g = svc.create_plan_week_generation(db, startup_id=s.id, created_by=create_user(db).id)
    with pytest.raises(NotFound):  # fetching a plan_week row via kind=copy
        svc.get_generation(
            db, startup_id=s.id, generation_id=g.id, kind=MarketingGenerationKind.copy
        )


def test_get_generation_other_tenant_not_found(db):
    s = _startup(db)
    g = svc.create_copy_generation(db, startup_id=s.id, created_by=create_user(db).id, data=_req())
    with pytest.raises(NotFound):
        svc.get_generation(
            db,
            startup_id=_startup(db).id,
            generation_id=g.id,
            kind=MarketingGenerationKind.copy,
        )


def test_list_copy_generations_only_copy(db):
    s = _startup(db)
    u = create_user(db)
    svc.create_copy_generation(db, startup_id=s.id, created_by=u.id, data=_req())
    svc.create_plan_week_generation(db, startup_id=s.id, created_by=u.id)
    rows = svc.list_copy_generations(db, startup_id=s.id)
    assert len(rows) == 1
    assert rows[0].kind == MarketingGenerationKind.copy

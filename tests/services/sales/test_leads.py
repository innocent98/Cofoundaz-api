import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.errors import AppError, NotFound
from app.db.models.activity import ActivityLog
from app.db.models.enums import LeadSource, LeadStatus, MembershipRole
from app.db.models.sales import SalesLead
from app.schemas.sales import LeadCreate, LeadUpdate
from app.services.sales import leads as svc
from tests.factories import create_membership, create_startup, create_user


def _ws(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    return u, s


def _fields(exc: AppError) -> list[str]:
    return [e["field"] for e in exc.field_errors]


def _actions(db, startup_id):
    return set(db.scalars(select(ActivityLog.action).where(ActivityLog.startup_id == startup_id)))


def test_create_requires_name_or_company(db):
    u, s = _ws(db)
    with pytest.raises(AppError) as ei:
        svc.create_lead(db, s.id, LeadCreate(email="a@b.co"), actor_id=u.id)
    assert ei.value.http_status == 422


def test_create_allows_duplicate_emails_and_logs(db):
    u, s = _ws(db)
    a = svc.create_lead(db, s.id, LeadCreate(name="A", email="x@acme.co"), actor_id=u.id)
    b = svc.create_lead(db, s.id, LeadCreate(name="B", email="X@acme.co"), actor_id=u.id)
    assert a.id != b.id and a.status == LeadStatus.new and a.source == LeadSource.manual
    assert "sales.lead.created" in _actions(db, s.id)


def test_create_rejects_foreign_owner(db):
    u, s = _ws(db)
    other, _s2 = _ws(db)
    with pytest.raises(AppError) as ei:
        svc.create_lead(db, s.id, LeadCreate(name="A", owner_id=other.id), actor_id=u.id)
    assert _fields(ei.value) == ["owner_id"]


def test_create_cannot_start_converted(db):
    with pytest.raises(ValueError):
        LeadCreate(name="A", status=LeadStatus.converted)


def test_update_and_empty_identity_rejected(db):
    u, s = _ws(db)
    lead = svc.create_lead(db, s.id, LeadCreate(name="A"), actor_id=u.id)
    upd = svc.update_lead(
        db, s.id, lead.id, LeadUpdate(status=LeadStatus.working, company="Acme"), actor_id=u.id
    )
    assert upd.status == LeadStatus.working and upd.company == "Acme"
    with pytest.raises(AppError):
        svc.update_lead(db, s.id, lead.id, LeadUpdate(name=None, company=None), actor_id=u.id)
    # a rejected write must leave the session usable
    assert svc.get_lead(db, s.id, lead.id).name == "A"
    assert "sales.lead.updated" in _actions(db, s.id)


def test_update_cannot_set_converted(db):
    u, s = _ws(db)
    lead = svc.create_lead(db, s.id, LeadCreate(name="A"), actor_id=u.id)
    with pytest.raises(AppError):
        svc.update_lead(db, s.id, lead.id, LeadUpdate(status=LeadStatus.converted), actor_id=u.id)


def test_converted_lead_locked(db):
    u, s = _ws(db)
    lead = svc.create_lead(db, s.id, LeadCreate(name="A"), actor_id=u.id)
    lead.status = LeadStatus.converted
    db.flush()
    with pytest.raises(AppError) as ei:
        svc.update_lead(db, s.id, lead.id, LeadUpdate(status=LeadStatus.new), actor_id=u.id)
    assert "already converted" in ei.value.message
    with pytest.raises(AppError):
        svc.delete_lead(db, s.id, lead.id, actor_id=u.id)


def test_delete_and_cross_tenant(db):
    u, s = _ws(db)
    u2, s2 = _ws(db)
    lead = svc.create_lead(db, s.id, LeadCreate(name="A"), actor_id=u.id)
    with pytest.raises(NotFound):
        svc.get_lead(db, s2.id, lead.id)
    with pytest.raises(NotFound):
        svc.delete_lead(db, s2.id, lead.id, actor_id=u2.id)
    svc.delete_lead(db, s.id, lead.id, actor_id=u.id)
    assert db.get(SalesLead, lead.id) is None
    assert "sales.lead.deleted" in _actions(db, s.id)
    with pytest.raises(NotFound):
        svc.get_lead(db, s.id, uuid.uuid4())


def test_list_filters_and_pagination(db):
    u, s = _ws(db)
    svc.create_lead(db, s.id, LeadCreate(name="Alpha", source=LeadSource.referral), actor_id=u.id)
    b = svc.create_lead(db, s.id, LeadCreate(company="Beta 100%", owner_id=u.id), actor_id=u.id)
    b.status = LeadStatus.working
    db.flush()
    rows, total = svc.list_leads(
        db, s.id, status=None, owner_id=None, source=None, q=None, limit=1, offset=0
    )
    assert total == 2 and len(rows) == 1
    rows, total = svc.list_leads(
        db, s.id, status=LeadStatus.working, owner_id=None, source=None, q=None, limit=50, offset=0
    )
    assert [r.id for r in rows] == [b.id]
    rows, _ = svc.list_leads(
        db, s.id, status=None, owner_id=u.id, source=None, q=None, limit=50, offset=0
    )
    assert [r.id for r in rows] == [b.id]
    rows, _ = svc.list_leads(
        db, s.id, status=None, owner_id=None, source=LeadSource.referral, q=None, limit=50, offset=0
    )
    assert len(rows) == 1 and rows[0].name == "Alpha"
    rows, total = svc.list_leads(
        db, s.id, status=None, owner_id=None, source=None, q="100%", limit=50, offset=0
    )
    assert total == 1

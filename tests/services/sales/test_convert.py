import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.core.errors import AppError
from app.db.models.activity import ActivityLog
from app.db.models.enums import LeadStatus, MembershipRole
from app.db.models.sales import DealStageChange, SalesAccount
from app.schemas.sales import AccountCreate, LeadCreate
from app.services.sales import leads as svc
from app.services.sales import people as people_svc
from app.services.sales import stages as stages_svc
from tests.factories import create_membership, create_startup, create_user


def _ws(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    stages_svc.ensure_stages(db, s.id)
    return u, s


def _lead(db, u, s, **kw):
    return svc.create_lead(db, s.id, LeadCreate(**kw), actor_id=u.id)


def _convert(db, u, s, lead):
    return svc.convert_lead(db, startup_id=s.id, lead_id=lead.id, actor_id=u.id)


def test_convert_reuses_existing_account_case_insensitively(db):
    u, s = _ws(db)
    acc = people_svc.create_account(db, s.id, AccountCreate(name="Acme Ltd"), actor_id=u.id)
    lead = _lead(db, u, s, name="Ada Obi Eze", company="ACME ltd", email="ada@acme.co")
    res = _convert(db, u, s, lead)
    assert res.account.id == acc.id
    assert res.contact.first_name == "Ada" and res.contact.last_name == "Obi Eze"
    assert res.contact.email == "ada@acme.co" and res.contact.account_id == acc.id
    assert res.deal.name == "ACME ltd" and res.deal.account_id == acc.id
    assert res.deal.contact_id == res.contact.id and res.deal.value_minor == 0
    assert res.deal.owner_id == u.id
    assert lead.status == LeadStatus.converted
    assert lead.converted_deal_id == res.deal.id
    assert lead.converted_contact_id == res.contact.id
    assert lead.converted_account_id == acc.id
    n = db.scalar(
        select(func.count()).select_from(SalesAccount).where(SalesAccount.startup_id == s.id)
    )
    assert n == 1


def test_convert_creates_account_when_missing(db):
    u, s = _ws(db)
    res = _convert(db, u, s, _lead(db, u, s, name="Bola", company="Newco"))
    assert res.account is not None and res.account.name == "Newco"
    assert res.contact.last_name is None


def test_convert_without_company_has_no_account(db):
    u, s = _ws(db)
    lead = _lead(db, u, s, name="Solo Person")
    res = _convert(db, u, s, lead)
    assert res.account is None and res.deal.account_id is None
    assert res.contact.account_id is None and lead.converted_account_id is None
    assert res.deal.name == "Solo Person"


def test_convert_no_name_uses_company_and_no_email(db):
    u, s = _ws(db)
    res = _convert(db, u, s, _lead(db, u, s, company="Acme"))
    assert res.contact.first_name == "Acme" and res.contact.email is None


def test_convert_uses_lead_owner_and_first_stage_with_history(db):
    u, s = _ws(db)
    lead = _lead(db, u, s, name="A", company="Co", owner_id=u.id)
    res = _convert(db, u, s, lead)
    first = stages_svc.ensure_stages(db, s.id)[0]
    assert res.deal.stage_id == first.id and res.deal.owner_id == u.id
    h = list(db.scalars(select(DealStageChange).where(DealStageChange.deal_id == res.deal.id)))
    assert len(h) == 1 and h[0].from_stage_id is None and h[0].to_stage_id == first.id
    row = db.scalar(select(ActivityLog).where(ActivityLog.action == "sales.lead.converted"))
    assert row.entity_type == "sales_lead" and row.entity_id == lead.id


def test_double_convert_422(db):
    u, s = _ws(db)
    lead = _lead(db, u, s, name="A", company="Co")
    _convert(db, u, s, lead)
    with pytest.raises(AppError) as ei:
        _convert(db, u, s, lead)
    assert ei.value.http_status == 422
    assert ei.value.message == "This lead was already converted."


def test_convert_foreign_lead_404(db):
    u, s = _ws(db)
    other_u, other_s = _ws(db)
    lead = _lead(db, other_u, other_s, name="A")
    with pytest.raises(AppError) as ei:
        _convert(db, u, s, lead)
    assert ei.value.http_status == 404
    with pytest.raises(AppError):
        svc.convert_lead(db, startup_id=s.id, lead_id=uuid.uuid4(), actor_id=u.id)

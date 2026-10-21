import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.errors import AppError, NotFound
from app.db.models.activity import ActivityLog
from app.db.models.enums import MembershipRole, MembershipStatus, StageKind
from app.db.models.sales import Deal, PipelineStage, SalesAccount, SalesContact, SalesLead
from app.schemas.sales import AccountCreate, AccountUpdate, ContactCreate, ContactUpdate
from app.services.sales import people as svc
from tests.factories import create_membership, create_startup, create_user


def _ws(db, role=MembershipRole.founder):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=role)
    db.flush()
    return u, s


def _fields(exc: AppError) -> list[str]:
    return [e["field"] for e in exc.field_errors]


def _actions(db, startup_id):
    return set(db.scalars(select(ActivityLog.action).where(ActivityLog.startup_id == startup_id)))


def test_check_owner_accepts_active_core_member(db):
    u, s = _ws(db)
    svc.check_owner(db, s.id, u.id)
    svc.check_owner(db, s.id, None)


@pytest.mark.parametrize("role", [MembershipRole.mentor, MembershipRole.accountant])
def test_check_owner_rejects_non_core_role(db, role):
    _u, s = _ws(db)
    other = create_user(db)
    create_membership(db, other, s, role=role)
    db.flush()
    with pytest.raises(AppError) as ei:
        svc.check_owner(db, s.id, other.id)
    assert _fields(ei.value) == ["owner_id"]


def test_check_owner_rejects_foreign_and_inactive(db):
    _u, s = _ws(db)
    foreign, _s2 = _ws(db)
    with pytest.raises(AppError):
        svc.check_owner(db, s.id, foreign.id)
    tm = create_user(db)
    m = create_membership(db, tm, s, role=MembershipRole.team_member)
    m.status = MembershipStatus.suspended
    db.flush()
    with pytest.raises(AppError):
        svc.check_owner(db, s.id, tm.id)


def test_account_crud_and_activity(db):
    u, s = _ws(db)
    a = svc.create_account(db, s.id, AccountCreate(name="  Acme  ", owner_id=u.id), actor_id=u.id)
    assert a.name == "Acme"
    a = svc.update_account(db, s.id, a.id, AccountUpdate(industry="Fintech"), actor_id=u.id)
    assert a.industry == "Fintech"
    svc.delete_account(db, s.id, a.id, actor_id=u.id)
    assert db.get(SalesAccount, a.id) is None
    assert {"sales.account.created", "sales.account.updated", "sales.account.deleted"} <= _actions(
        db, s.id
    )
    row = db.scalars(select(ActivityLog).where(ActivityLog.action == "sales.account.created")).one()
    assert row.entity_type == "sales_account"


def test_duplicate_account_name_case_insensitive_keeps_session_usable(db):
    u, s = _ws(db)
    svc.create_account(db, s.id, AccountCreate(name="Acme"), actor_id=u.id)
    with pytest.raises(AppError) as ei:
        svc.create_account(db, s.id, AccountCreate(name="ACME"), actor_id=u.id)
    assert _fields(ei.value) == ["name"]
    # follow-up read in the same session must still work (SAVEPOINT rule)
    assert len(svc.list_accounts(db, s.id, q=None, owner_id=None, limit=50, offset=0)[0]) == 1


def test_duplicate_name_on_update_and_same_name_other_workspace_ok(db):
    u, s = _ws(db)
    u2, s2 = _ws(db)
    svc.create_account(db, s.id, AccountCreate(name="Acme"), actor_id=u.id)
    b = svc.create_account(db, s.id, AccountCreate(name="Beta"), actor_id=u.id)
    with pytest.raises(AppError) as ei:
        svc.update_account(db, s.id, b.id, AccountUpdate(name="acme"), actor_id=u.id)
    assert _fields(ei.value) == ["name"]
    svc.create_account(db, s2.id, AccountCreate(name="Acme"), actor_id=u2.id)
    assert svc.get_account(db, s.id, b.id).name == "Beta"


def test_foreign_owner_rejected_on_account(db):
    u, s = _ws(db)
    foreign, _ = _ws(db)
    with pytest.raises(AppError) as ei:
        svc.create_account(db, s.id, AccountCreate(name="X", owner_id=foreign.id), actor_id=u.id)
    assert _fields(ei.value) == ["owner_id"]


def test_account_list_filter_and_pagination(db):
    u, s = _ws(db)
    for n in ["Alpha Co", "Beta Co", "Alphabet"]:
        svc.create_account(db, s.id, AccountCreate(name=n, owner_id=u.id), actor_id=u.id)
    rows, total = svc.list_accounts(db, s.id, q="alpha", owner_id=None, limit=50, offset=0)
    assert total == 2
    assert [r.name for r in rows] == ["Alpha Co", "Alphabet"]
    rows, total = svc.list_accounts(db, s.id, q=None, owner_id=None, limit=1, offset=1)
    assert total == 3 and len(rows) == 1
    _rows, total = svc.list_accounts(db, s.id, q=None, owner_id=uuid.uuid4(), limit=50, offset=0)
    assert total == 0


def test_account_cross_tenant_not_found(db):
    u, s = _ws(db)
    _u2, s2 = _ws(db)
    a = svc.create_account(db, s2.id, AccountCreate(name="Theirs"), actor_id=u.id)
    for fn in (
        lambda: svc.get_account(db, s.id, a.id),
        lambda: svc.update_account(db, s.id, a.id, AccountUpdate(name="x"), actor_id=u.id),
        lambda: svc.delete_account(db, s.id, a.id, actor_id=u.id),
    ):
        with pytest.raises(NotFound):
            fn()


def test_contact_crud_and_activity(db):
    u, s = _ws(db)
    a = svc.create_account(db, s.id, AccountCreate(name="Acme"), actor_id=u.id)
    c = svc.create_contact(
        db,
        s.id,
        ContactCreate(first_name="Ada", last_name="L", email="Ada@X.com", account_id=a.id),
        actor_id=u.id,
    )
    assert c.account_id == a.id
    c = svc.update_contact(
        db, s.id, c.id, ContactUpdate(title="CTO", account_id=None), actor_id=u.id
    )
    assert c.title == "CTO" and c.account_id is None
    svc.delete_contact(db, s.id, c.id, actor_id=u.id)
    assert {"sales.contact.created", "sales.contact.updated", "sales.contact.deleted"} <= _actions(
        db, s.id
    )


def test_contact_foreign_account_rejected(db):
    u, s = _ws(db)
    _u2, s2 = _ws(db)
    theirs = svc.create_account(db, s2.id, AccountCreate(name="Theirs"), actor_id=u.id)
    with pytest.raises(AppError) as ei:
        svc.create_contact(
            db, s.id, ContactCreate(first_name="A", account_id=theirs.id), actor_id=u.id
        )
    assert _fields(ei.value) == ["account_id"]
    mine = svc.create_contact(db, s.id, ContactCreate(first_name="A"), actor_id=u.id)
    with pytest.raises(AppError):
        svc.update_contact(db, s.id, mine.id, ContactUpdate(account_id=theirs.id), actor_id=u.id)


def test_contact_list_filters(db):
    u, s = _ws(db)
    a = svc.create_account(db, s.id, AccountCreate(name="Acme"), actor_id=u.id)
    svc.create_contact(
        db, s.id, ContactCreate(first_name="Ada", email="ada@x.com", account_id=a.id), actor_id=u.id
    )
    svc.create_contact(db, s.id, ContactCreate(first_name="Bob", last_name="Smith"), actor_id=u.id)
    assert svc.list_contacts(db, s.id, account_id=a.id, q=None, limit=50, offset=0)[1] == 1
    assert svc.list_contacts(db, s.id, account_id=None, q="smith", limit=50, offset=0)[1] == 1
    assert svc.list_contacts(db, s.id, account_id=None, q="ADA@X", limit=50, offset=0)[1] == 1
    assert svc.list_contacts(db, s.id, account_id=None, q=None, limit=50, offset=0)[1] == 2


def _stage(db, s):
    st = PipelineStage(startup_id=s.id, name="New", position=0, kind=StageKind.open)
    db.add(st)
    db.flush()
    return st


def test_delete_account_with_deals_refused(db):
    u, s = _ws(db)
    a = svc.create_account(db, s.id, AccountCreate(name="Acme"), actor_id=u.id)
    db.add(Deal(startup_id=s.id, name="D", stage_id=_stage(db, s).id, account_id=a.id))
    db.flush()
    with pytest.raises(AppError) as ei:
        svc.delete_account(db, s.id, a.id, actor_id=u.id)
    assert ei.value.message == "Remove or move its deals first."
    assert svc.get_account(db, s.id, a.id) is not None


def test_delete_account_detaches_contacts_and_leads(db):
    u, s = _ws(db)
    a = svc.create_account(db, s.id, AccountCreate(name="Acme"), actor_id=u.id)
    c = svc.create_contact(db, s.id, ContactCreate(first_name="A", account_id=a.id), actor_id=u.id)
    lead = SalesLead(startup_id=s.id, name="L", converted_account_id=a.id)
    db.add(lead)
    db.flush()
    svc.delete_account(db, s.id, a.id, actor_id=u.id)
    db.refresh(c)
    db.refresh(lead)
    assert c.account_id is None
    assert lead.converted_account_id is None


def test_delete_contact_nulls_deal_contact(db):
    u, s = _ws(db)
    c = svc.create_contact(db, s.id, ContactCreate(first_name="A"), actor_id=u.id)
    d = Deal(startup_id=s.id, name="D", stage_id=_stage(db, s).id, contact_id=c.id)
    db.add(d)
    db.flush()
    svc.delete_contact(db, s.id, c.id, actor_id=u.id)
    db.refresh(d)
    assert d.contact_id is None
    assert db.get(SalesContact, c.id) is None


def test_account_detail_shape(db):
    u, s = _ws(db)
    a = svc.create_account(db, s.id, AccountCreate(name="Acme"), actor_id=u.id)
    svc.create_contact(db, s.id, ContactCreate(first_name="A", account_id=a.id), actor_id=u.id)
    detail = svc.account_detail(db, s.id, a.id)
    assert detail["account"].id == a.id
    assert len(detail["contacts"]) == 1
    assert detail["open_deals"] == []
    assert detail["lifetime_won_minor"] == 0

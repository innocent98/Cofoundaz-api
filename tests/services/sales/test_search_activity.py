from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.models.activity import ActivityLog
from app.db.models.enums import SALES_ROLES, MembershipRole
from app.db.models.sales import Deal, SalesAccount, SalesContact, SalesLead
from app.platform.activity import activity_visibility_filter, activity_visible
from app.services.sales import stages as stages_svc
from app.services.search.service import search
from tests.factories import create_activity, create_membership, create_startup, create_user


def _ws(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    first = stages_svc.ensure_stages(db, s.id)[0]
    db.add_all(
        [
            Deal(
                startup_id=s.id,
                name="Zorbax rollout",
                value_minor=1,
                currency="NGN",
                stage_id=first.id,
                last_activity_at=datetime.now(UTC),
            ),
            SalesAccount(startup_id=s.id, name="Zorbax Holdings"),
            SalesContact(
                startup_id=s.id, first_name="Zed", last_name="Zorbax", email="z@zorbax.test"
            ),
            SalesLead(startup_id=s.id, name="Zoe", company="Zorbax Labs", email="zoe@x.test"),
        ]
    )
    db.flush()
    return u, s


def test_founder_finds_every_sales_source(db):
    u, s = _ws(db)
    out = search(db, s, u, MembershipRole.founder, "zorbax")
    by_type = {r["type"]: r for r in out["results"]}
    assert {"deal", "sales_account", "sales_contact", "sales_lead"} <= set(by_type)
    assert by_type["deal"]["href"] == f"/sales?deal={by_type['deal']['id']}"
    assert by_type["sales_account"]["href"] == f"/sales/accounts/{by_type['sales_account']['id']}"
    assert (
        by_type["sales_contact"]["href"]
        == f"/sales/contacts?contact={by_type['sales_contact']['id']}"
    )
    assert by_type["sales_lead"]["href"] == f"/sales/leads?lead={by_type['sales_lead']['id']}"
    assert all(r["subtitle"] for r in out["results"] if r["type"].startswith(("sales_", "deal")))


def test_contact_matches_email_and_lead_matches_company_and_email(db):
    u, s = _ws(db)
    assert any(
        r["type"] == "sales_contact"
        for r in search(db, s, u, MembershipRole.team_member, "z@zorbax")["results"]
    )
    assert any(
        r["type"] == "sales_lead"
        for r in search(db, s, u, MembershipRole.founder, "zoe@x")["results"]
    )


@pytest.mark.parametrize(
    "role",
    [
        MembershipRole.mentor,
        MembershipRole.accountant,
        MembershipRole.investor,
        MembershipRole.business_consultant,
        MembershipRole.legal_advisor,
    ],
)
def test_other_roles_get_no_sales_hits_or_pages(db, role):
    u, s = _ws(db)
    out = search(db, s, u, role, "zorbax")
    assert not [
        r for r in out["results"] if r["type"] in ("deal",) or r["type"].startswith("sales")
    ]
    pages = search(db, s, u, role, "sales")["results"]
    assert not [r for r in pages if r["href"].startswith("/sales")]


def test_sales_pages_for_founder(db):
    u, s = _ws(db)
    hrefs = {r["href"] for r in search(db, s, u, MembershipRole.founder, "sales")["results"]}
    assert "/sales" in hrefs
    assert "/sales/leads" in {
        r["href"] for r in search(db, s, u, MembershipRole.founder, "leads")["results"]
    }
    assert "/sales/accounts" in {
        r["href"] for r in search(db, s, u, MembershipRole.founder, "accounts")["results"]
    }


def test_search_is_tenant_isolated(db):
    _u, _s = _ws(db)
    other_u = create_user(db, email_verified_at=datetime.now(UTC))
    other = create_startup(db, owner=other_u)
    db.flush()
    out = search(db, other, other_u, MembershipRole.founder, "zorbax")
    assert [r for r in out["results"] if r["type"] != "page"] == []


@pytest.mark.parametrize("entity", ["deal", "sales_activity", "sales_lead", "sales_account"])
def test_activity_visibility_python_and_sql_agree(db, entity):
    u, s = _ws(db)
    for role in MembershipRole:
        action = f"sales.{entity}.updated"
        assert activity_visible(role, action, entity) == (role in SALES_ROLES)
    assert activity_visible(MembershipRole.mentor, "sales.deal.won", "deal") is False
    assert activity_visible(MembershipRole.founder, "sales.deal.won", "deal") is True
    create_activity(db, startup=s, actor=u, action="sales.deal.won", entity_type="deal")
    db.flush()
    for role in MembershipRole:
        rows = db.scalars(
            select(ActivityLog.id).where(
                ActivityLog.startup_id == s.id,
                ActivityLog.action.like("sales.%"),
                activity_visibility_filter(role),
            )
        ).all()
        assert bool(rows) == (role in SALES_ROLES), role

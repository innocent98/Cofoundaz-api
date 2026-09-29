from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/marketing"
NON_MARKETING_ROLES = [MembershipRole.mentor, MembershipRole.investor]


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _member(db, *, role=MembershipRole.founder, startup=None):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    if startup is None:
        startup = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, startup, _headers(u, startup)


def test_keyword_crud_roundtrip(client, db):
    _u, _s, h = _member(db)
    created = client.post(
        f"{BASE}/keywords",
        json={
            "keyword": "automated daily savings",
            "volume": "2.4K",
            "difficulty": 45,
            "current_rank": 12,
            "target_page": "/x",
        },
        headers=h,
    )
    assert created.status_code == 200, created.text
    kid = created.json()["data"]["id"]
    assert created.json()["data"]["volume"] == "2.4K"

    listed = client.get(f"{BASE}/keywords", headers=h)
    assert listed.status_code == 200
    assert any(k["id"] == kid for k in listed.json()["data"]["keywords"])

    patched = client.patch(f"{BASE}/keywords/{kid}", json={"current_rank": 4}, headers=h)
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["current_rank"] == 4

    deleted = client.delete(f"{BASE}/keywords/{kid}", headers=h)
    assert deleted.status_code == 200, deleted.text


def test_keyword_difficulty_out_of_range_422(client, db):
    _u, _s, h = _member(db)
    resp = client.post(f"{BASE}/keywords", json={"keyword": "x", "difficulty": 150}, headers=h)
    assert resp.status_code == 422, resp.text


def test_keyword_patch_explicit_null_422_and_partial_update_ok(client, db):
    _u, _s, h = _member(db)
    created = client.post(f"{BASE}/keywords", json={"keyword": "seed kw"}, headers=h)
    assert created.status_code == 200, created.text
    kid = created.json()["data"]["id"]

    nulled = client.patch(f"{BASE}/keywords/{kid}", json={"keyword": None}, headers=h)
    assert nulled.status_code == 422, nulled.text

    partial = client.patch(f"{BASE}/keywords/{kid}", json={"current_rank": 3}, headers=h)
    assert partial.status_code == 200, partial.text
    assert partial.json()["data"]["current_rank"] == 3
    assert partial.json()["data"]["keyword"] == "seed kw"


@pytest.mark.parametrize("role", NON_MARKETING_ROLES)
def test_keyword_rbac_forbidden(client, db, role):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    posted = client.post(f"{BASE}/keywords", json={"keyword": "x"}, headers=h)
    assert posted.status_code == 403, posted.text
    listed = client.get(f"{BASE}/keywords", headers=h)
    assert listed.status_code == 403, listed.text


def test_tracked_page_create_seeds_checklist_and_toggles(client, db):
    _u, _s, h = _member(db)
    created = client.post(f"{BASE}/seo/pages", json={"url": "/pricing"}, headers=h)
    assert created.status_code == 200, created.text
    pid = created.json()["data"]["id"]
    cl = created.json()["data"]["checklist"]
    assert cl["h1"] is False
    assert created.json()["data"]["total"] == 8
    assert created.json()["data"]["completed"] == 0

    patched = client.patch(f"{BASE}/seo/pages/{pid}", json={"checklist": {"h1": True}}, headers=h)
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["checklist"]["h1"] is True
    assert patched.json()["data"]["checklist"]["meta_description"] is False  # others preserved
    assert patched.json()["data"]["completed"] == 1


def test_tracked_page_duplicate_url_422(client, db):
    _u, _s, h = _member(db)
    first = client.post(f"{BASE}/seo/pages", json={"url": "/dup"}, headers=h)
    assert first.status_code == 200, first.text
    dup = client.post(f"{BASE}/seo/pages", json={"url": "/dup"}, headers=h)
    assert dup.status_code == 422, dup.text
    # The SAVEPOINT must leave the session usable: a follow-up read still works and
    # only the first page persisted (the dup was never committed).
    after = client.get(f"{BASE}/seo/pages", headers=h)
    assert after.status_code == 200, after.text
    assert len(after.json()["data"]["pages"]) == 1


def test_tracked_page_unknown_checklist_key_422(client, db):
    _u, _s, h = _member(db)
    created = client.post(f"{BASE}/seo/pages", json={"url": "/p"}, headers=h)
    pid = created.json()["data"]["id"]
    bad = client.patch(
        f"{BASE}/seo/pages/{pid}", json={"checklist": {"not_a_real_item": True}}, headers=h
    )
    assert bad.status_code == 422, bad.text


def test_positioning_get_before_put_is_empty(client, db):
    _u, _s, h = _member(db)
    got = client.get(f"{BASE}/positioning", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["statement"] is None
    assert got.json()["data"]["audience"] is None


def test_positioning_empty_put_yields_null_statement(client, db):
    # An all-blank PUT must not store a dangling "For  who ,  is the  that ." skeleton;
    # statement stays null, consistent with the GET-before-PUT shape.
    _u, _s, h = _member(db)
    put = client.put(f"{BASE}/positioning", json={}, headers=h)
    assert put.status_code == 200, put.text
    assert put.json()["data"]["statement"] is None


def test_positioning_upsert_composes_statement_and_is_single_row(client, db):
    _u, _s, h = _member(db)
    body = {
        "audience": "gig workers",
        "need": "save on irregular income",
        "product": "Kolo",
        "category": "savings app",
        "differentiator": "saves automatically",
    }
    put1 = client.put(f"{BASE}/positioning", json=body, headers=h)
    assert put1.status_code == 200, put1.text
    assert put1.json()["data"]["statement"] == (
        "For gig workers who save on irregular income, Kolo is the savings app that saves automatically."
    )
    put2 = client.put(f"{BASE}/positioning", json={**body, "product": "KoloPay"}, headers=h)
    assert put2.status_code == 200, put2.text
    assert "KoloPay" in put2.json()["data"]["statement"]
    got = client.get(f"{BASE}/positioning", headers=h)
    assert got.json()["data"]["product"] == "KoloPay"

from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/legal"

# Two sets, not one (spec D1): a legal advisor reads every GET and writes nothing.
READER_ROLES = [
    MembershipRole.founder,
    MembershipRole.team_member,
    MembershipRole.legal_advisor,
]
WRITER_ROLES = [MembershipRole.founder, MembershipRole.team_member]
FORBIDDEN_ROLES = [
    MembershipRole.mentor,
    MembershipRole.accountant,
    MembershipRole.business_consultant,
    MembershipRole.investor,
]


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


# --- formation: jurisdictions ------------------------------------------------------------------


def test_jurisdictions_lists_code_and_name(client, db):
    _u, _s, h = _member(db)
    r = client.get(f"{BASE}/formation/jurisdictions", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()["data"]["jurisdictions"]
    assert {j["code"] for j in rows} == {"NG", "US-DE"}
    # Codes, not the mock's display strings (spec §FE cross-check 3).
    by_code = {j["code"]: j["name"] for j in rows}
    assert by_code["NG"] == "Nigeria" and by_code["US-DE"] == "Delaware, USA"


# --- formation: checklist ----------------------------------------------------------------------


def test_checklist_merges_the_catalog_with_progress(client, db):
    _u, _s, h = _member(db)
    r = client.get(f"{BASE}/formation/checklist?jurisdiction=NG", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["jurisdiction"] == {"code": "NG", "name": "Nigeria"}
    assert data["estimates"] is True and data["disclaimer"]
    first = data["steps"][0]
    assert first["completed"] is False and first["completed_at"] is None
    for field in ("key", "title", "authority", "est_days", "est_cost_minor", "est_cost_currency"):
        assert field in first
    assert "meta" not in first  # the mock's squashed string is not the contract


def test_checklist_for_an_unsupported_jurisdiction_is_404(client, db):
    _u, _s, h = _member(db)
    # Kenya and Ghana are not supported in Slice 1 (spec D6), and a missing catalog entry is a
    # 404 rather than a 422 — it names a resource, and 404 keeps the catalog non-enumerable (D3).
    assert client.get(f"{BASE}/formation/checklist?jurisdiction=KE", headers=h).status_code == 404


def test_checklist_requires_the_jurisdiction_param(client, db):
    _u, _s, h = _member(db)
    assert client.get(f"{BASE}/formation/checklist", headers=h).status_code == 422


# --- formation: ticking ------------------------------------------------------------------------


def test_tick_and_untick_a_step(client, db):
    _u, _s, h = _member(db)
    ticked = client.patch(
        f"{BASE}/formation/steps/reserve_name",
        json={"jurisdiction": "NG", "completed": True},
        headers=h,
    )
    assert ticked.status_code == 200, ticked.text
    steps = {s["key"]: s for s in ticked.json()["data"]["steps"]}
    assert steps["reserve_name"]["completed"] is True
    assert steps["reserve_name"]["completed_at"] is not None

    unticked = client.patch(
        f"{BASE}/formation/steps/reserve_name",
        json={"jurisdiction": "NG", "completed": False},
        headers=h,
    )
    assert unticked.status_code == 200
    steps = {s["key"]: s for s in unticked.json()["data"]["steps"]}
    assert steps["reserve_name"]["completed"] is False


def test_a_duplicate_tick_is_a_no_op(client, db):
    _u, _s, h = _member(db)
    body = {"jurisdiction": "NG", "completed": True}
    assert (
        client.patch(f"{BASE}/formation/steps/reserve_name", json=body, headers=h).status_code
        == 200
    )
    again = client.patch(f"{BASE}/formation/steps/reserve_name", json=body, headers=h)
    assert again.status_code == 200, again.text
    ticked = [s for s in again.json()["data"]["steps"] if s["completed"]]
    assert len(ticked) == 1


def test_a_step_key_from_another_jurisdiction_is_404(client, db):
    _u, _s, h = _member(db)
    r = client.patch(
        f"{BASE}/formation/steps/reserve_name",
        json={"jurisdiction": "US-DE", "completed": True},
        headers=h,
    )
    assert r.status_code == 404, r.text


def test_an_unknown_step_key_is_404(client, db):
    _u, _s, h = _member(db)
    r = client.patch(
        f"{BASE}/formation/steps/not_a_real_step",
        json={"jurisdiction": "NG", "completed": True},
        headers=h,
    )
    assert r.status_code == 404


def test_progress_is_per_workspace(client, db):
    _u, _s, h = _member(db)
    _other_u, _other_s, other_h = _member(db)
    client.patch(
        f"{BASE}/formation/steps/reserve_name",
        json={"jurisdiction": "NG", "completed": True},
        headers=h,
    )
    theirs = client.get(f"{BASE}/formation/checklist?jurisdiction=NG", headers=other_h)
    assert all(s["completed"] is False for s in theirs.json()["data"]["steps"])


# --- RBAC -------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", READER_ROLES)
def test_every_reader_role_can_read_formation(client, db, role):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    startup = create_startup(db, owner=owner, stage=StartupStage.validation)
    _u, _s, h = _member(db, role=role, startup=startup)
    assert client.get(f"{BASE}/formation/jurisdictions", headers=h).status_code == 200
    assert client.get(f"{BASE}/formation/checklist?jurisdiction=NG", headers=h).status_code == 200


def test_a_legal_advisor_cannot_tick_a_step(client, db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    startup = create_startup(db, owner=owner, stage=StartupStage.validation)
    _u, _s, h = _member(db, role=MembershipRole.legal_advisor, startup=startup)
    # Read-only on all three Slice 1 surfaces (spec D1). Commenting is Slice 2.
    r = client.patch(
        f"{BASE}/formation/steps/reserve_name",
        json={"jurisdiction": "NG", "completed": True},
        headers=h,
    )
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_forbidden_roles_cannot_touch_formation(client, db, role):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    startup = create_startup(db, owner=owner, stage=StartupStage.validation)
    _u, _s, h = _member(db, role=role, startup=startup)
    assert client.get(f"{BASE}/formation/jurisdictions", headers=h).status_code == 403
    assert client.get(f"{BASE}/formation/checklist?jurisdiction=NG", headers=h).status_code == 403
    r = client.patch(
        f"{BASE}/formation/steps/reserve_name",
        json={"jurisdiction": "NG", "completed": True},
        headers=h,
    )
    assert r.status_code == 403


def test_unauthenticated_is_rejected(client, db):
    _u, s, _h = _member(db)
    r = client.get(f"{BASE}/formation/jurisdictions", headers={"X-Workspace-Id": str(s.id)})
    assert r.status_code in (401, 403)

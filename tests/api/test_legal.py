from datetime import UTC, datetime, timedelta

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
    resp = client.get(f"{BASE}/formation/checklist?jurisdiction=KE", headers=h)
    assert resp.status_code == 404, resp.text


def test_checklist_requires_the_jurisdiction_param(client, db):
    _u, _s, h = _member(db)
    resp = client.get(f"{BASE}/formation/checklist", headers=h)
    assert resp.status_code == 422, resp.text


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
    resp = client.patch(f"{BASE}/formation/steps/reserve_name", json=body, headers=h)
    assert resp.status_code == 200, resp.text
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


# --- ip assets ---------------------------------------------------------------------------------


def _ip_body(**kw):
    body = {
        "type": "trademark",
        "name": "Kolo (wordmark)",
        "jurisdiction": "Nigeria",
        "status": "filed",
    }
    body.update(kw)
    return body


def test_ip_asset_crud_roundtrip(client, db):
    _u, _s, h = _member(db)
    created = client.post(f"{BASE}/ip-assets", json=_ip_body(renewal_on="2027-02-01"), headers=h)
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    aid = data["id"]
    # ISO date on the wire, never the mock's "Renews Feb 2027".
    assert data["renewal_on"] == "2027-02-01"
    assert data["status"] == "filed" and data["derived_status"] == "filed"
    assert data["type"] == "trademark"  # lowercase, not the mock's "Trademark"

    listed = client.get(f"{BASE}/ip-assets", headers=h)
    assert listed.status_code == 200
    assert any(a["id"] == aid for a in listed.json()["data"]["ip_assets"])

    one = client.get(f"{BASE}/ip-assets/{aid}", headers=h)
    assert one.status_code == 200 and one.json()["data"]["name"] == "Kolo (wordmark)"

    patched = client.patch(f"{BASE}/ip-assets/{aid}", json={"status": "registered"}, headers=h)
    assert patched.status_code == 200
    assert patched.json()["data"]["status"] == "registered"

    cleared = client.patch(f"{BASE}/ip-assets/{aid}", json={"renewal_on": None}, headers=h)
    assert cleared.json()["data"]["renewal_on"] is None

    deleted = client.delete(f"{BASE}/ip-assets/{aid}", headers=h)
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"deleted": True}
    resp = client.get(f"{BASE}/ip-assets/{aid}", headers=h)
    assert resp.status_code == 404, resp.text


def test_a_past_renewal_reads_as_expired(client, db):
    _u, _s, h = _member(db)
    created = client.post(
        f"{BASE}/ip-assets",
        json=_ip_body(status="registered", renewal_on="2020-01-01"),
        headers=h,
    )
    data = created.json()["data"]
    # `expired` is derived, never stored: the raw status is untouched (spec D9).
    assert data["status"] == "registered"
    assert data["derived_status"] == "expired"


def test_an_empty_register_lists_nothing(client, db):
    _u, _s, h = _member(db)
    r = client.get(f"{BASE}/ip-assets", headers=h)
    assert r.status_code == 200 and r.json()["data"]["ip_assets"] == []


def test_ip_list_filters(client, db):
    _u, _s, h = _member(db)
    client.post(f"{BASE}/ip-assets", json=_ip_body(name="A mark"), headers=h)
    client.post(
        f"{BASE}/ip-assets",
        json=_ip_body(name="A domain", type="domain", status="registered"),
        headers=h,
    )
    by_type = client.get(f"{BASE}/ip-assets?type=domain", headers=h)
    assert [a["name"] for a in by_type.json()["data"]["ip_assets"]] == ["A domain"]
    by_status = client.get(f"{BASE}/ip-assets?status=registered", headers=h)
    assert [a["name"] for a in by_status.json()["data"]["ip_assets"]] == ["A domain"]


@pytest.mark.parametrize(
    "body",
    [
        {"status": "expired"},  # not a stored value (spec D9)
        {"type": "slogan"},  # not one of the four
        {"name": ""},
        {"jurisdiction": ""},
        {"renewal_on": "not-a-date"},
    ],
)
def test_bad_ip_bodies_are_422(client, db, body):
    _u, _s, h = _member(db)
    r = client.post(f"{BASE}/ip-assets", json=_ip_body(**body), headers=h)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_ip_cross_tenant_ids_are_404(client, db):
    _u, _s, h = _member(db)
    _other_u, _other_s, other_h = _member(db)
    theirs = client.post(f"{BASE}/ip-assets", json=_ip_body(), headers=other_h).json()["data"]["id"]
    resp = client.get(f"{BASE}/ip-assets/{theirs}", headers=h)
    assert resp.status_code == 404, resp.text
    resp = client.patch(f"{BASE}/ip-assets/{theirs}", json={"name": "X"}, headers=h)
    assert resp.status_code == 404, resp.text
    resp = client.delete(f"{BASE}/ip-assets/{theirs}", headers=h)
    assert resp.status_code == 404, resp.text


def test_a_legal_advisor_reads_but_cannot_write_ip(client, db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    startup = create_startup(db, owner=owner, stage=StartupStage.validation)
    create_membership(db, owner, startup, role=MembershipRole.founder)
    db.flush()
    _u, _s, advisor_h = _member(db, role=MembershipRole.legal_advisor, startup=startup)

    resp = client.get(f"{BASE}/ip-assets", headers=advisor_h)
    assert resp.status_code == 200, resp.text
    resp = client.post(f"{BASE}/ip-assets", json=_ip_body(), headers=advisor_h)
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_forbidden_roles_cannot_touch_ip(client, db, role):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    startup = create_startup(db, owner=owner, stage=StartupStage.validation)
    _u, _s, h = _member(db, role=role, startup=startup)
    resp = client.get(f"{BASE}/ip-assets", headers=h)
    assert resp.status_code == 403, resp.text
    resp = client.post(f"{BASE}/ip-assets", json=_ip_body(), headers=h)
    assert resp.status_code == 403, resp.text


# --- compliance items --------------------------------------------------------------------------


def _comp_body(**kw):
    body = {"title": "VAT filing", "authority": "FIRS", "due_on": "2026-11-30"}
    body.update(kw)
    return body


def _in_days(n):
    return (datetime.now(UTC).date() + timedelta(days=n)).isoformat()


def test_compliance_crud_roundtrip(client, db):
    _u, _s, h = _member(db)
    created = client.post(f"{BASE}/compliance-items", json=_comp_body(), headers=h)
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    cid = data["id"]
    assert data["due_on"] == "2026-11-30"  # ISO, never "in 9 days"
    assert data["status"] == "open"
    assert data["recurrence"] == "none"
    assert data["done_at"] is None and data["proof_url"] is None

    listed = client.get(f"{BASE}/compliance-items", headers=h)
    assert listed.status_code == 200
    assert any(i["id"] == cid for i in listed.json()["data"]["compliance_items"])

    one = client.get(f"{BASE}/compliance-items/{cid}", headers=h)
    assert one.status_code == 200 and one.json()["data"]["title"] == "VAT filing"

    patched = client.patch(
        f"{BASE}/compliance-items/{cid}", json={"title": "VAT return"}, headers=h
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["title"] == "VAT return"

    cleared = client.patch(f"{BASE}/compliance-items/{cid}", json={"authority": None}, headers=h)
    assert cleared.json()["data"]["authority"] is None

    deleted = client.delete(f"{BASE}/compliance-items/{cid}", headers=h)
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"deleted": True}
    resp = client.get(f"{BASE}/compliance-items/{cid}", headers=h)
    assert resp.status_code == 404, resp.text


def test_the_four_derived_badges(client, db):
    _u, _s, h = _member(db)
    client.post(
        f"{BASE}/compliance-items", json=_comp_body(title="Late", due_on=_in_days(-1)), headers=h
    )
    client.post(
        f"{BASE}/compliance-items", json=_comp_body(title="Today", due_on=_in_days(0)), headers=h
    )
    client.post(
        f"{BASE}/compliance-items", json=_comp_body(title="Soon", due_on=_in_days(14)), headers=h
    )
    client.post(
        f"{BASE}/compliance-items", json=_comp_body(title="Later", due_on=_in_days(15)), headers=h
    )

    rows = client.get(f"{BASE}/compliance-items", headers=h).json()["data"]["compliance_items"]
    by_title = {i["title"]: i["derived_status"] for i in rows}
    assert by_title["Late"] == "overdue"
    assert by_title["Today"] == "due_soon"  # today has not passed
    assert by_title["Soon"] == "due_soon"  # 14 days is inside the window
    assert by_title["Later"] == "upcoming"  # 15 is outside it


def test_marking_done_wins_over_overdue(client, db):
    _u, _s, h = _member(db)
    cid = client.post(
        f"{BASE}/compliance-items", json=_comp_body(due_on=_in_days(-30)), headers=h
    ).json()["data"]["id"]
    done = client.patch(f"{BASE}/compliance-items/{cid}", json={"status": "done"}, headers=h)
    assert done.status_code == 200, done.text
    assert done.json()["data"]["derived_status"] == "done"
    assert done.json()["data"]["done_at"] is not None


def test_an_empty_calendar_lists_nothing(client, db):
    _u, _s, h = _member(db)
    r = client.get(f"{BASE}/compliance-items", headers=h)
    assert r.status_code == 200 and r.json()["data"]["compliance_items"] == []


def test_compliance_range_and_status_filters(client, db):
    _u, _s, h = _member(db)
    client.post(
        f"{BASE}/compliance-items", json=_comp_body(title="June", due_on="2026-06-30"), headers=h
    )
    client.post(
        f"{BASE}/compliance-items", json=_comp_body(title="July 1", due_on="2026-07-01"), headers=h
    )
    client.post(
        f"{BASE}/compliance-items", json=_comp_body(title="July 31", due_on="2026-07-31"), headers=h
    )

    ranged = client.get(
        f"{BASE}/compliance-items?date_from=2026-07-01&date_to=2026-07-31", headers=h
    )
    assert [i["title"] for i in ranged.json()["data"]["compliance_items"]] == ["July 1", "July 31"]

    only_open = client.get(f"{BASE}/compliance-items?status=open", headers=h)
    assert len(only_open.json()["data"]["compliance_items"]) == 3


@pytest.mark.parametrize(
    "body",
    [
        {"title": ""},
        {"title": "   "},
        {"due_on": "not-a-date"},
        {"recurrence": "fortnightly"},
        {"authority": "a" * 121},
    ],
)
def test_bad_compliance_bodies_are_422(client, db, body):
    _u, _s, h = _member(db)
    r = client.post(f"{BASE}/compliance-items", json=_comp_body(**body), headers=h)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_due_on_is_required_on_create(client, db):
    _u, _s, h = _member(db)
    r = client.post(f"{BASE}/compliance-items", json={"title": "VAT"}, headers=h)
    assert r.status_code == 422


def test_an_explicit_null_on_a_required_compliance_field_is_422(client, db):
    _u, _s, h = _member(db)
    cid = client.post(f"{BASE}/compliance-items", json=_comp_body(), headers=h).json()["data"]["id"]
    r = client.patch(f"{BASE}/compliance-items/{cid}", json={"title": None}, headers=h)
    assert r.status_code == 422


def test_anchor_day_cannot_be_set_by_the_client(client, db):
    _u, _s, h = _member(db)
    # Extra keys are ignored by the schema, so the anchor stays derived from due_on (spec D24).
    created = client.post(
        f"{BASE}/compliance-items",
        json=_comp_body(due_on="2026-01-31") | {"anchor_day": 1},
        headers=h,
    )
    assert created.status_code == 200, created.text
    assert "anchor_day" not in created.json()["data"]


def test_compliance_cross_tenant_ids_are_404(client, db):
    _u, _s, h = _member(db)
    _other_u, _other_s, other_h = _member(db)
    theirs = client.post(f"{BASE}/compliance-items", json=_comp_body(), headers=other_h).json()[
        "data"
    ]["id"]
    resp = client.get(f"{BASE}/compliance-items/{theirs}", headers=h)
    assert resp.status_code == 404, resp.text
    resp = client.patch(f"{BASE}/compliance-items/{theirs}", json={"title": "X"}, headers=h)
    assert resp.status_code == 404, resp.text
    resp = client.delete(f"{BASE}/compliance-items/{theirs}", headers=h)
    assert resp.status_code == 404, resp.text


def test_a_legal_advisor_reads_but_cannot_write_compliance(client, db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    startup = create_startup(db, owner=owner, stage=StartupStage.validation)
    _u, _s, advisor_h = _member(db, role=MembershipRole.legal_advisor, startup=startup)
    resp = client.get(f"{BASE}/compliance-items", headers=advisor_h)
    assert resp.status_code == 200, resp.text
    resp = client.post(f"{BASE}/compliance-items", json=_comp_body(), headers=advisor_h)
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_forbidden_roles_cannot_touch_compliance(client, db, role):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    startup = create_startup(db, owner=owner, stage=StartupStage.validation)
    _u, _s, h = _member(db, role=role, startup=startup)
    resp = client.get(f"{BASE}/compliance-items", headers=h)
    assert resp.status_code == 403, resp.text
    resp = client.post(f"{BASE}/compliance-items", json=_comp_body(), headers=h)
    assert resp.status_code == 403, resp.text


# --- RBAC -------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", READER_ROLES)
def test_every_reader_role_can_read_formation(client, db, role):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    startup = create_startup(db, owner=owner, stage=StartupStage.validation)
    _u, _s, h = _member(db, role=role, startup=startup)
    resp = client.get(f"{BASE}/formation/jurisdictions", headers=h)
    assert resp.status_code == 200, resp.text
    resp = client.get(f"{BASE}/formation/checklist?jurisdiction=NG", headers=h)
    assert resp.status_code == 200, resp.text


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
    resp = client.get(f"{BASE}/formation/jurisdictions", headers=h)
    assert resp.status_code == 403, resp.text
    resp = client.get(f"{BASE}/formation/checklist?jurisdiction=NG", headers=h)
    assert resp.status_code == 403, resp.text
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

"""Marketplace slice B: expert listings, availability, staff listing review and the founder-facing
directory (visibility, filters, sort, slots, reports)."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.redis import get_redis
from app.db.models.enums import (
    ListingStatus,
    ListingType,
    MembershipRole,
    ProviderStatus,
    StaffRole,
)
from app.db.models.marketplace import MarketplaceListing, MarketplaceProvider
from app.platform.email import ConsoleEmailSender
from app.services.marketplace import emails as mp_emails
from tests.api.test_marketplace_providers import approved, audit_actions
from tests.api.test_staff_users import hdr, member, mk_staff, mk_user, mk_ws

P = "/api/v1/provider"
A = "/api/v1/admin/marketplace"
M = "/api/v1/marketplace"
REASON = "Checked the listing against the rules"
BIO = "Fifteen years advising founders on contracts, shareholder agreements and fundraising."
DESC = "A focused one hour session reviewing your contract or term sheet, with written notes after."
MEET = "https://meet.example.com/ngozi"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.test")
    r = get_redis()
    for pat in ("staff_stepup_*", "report_rl:*"):
        for k in r.scan_iter(pat):
            r.delete(k)


@pytest.fixture()
def mail(monkeypatch):
    sender = ConsoleEmailSender()
    monkeypatch.setattr(mp_emails, "get_email_sender", lambda: sender)
    return sender


@pytest.fixture()
def moderator(db):
    return mk_staff(db, StaffRole.admin, ("moderation",))


def staff_post(client, actor, path, reason=REASON, step_up=True, **extra):
    import pyotp

    r = get_redis()
    for k in r.scan_iter("staff_stepup_used:*"):
        r.delete(k)
    body = {"reason": reason, **extra}
    if step_up:
        body["totp_code"] = pyotp.TOTP(actor.test_secret).now()
    return client.post(f"{A}{path}", json=body, headers=hdr(actor))


def fh(user, ws):
    return {**hdr(user), "X-Workspace-Id": str(ws.id)}


def founder(db, role=MembershipRole.founder):
    owner = mk_user(db)
    ws = mk_ws(db, owner, plan="growth")
    if role == MembershipRole.founder:
        return owner, ws
    other = mk_user(db)
    member(db, ws, other, role)
    return other, ws


def mk_provider(
    db,
    *,
    name="Tolu Ade",
    category="Design",
    status=ProviderStatus.approved,
    languages=("English",),
    tz="Africa/Lagos",
    rating=(0, 0),
    user=None,
    avatar=None,
):
    user = user or mk_user(db, name=name)
    row = MarketplaceProvider(
        user_id=user.id,
        email=user.email,
        status=status,
        source="invite",
        display_name=name,
        headline=f"{category} specialist",
        bio=BIO,
        category=category,
        categories=[],
        languages=list(languages),
        timezone=tz,
        terms_accepted_at=datetime.now(UTC),
        vetted_at=datetime.now(UTC),
        vetting_json={"checks": {"id": {"done": True}, "references": {"done": True, "count": 1}}},
        rating_avg=rating[0],
        rating_count=rating[1],
    )
    db.add(row)
    db.flush()
    return user, row


def mk_listing(db, prov, *, status=ListingStatus.active, type_="consultation", **kw):
    fields = {
        "title": "Contract review",
        "summary": "A one hour review of your contract with written notes.",
        "description": DESC,
        "category": prov.category,
        "price_minor": 1_000_000,
        "tags": [],
        "deliverables_json": [],
        "meeting_url": MEET,
        "prescreen_json": {},
    }
    if type_ == "consultation":
        fields["duration_min"] = 60
    else:
        fields.update(delivery_days=5, deliverables_json=["A brand kit"])
    fields.update(kw)
    row = MarketplaceListing(
        provider_id=prov.id,
        type=ListingType(type_),
        status=status,
        published_at=datetime.now(UTC) if status == ListingStatus.active else None,
        **fields,
    )
    db.add(row)
    db.flush()
    return row


def open_week(client, user):
    """Every weekday 08:00-20:00 Lagos, no notice: slots exist within the week."""
    body = {
        "min_notice_h": 0,
        "buffer_min": 0,
        "rules": [
            {"weekday": d, "start": "08:00", "end": "20:00", "slot_min": 60} for d in range(7)
        ],
        "exceptions": [],
    }
    r = client.put(f"{P}/availability", json=body, headers=hdr(user))
    assert r.status_code == 200, r.text


def create_body(**over):
    return {
        "type": "consultation",
        "title": "Contract review",
        "summary": "A one hour review of your contract with written notes.",
        "description": DESC,
        "category": "Legal",
        "price_minor": 1_000_000,
        "duration_min": 60,
        "meeting_url": MEET,
        **over,
    }


def new_listing(client, user, **over):
    r = client.post(f"{P}/listings", json=create_body(**over), headers=hdr(user))
    assert r.status_code == 201, r.text
    return r.json()["data"]


def live_listing(client, db, moderator, user, **over):
    row = new_listing(client, user, **over)
    resp1 = client.post(f"{P}/listings/{row['id']}/submit", headers=hdr(user))
    assert resp1.status_code == 200
    r = staff_post(client, moderator, f"/listings/{row['id']}/approve", step_up=False)
    assert r.status_code == 200, r.text
    return row["id"]


# ---------------------------------------------------------------- guards: provider routes

PROVIDER_ROUTES = [
    ("get", "/listings"),
    ("post", "/listings"),
    ("get", f"/listings/{uuid.uuid4()}"),
    ("patch", f"/listings/{uuid.uuid4()}"),
    ("delete", f"/listings/{uuid.uuid4()}"),
    ("post", f"/listings/{uuid.uuid4()}/submit"),
    ("post", f"/listings/{uuid.uuid4()}/pause"),
    ("post", f"/listings/{uuid.uuid4()}/resume"),
    ("get", "/availability"),
    ("put", "/availability"),
]


def test_listing_routes_need_sign_in_a_provider_and_approval(client, db):
    for method, path in PROVIDER_ROUTES:
        assert getattr(client, method)(f"{P}{path}").status_code == 401, path
    plain = mk_user(db)
    for method, path in PROVIDER_ROUTES:
        r = getattr(client, method)(f"{P}{path}", headers=hdr(plain))
        assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_A_PROVIDER", path
    for st in (ProviderStatus.in_review, ProviderStatus.suspended, ProviderStatus.invited):
        u, _ = mk_provider(db, status=st)
        for method, path in PROVIDER_ROUTES:
            r = getattr(client, method)(f"{P}{path}", headers=hdr(u))
            assert r.status_code == 403, (st, path)
            assert r.json()["error"]["code"] == "PROVIDER_NOT_APPROVED", (st, path)


def test_one_expert_cannot_touch_anothers_listing(client, db):
    a, pa = mk_provider(db, name="A One")
    b, _ = mk_provider(db, name="B Two")
    row = mk_listing(db, pa, status=ListingStatus.draft)
    for method, path in (
        ("get", ""),
        ("patch", ""),
        ("delete", ""),
        ("post", "/submit"),
        ("post", "/pause"),
    ):
        r = getattr(client, method)(
            f"{P}/listings/{row.id}{path}",
            headers=hdr(b),
            **({"json": {"title": "x" * 5}} if method == "patch" else {}),
        )
        assert r.status_code == 404, (method, path)
    assert client.get(f"{P}/listings", headers=hdr(b)).json()["data"]["listings"] == []
    assert len(client.get(f"{P}/listings", headers=hdr(a)).json()["data"]["listings"]) == 1


# ---------------------------------------------------------------- create, validate, earnings


def test_create_is_a_draft_with_an_earnings_preview(client, db, moderator, mail):
    user, _ = approved(client, db, moderator, mail)
    d = new_listing(client, user)
    assert d["status"] == "draft" and d["currency"] == "NGN" and d["type"] == "consultation"
    assert d["earnings_preview"] == {
        "commission_bps": 1500,
        "commission_minor": 150_000,
        "net_minor": 850_000,
    }
    assert "prescreen" not in json.dumps(d)  # staff-only


def test_commission_rounds_half_up_and_always_adds_up():
    from app.services.marketplace.listings import commission_for

    class P:
        commission_bps = None

    for price in (1, 2, 3, 100_001, 333_333, 999_999, 1_000_000, 123_456_789):
        c = commission_for(P(), price)  # type: ignore[arg-type]
        assert c["commission_minor"] + c["net_minor"] == price
        assert c["commission_minor"] == (price * 1500 + 5000) // 10000
    P.commission_bps = 1000
    assert commission_for(P(), 100_001)["commission_minor"] == 10_000  # override respected


def test_commission_override_is_used_in_the_preview(client, db, moderator, mail):
    user, pid = approved(client, db, moderator, mail)
    db.get(MarketplaceProvider, uuid.UUID(pid)).commission_bps = 1000
    db.flush()
    d = new_listing(client, user)
    assert d["earnings_preview"]["commission_minor"] == 100_000


@pytest.mark.parametrize(
    "over",
    [
        {"price_minor": 0},
        {"price_minor": -5},
        {"price_minor": 10_000_000_001},
        {"price_minor": 1.5},
        {"price_minor": "5000"},
        {"category": "Cooking"},
        {"type": "bundle"},
        {"duration_min": 20},
        {"duration_min": 0},
        {"delivery_days": 0},
        {"deliverables": ["x"] * 11},
        {"tags": ["t"] * 9},
        {"meeting_url": "javascript:alert(1)"},
        {"title": "ab"},
        {"revisions_included": 9},
        {"status": "active"},
        {"provider_id": str(uuid.uuid4())},
    ],
)
def test_create_validation(client, db, moderator, mail, over):
    user, _ = approved(client, db, moderator, mail)
    r = client.post(f"{P}/listings", json=create_body(**over), headers=hdr(user))
    assert r.status_code == 422, (over, r.text)


def test_regulated_categories_need_the_vetted_main_category(client, db, moderator, mail):
    legal, _ = approved(client, db, moderator, mail)
    r = client.post(f"{P}/listings", json=create_body(category="Design"), headers=hdr(legal))
    assert r.status_code == 422 and r.json()["error"]["code"] == "CATEGORY_NOT_ALLOWED"
    designer, _ = mk_provider(db, category="Design")
    r = client.post(f"{P}/listings", json=create_body(category="Legal"), headers=hdr(designer))
    assert r.status_code == 422 and r.json()["error"]["code"] == "CATEGORY_NOT_ALLOWED"
    r = client.post(f"{P}/listings", json=create_body(category="Accounting"), headers=hdr(designer))
    assert r.status_code == 422
    r = client.post(f"{P}/listings", json=create_body(category="Design"), headers=hdr(designer))
    assert r.status_code == 201
    # a non-regulated secondary category is fine
    row = db.query(MarketplaceProvider).filter_by(user_id=designer.id).one()
    row.categories = ["Marketing"]
    db.flush()
    r = client.post(f"{P}/listings", json=create_body(category="Marketing"), headers=hdr(designer))
    assert r.status_code == 201


def test_listing_limit(client, db, monkeypatch):
    monkeypatch.setattr("app.services.marketplace.listings.MAX_LISTINGS_PER_PROVIDER", 2)
    u, _ = mk_provider(db)
    for _i in range(2):
        assert (
            client.post(
                f"{P}/listings", json=create_body(category="Design"), headers=hdr(u)
            ).status_code
            == 201
        )
    r = client.post(f"{P}/listings", json=create_body(category="Design"), headers=hdr(u))
    assert r.status_code == 409 and r.json()["error"]["code"] == "LISTING_LIMIT_REACHED"


def test_patch_rules_on_a_draft(client, db, moderator, mail):
    user, _ = approved(client, db, moderator, mail)
    d = new_listing(client, user)
    h = hdr(user)
    resp2 = client.patch(f"{P}/listings/{d['id']}", json={}, headers=h)
    assert resp2.status_code == 422
    for bad in (
        {"title": None},
        {"price_minor": None},
        {"category": None},
        {"revisions_included": None},
    ):
        resp3 = client.patch(f"{P}/listings/{d['id']}", json=bad, headers=h)
        assert resp3.status_code == 422, bad
    r = client.patch(
        f"{P}/listings/{d['id']}",
        json={"tags": None, "deliverables": ["A memo"], "price_minor": 2_000_000},
        headers=h,
    )
    assert r.status_code == 200 and r.json()["data"]["tags"] == []
    assert r.json()["data"]["price_minor"] == 2_000_000 and r.json()["data"]["status"] == "draft"
    assert (
        client.patch(f"{P}/listings/{d['id']}", json={"type": "package"}, headers=h).status_code
        == 422
    )


# ---------------------------------------------------------------- submit and review


def test_submit_lists_what_is_missing(client, db, moderator, mail):
    user, _ = approved(client, db, moderator, mail)
    d = new_listing(
        client, user, summary="short", description=None, meeting_url=None, duration_min=None
    )
    r = client.post(f"{P}/listings/{d['id']}/submit", headers=hdr(user))
    assert r.status_code == 422 and r.json()["error"]["code"] == "SUBMISSION_INCOMPLETE"
    assert {e["field"] for e in r.json()["error"]["field_errors"]} == {
        "summary",
        "description",
        "duration_min",
        "meeting_url",
    }
    pkg = new_listing(client, user, type="package", duration_min=None, meeting_url=None)
    r = client.post(f"{P}/listings/{pkg['id']}/submit", headers=hdr(user))
    assert {e["field"] for e in r.json()["error"]["field_errors"]} == {
        "delivery_days",
        "deliverables",
    }


def test_submit_locks_the_listing_and_shows_in_the_queue(client, db, moderator, mail):
    user, _ = approved(client, db, moderator, mail)
    d = new_listing(client, user)
    h, mh = hdr(user), hdr(moderator)
    r = client.post(f"{P}/listings/{d['id']}/submit", headers=h)
    assert r.status_code == 200 and r.json()["data"]["status"] == "pending_review"
    lock = client.patch(f"{P}/listings/{d['id']}", json={"title": "New title here"}, headers=h)
    assert lock.status_code == 409 and lock.json()["error"]["code"] == "LISTING_LOCKED"
    resp4 = client.post(f"{P}/listings/{d['id']}/submit", headers=h)
    assert resp4.status_code == 409
    q = client.get(f"{A}/listings?queue=true", headers=mh).json()["data"]["listings"]
    assert [x["id"] for x in q] == [d["id"]]
    assert (
        client.get(f"{A}/listings/counts", headers=mh).json()["data"]["by_status"]["pending_review"]
        == 1
    )


def test_approve_makes_it_live_and_emails_the_expert(client, db, moderator, mail):
    user, _ = approved(client, db, moderator, mail)
    d = new_listing(client, user)
    client.post(f"{P}/listings/{d['id']}/submit", headers=hdr(user))
    mail.sent.clear()
    short = client.post(
        f"{A}/listings/{d['id']}/approve", json={"reason": "ok"}, headers=hdr(moderator)
    )
    assert short.status_code == 422
    r = staff_post(client, moderator, f"/listings/{d['id']}/approve", step_up=False)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "active" and r.json()["data"]["published_at"]
    assert "live" in mail.sent[0].subject.lower() and mail.sent[0].to == user.email
    assert "marketplace.listing.approved" in audit_actions(db, d["id"])
    again = staff_post(client, moderator, f"/listings/{d['id']}/approve", step_up=False)
    assert again.status_code == 409 and again.json()["error"]["code"] == "INVALID_STATE"


def test_reject_and_request_changes_send_it_back(client, db, moderator, mail):
    user, _ = approved(client, db, moderator, mail)
    h = hdr(user)
    a = new_listing(client, user)
    client.post(f"{P}/listings/{a['id']}/submit", headers=h)
    bad = staff_post(
        client, moderator, f"/listings/{a['id']}/reject", step_up=False, template="nope"
    )
    assert bad.status_code == 422
    r = staff_post(
        client, moderator, f"/listings/{a['id']}/reject", step_up=False, template="pricing"
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "rejected"
    mine = client.get(f"{P}/listings/{a['id']}", headers=h).json()["data"]
    assert mine["review_message"] == "The price doesn't match the scope"
    assert (
        client.patch(
            f"{P}/listings/{a['id']}", json={"price_minor": 900_000}, headers=h
        ).status_code
        == 200
    )
    resp5 = client.post(f"{P}/listings/{a['id']}/submit", headers=h)
    assert resp5.status_code == 200  # resubmit

    b = new_listing(client, user, title="Second listing")
    client.post(f"{P}/listings/{b['id']}/submit", headers=h)
    msg = "Please say what the written notes will contain."
    r = client.post(
        f"{A}/listings/{b['id']}/request-changes", json={"message": msg}, headers=hdr(moderator)
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "draft"
    assert client.get(f"{P}/listings/{b['id']}", headers=h).json()["data"]["review_message"] == msg
    short = client.post(
        f"{A}/listings/{b['id']}/request-changes", json={"message": "no"}, headers=hdr(moderator)
    )
    assert short.status_code == 422


def test_approving_for_an_unapproved_provider_is_refused(client, db, moderator):
    u, prov = mk_provider(db, status=ProviderStatus.suspended)
    row = mk_listing(db, prov, status=ListingStatus.pending_review)
    r = staff_post(client, moderator, f"/listings/{row.id}/approve", step_up=False)
    assert r.status_code == 409 and r.json()["error"]["code"] == "PROVIDER_NOT_APPROVED"


def test_prescreen_chips_never_block_but_show_to_staff_only(client, db, moderator, mail):
    user, _ = approved(client, db, moderator, mail)
    d = new_listing(
        client,
        user,
        description=DESC + " Call +234 803 123 4567 for guaranteed approval of your visa.",
        price_minor=5000,
    )
    assert "flags" not in json.dumps(d)
    client.post(f"{P}/listings/{d['id']}/submit", headers=hdr(user))
    detail = client.get(f"{A}/listings/{d['id']}", headers=hdr(moderator)).json()["data"]
    rules = {f["rule"] for f in detail["prescreen_flags"]}
    assert {"contact_details", "guaranteed_outcome", "price_out_of_range"} <= rules


# ---------------------------------------------------------------- live edits go to review (D-MP-L8)


def test_live_edits_split_into_immediate_and_reviewed(client, db, moderator, mail):
    user, _ = approved(client, db, moderator, mail)
    lid = live_listing(client, db, moderator, user)
    h, mh = hdr(user), hdr(moderator)
    r = client.patch(
        f"{P}/listings/{lid}",
        json={
            "price_minor": 1_200_000,
            "title": "Contract and term sheet review",
            "tags": ["contracts"],
        },
        headers=h,
    )
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["status"] == "active"
    assert d["price_minor"] == 1_200_000 and d["tags"] == ["contracts"]  # applied at once
    assert d["title"] == "Contract review"  # the live title is unchanged
    assert d["pending_revision"] == {"title": "Contract and term sheet review"}
    # staff see it in the queue with a diff
    q = client.get(f"{A}/listings?queue=true", headers=mh).json()["data"]["listings"]
    assert [x["id"] for x in q] == [lid] and q[0]["has_revision"] is True
    detail = client.get(f"{A}/listings/{lid}", headers=mh).json()["data"]
    assert detail["diff"] == [
        {"field": "title", "from": "Contract review", "to": "Contract and term sheet review"}
    ]
    assert "marketplace.listing.revision_submitted" in audit_actions(db, lid)


def test_a_live_edit_that_restores_the_live_value_is_not_a_change(client, db, moderator, mail):
    user, _ = approved(client, db, moderator, mail)
    lid = live_listing(client, db, moderator, user)
    h = hdr(user)
    client.patch(f"{P}/listings/{lid}", json={"title": "Something else entirely"}, headers=h)
    r = client.patch(f"{P}/listings/{lid}", json={"title": "Contract review"}, headers=h)
    assert r.json()["data"]["pending_revision"] is None
    assert (
        client.get(f"{A}/listings?queue=true", headers=hdr(moderator)).json()["data"]["listings"]
        == []
    )


def test_approving_a_revision_applies_it(client, db, moderator, mail):
    user, _ = approved(client, db, moderator, mail)
    lid = live_listing(client, db, moderator, user)
    client.patch(
        f"{P}/listings/{lid}",
        json={"title": "Better title here", "deliverables": ["A memo"]},
        headers=hdr(user),
    )
    mail.sent.clear()
    r = staff_post(client, moderator, f"/listings/{lid}/approve", step_up=False)
    d = r.json()["data"]
    assert d["status"] == "active" and d["title"] == "Better title here"
    assert d["deliverables"] == ["A memo"] and d["pending_revision"] is None and d["diff"] == []
    assert "edit" in mail.sent[0].subject.lower()
    assert (
        staff_post(client, moderator, f"/listings/{lid}/approve", step_up=False).status_code == 409
    )


def test_rejecting_or_sending_back_a_revision_keeps_the_live_version(client, db, moderator, mail):
    user, _ = approved(client, db, moderator, mail)
    lid = live_listing(client, db, moderator, user)
    h = hdr(user)
    client.patch(f"{P}/listings/{lid}", json={"title": "Bad new title"}, headers=h)
    r = staff_post(client, moderator, f"/listings/{lid}/reject", step_up=False, template="other")
    d = r.json()["data"]
    assert (
        d["status"] == "active"
        and d["title"] == "Contract review"
        and d["pending_revision"] is None
    )
    mine = client.get(f"{P}/listings/{lid}", headers=h).json()["data"]
    assert mine["status"] == "active" and mine["review_message"]
    client.patch(f"{P}/listings/{lid}", json={"title": "Another try at it"}, headers=h)
    r = client.post(
        f"{A}/listings/{lid}/request-changes",
        json={"message": "Please be more specific."},
        headers=hdr(moderator),
    )
    assert r.json()["data"]["status"] == "active" and r.json()["data"]["pending_revision"] is None


def test_founders_keep_seeing_the_live_version_while_an_edit_waits(client, db, moderator, mail):
    user, _ = approved(client, db, moderator, mail)
    lid = live_listing(client, db, moderator, user)
    client.patch(f"{P}/listings/{lid}", json={"title": "Edited but unreviewed"}, headers=hdr(user))
    f, ws = founder(db)
    d = client.get(f"{M}/listings/{lid}", headers=fh(f, ws)).json()["data"]
    assert d["title"] == "Contract review"


def test_pause_resume_and_delete_rules(client, db, moderator, mail):
    user, _ = approved(client, db, moderator, mail)
    h = hdr(user)
    lid = live_listing(client, db, moderator, user)
    resp6 = client.post(f"{P}/listings/{lid}/resume", headers=h)
    assert resp6.status_code == 409
    r = client.post(f"{P}/listings/{lid}/pause", headers=h)
    assert r.status_code == 200 and r.json()["data"]["status"] == "paused"
    resp7 = client.post(f"{P}/listings/{lid}/pause", headers=h)
    assert resp7.status_code == 409
    # a paused listing is editable like a live one
    e = client.patch(f"{P}/listings/{lid}", json={"title": "Edited while paused"}, headers=h)
    assert e.status_code == 200 and e.json()["data"]["pending_revision"]
    resp8 = client.delete(f"{P}/listings/{lid}", headers=h)
    assert resp8.status_code == 409  # pause, don't delete
    resp9 = client.post(f"{P}/listings/{lid}/resume", headers=h)
    assert resp9.json()["data"]["status"] == "active"
    draft = new_listing(client, user, title="Throwaway draft")
    resp10 = client.delete(f"{P}/listings/{draft['id']}", headers=h)
    assert resp10.status_code == 200
    assert client.get(f"{P}/listings/{draft['id']}", headers=h).status_code == 404


def test_unlist_and_relist_need_a_code_and_hide_the_listing(client, db, moderator, mail):
    user, _ = approved(client, db, moderator, mail)
    lid = live_listing(client, db, moderator, user)
    f, ws = founder(db)
    mh = hdr(moderator)
    assert client.get(f"{M}/listings/{lid}", headers=fh(f, ws)).status_code == 200
    no_code = client.post(f"{A}/listings/{lid}/unlist", json={"reason": REASON}, headers=mh)
    assert no_code.status_code == 422
    r = staff_post(
        client, moderator, f"/listings/{lid}/unlist", reason="Contact details in the description"
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "unlisted"
    assert r.json()["data"]["unlisted_reason"] == "Contact details in the description"
    assert client.get(f"{M}/listings/{lid}", headers=fh(f, ws)).status_code == 404
    mine = client.get(f"{P}/listings/{lid}", headers=hdr(user)).json()["data"]
    assert mine["status"] == "unlisted" and mine["unlisted_reason"]
    locked = client.patch(
        f"{P}/listings/{lid}", json={"title": "Trying to edit"}, headers=hdr(user)
    )
    assert locked.status_code == 403 and locked.json()["error"]["code"] == "LISTING_UNLISTED"
    resp11 = client.post(f"{P}/listings/{lid}/resume", headers=hdr(user))
    assert resp11.status_code == 409
    back = staff_post(client, moderator, f"/listings/{lid}/relist")
    assert back.status_code == 200 and back.json()["data"]["status"] == "active"
    assert client.get(f"{M}/listings/{lid}", headers=fh(f, ws)).status_code == 200
    assert staff_post(client, moderator, f"/listings/{lid}/relist").status_code == 409


def test_unknown_listing_does_not_spend_the_authenticator_code(client, db, moderator):
    import pyotp

    code = pyotp.TOTP(moderator.test_secret).now()
    r = client.post(
        f"{A}/listings/{uuid.uuid4()}/unlist",
        json={"reason": REASON, "totp_code": code},
        headers=hdr(moderator),
    )
    assert r.status_code == 404 and r.json()["error"]["code"] == "LISTING_NOT_FOUND"
    assert not get_redis().exists(f"staff_stepup_used:{moderator.id}:{code}")


# ---------------------------------------------------------------- staff cannot act on their own


def test_staff_cannot_moderate_their_own_listings(client, db, moderator):
    _, prov = mk_provider(db, user=moderator)
    pending = mk_listing(db, prov, status=ListingStatus.pending_review)
    live = mk_listing(db, prov, title="Live one")
    unlisted = mk_listing(db, prov, status=ListingStatus.unlisted, title="Gone")
    mh = hdr(moderator)
    for path, extra, step in (
        (f"/listings/{pending.id}/approve", {}, False),
        (f"/listings/{pending.id}/reject", {"template": "other"}, False),
        (f"/listings/{live.id}/unlist", {}, True),
        (f"/listings/{unlisted.id}/relist", {}, True),
    ):
        r = staff_post(client, moderator, path, step_up=step, **extra)
        assert r.status_code == 422 and r.json()["error"]["code"] == "CANNOT_ACT_ON_SELF", path
    r = client.post(
        f"{A}/listings/{pending.id}/request-changes",
        json={"message": "Please change things."},
        headers=mh,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "CANNOT_ACT_ON_SELF"
    db.refresh(pending)
    assert pending.status == ListingStatus.pending_review


# ---------------------------------------------------------------- admin guard

ADMIN_ROUTES = [
    ("get", "/listings"),
    ("get", "/listings/counts"),
    ("get", f"/listings/{uuid.uuid4()}"),
    ("post", f"/listings/{uuid.uuid4()}/approve"),
    ("post", f"/listings/{uuid.uuid4()}/reject"),
    ("post", f"/listings/{uuid.uuid4()}/request-changes"),
    ("post", f"/listings/{uuid.uuid4()}/unlist"),
    ("post", f"/listings/{uuid.uuid4()}/relist"),
]


def test_admin_listing_routes_hide_from_non_staff_and_refuse_wrong_scope(client, db):
    f, ws = founder(db)
    support = mk_staff(db, StaffRole.admin, ("support",))
    for method, path in ADMIN_ROUTES:
        assert getattr(client, method)(f"{A}{path}").status_code == 404, path
        assert getattr(client, method)(f"{A}{path}", headers=hdr(f)).status_code == 404, path
        assert getattr(client, method)(f"{A}{path}", headers=hdr(support)).status_code == 403, path
    sa = mk_staff(db, StaffRole.super_admin)
    assert client.get(f"{A}/listings", headers=hdr(sa)).status_code == 200


def test_admin_queue_filters_and_pages(client, db, moderator):
    _, prov = mk_provider(db, name="Queue Person")
    for i in range(3):
        mk_listing(db, prov, status=ListingStatus.pending_review, title=f"Pending number {i}")
    mk_listing(db, prov, title="Already live")
    mh = hdr(moderator)
    all_ = client.get(f"{A}/listings", headers=mh).json()
    assert len(all_["data"]["listings"]) == 4
    one = client.get(f"{A}/listings?status=pending_review&limit=2", headers=mh).json()
    assert len(one["data"]["listings"]) == 2 and one["meta"]["next_cursor"]
    two = client.get(
        f"{A}/listings?status=pending_review&limit=2&cursor={one['meta']['next_cursor']}",
        headers=mh,
    ).json()
    ids = [x["id"] for x in one["data"]["listings"] + two["data"]["listings"]]
    assert len(ids) == 3 == len(set(ids))
    assert (
        len(client.get(f"{A}/listings?query=Queue Person", headers=mh).json()["data"]["listings"])
        == 4
    )
    assert client.get(f"{A}/listings?status=bogus", headers=mh).status_code == 422


# ---------------------------------------------------------------- availability (provider)


def test_availability_round_trip_and_replace(client, db):
    u, _ = mk_provider(db)
    h = hdr(u)
    empty = client.get(f"{P}/availability", headers=h).json()["data"]
    assert empty == {
        "timezone": "Africa/Lagos",
        "min_notice_h": 24,
        "buffer_min": 0,
        "rules": [],
        "exceptions": [],
    }
    tomorrow = (date.today() + timedelta(days=3)).isoformat()
    body = {
        "min_notice_h": 12,
        "buffer_min": 15,
        "rules": [
            {"weekday": 0, "start": "09:00", "end": "12:00", "slot_min": 30},
            {"weekday": 0, "start": "14:00", "end": "17:00", "slot_min": 60},
        ],
        "exceptions": [
            {"on_date": tomorrow, "kind": "blocked", "start": None, "end": None},
            {"on_date": tomorrow, "kind": "extra", "start": "18:00", "end": "19:00"},
        ],
    }
    r = client.put(f"{P}/availability", json=body, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["rules"][0] == {
        "weekday": 0,
        "start": "09:00",
        "end": "12:00",
        "slot_min": 30,
    }
    assert client.get(f"{P}/availability", headers=h).json()["data"] == r.json()["data"]
    r2 = client.put(f"{P}/availability", json={**body, "rules": [], "exceptions": []}, headers=h)
    assert r2.json()["data"]["rules"] == [] and r2.json()["data"]["exceptions"] == []
    assert r2.json()["data"]["min_notice_h"] == 12  # settings replaced with the schedule
    assert "marketplace.availability.updated" in audit_actions(db)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b["rules"].append(
            {"weekday": 7, "start": "09:00", "end": "10:00", "slot_min": 30}
        ),
        lambda b: b["rules"].append(
            {"weekday": 1, "start": "10:00", "end": "09:00", "slot_min": 30}
        ),
        lambda b: b["rules"].append(
            {"weekday": 1, "start": "10:00", "end": "10:00", "slot_min": 30}
        ),
        lambda b: b["rules"].append(
            {"weekday": 1, "start": "09:00", "end": "10:00", "slot_min": 20}
        ),
        lambda b: b["rules"].append(
            {"weekday": 1, "start": "09:00:30", "end": "10:00", "slot_min": 30}
        ),
        lambda b: b["exceptions"].append({"on_date": "2020-01-01", "kind": "blocked"}),
        lambda b: b["exceptions"].append(
            {"on_date": (date.today() + timedelta(days=2)).isoformat(), "kind": "extra"}
        ),
        lambda b: b["exceptions"].append(
            {
                "on_date": (date.today() + timedelta(days=2)).isoformat(),
                "kind": "blocked",
                "start": "09:00",
            }
        ),
        lambda b: b["exceptions"].append(
            {
                "on_date": (date.today() + timedelta(days=2)).isoformat(),
                "kind": "extra",
                "start": "11:00",
                "end": "10:00",
            }
        ),
        lambda b: b.update(min_notice_h=-1),
        lambda b: b.update(buffer_min=500),
        lambda b: b.update(
            rules=[{"weekday": 0, "start": "08:00", "end": "09:00", "slot_min": 30}] * 29
        ),
        lambda b: b.update(surprise=1),
    ],
)
def test_availability_validation(client, db, mutate):
    u, _ = mk_provider(db)
    body = {"min_notice_h": 24, "buffer_min": 0, "rules": [], "exceptions": []}
    mutate(body)
    resp12 = client.put(f"{P}/availability", json=body, headers=hdr(u))
    assert resp12.status_code == 422


def test_a_failed_availability_put_leaves_the_old_schedule(client, db):
    u, _ = mk_provider(db)
    h = hdr(u)
    ok = {
        "min_notice_h": 24,
        "buffer_min": 0,
        "rules": [{"weekday": 1, "start": "09:00", "end": "10:00", "slot_min": 30}],
        "exceptions": [],
    }
    resp13 = client.put(f"{P}/availability", json=ok, headers=h)
    assert resp13.status_code == 200
    bad = {**ok, "rules": [{"weekday": 2, "start": "11:00", "end": "10:00", "slot_min": 30}]}
    resp14 = client.put(f"{P}/availability", json=bad, headers=h)
    assert resp14.status_code == 422
    assert client.get(f"{P}/availability", headers=h).json()["data"]["rules"][0]["weekday"] == 1

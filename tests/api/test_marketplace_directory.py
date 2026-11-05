"""Marketplace slice B, founder side: who can browse, what is visible, filters, sort, slots,
files and reports. The rule under test throughout: only approved experts and active listings are
visible, and nothing staff-only ever leaves the directory."""

import json
import uuid
from datetime import date, timedelta

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.redis import get_redis
from app.db.models.content_report import ContentReport
from app.db.models.enums import (
    ListingStatus,
    MarketplaceFilePurpose,
    MembershipRole,
    ProviderStatus,
    ReportItemType,
    ReportStatus,
    ScanStatus,
    StaffRole,
)
from app.db.models.marketplace import MarketplaceFile
from app.platform.storage import get_storage
from tests.api.test_marketplace_listings import (
    M,
    P,
    fh,
    founder,
    mk_listing,
    mk_provider,
    open_week,
    staff_post,
)
from tests.api.test_staff_users import hdr, mk_staff, mk_user

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
FORBIDDEN_KEYS = (
    "email",
    "user_id",
    "vetting",
    "prescreen",
    "commission",
    "review_message",
    "meeting_url",
    "pending_revision",
    "unlisted",
    "submitted_at",
    "terms_",
    "status",
    "suspended",
    "invite",
    "totp",
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    r = get_redis()
    for pat in ("staff_stepup_*", "report_rl:*"):
        for k in r.scan_iter(pat):
            r.delete(k)


@pytest.fixture()
def moderator(db):
    return mk_staff(db, StaffRole.admin, ("moderation",))


def get(client, user, ws, path, **params):
    return client.get(f"{M}{path}", params=params, headers=fh(user, ws))


@pytest.fixture()
def fx(db):
    f, ws = founder(db)
    return f, ws


# ---------------------------------------------------------------- who can browse


def test_founder_and_team_member_browse_everyone_else_is_refused(client, db):
    _, prov = mk_provider(db)
    mk_listing(db, prov)
    for role, ok in (
        (MembershipRole.founder, True),
        (MembershipRole.team_member, True),
        (MembershipRole.mentor, False),
        (MembershipRole.accountant, False),
        (MembershipRole.legal_advisor, False),
        (MembershipRole.investor, False),
    ):
        user, ws = founder(db, role)
        for path in ("/providers", "/listings"):
            r = get(client, user, ws, path)
            assert r.status_code == (200 if ok else 403), (role, path, r.status_code)


def test_anonymous_and_workspaceless_callers_cannot_browse(client, db):
    assert client.get(f"{M}/providers").status_code == 401
    lonely = mk_user(db)
    assert client.get(f"{M}/providers", headers=hdr(lonely)).status_code in (400, 403, 404, 422)
    _, prov = mk_provider(db)
    lid = mk_listing(db, prov).id
    for path in (f"/providers/{prov.id}", f"/listings/{lid}", f"/listings/{lid}/slots"):
        assert client.get(f"{M}{path}").status_code == 401, path


def test_an_expert_cannot_browse_as_an_expert(client, db):
    u, prov = mk_provider(db)
    mk_listing(db, prov)
    assert client.get(f"{M}/providers", headers=hdr(u)).status_code in (400, 403, 404, 422)


# ---------------------------------------------------------------- visibility


def test_only_approved_experts_with_active_listings_are_visible(client, db, fx):
    f, ws = fx
    _, shown = mk_provider(db, name="Visible Vera")
    mk_listing(db, shown)
    for st in (
        ProviderStatus.invited,
        ProviderStatus.applied,
        ProviderStatus.in_review,
        ProviderStatus.changes_requested,
        ProviderStatus.suspended,
        ProviderStatus.rejected,
    ):
        _, hidden = mk_provider(db, name=f"Hidden {st.value}", status=st)
        mk_listing(db, hidden)  # even with a live listing, the expert's status wins
    _, none = mk_provider(db, name="No Listings")
    _, only_draft = mk_provider(db, name="Only Draft")
    for st in (
        ListingStatus.draft,
        ListingStatus.pending_review,
        ListingStatus.paused,
        ListingStatus.unlisted,
        ListingStatus.rejected,
    ):
        mk_listing(db, only_draft, status=st, title=f"Not live {st.value}")
    names = [
        p["display_name"] for p in get(client, f, ws, "/providers").json()["data"]["providers"]
    ]
    assert names == ["Visible Vera"]
    listings = get(client, f, ws, "/listings").json()["data"]["listings"]
    assert [x["provider"]["display_name"] for x in listings] == ["Visible Vera"]


def test_listings_of_non_approved_experts_are_not_listed(client, db, fx):
    f, ws = fx
    _, ok = mk_provider(db, name="Approved Ann")
    mk_listing(db, ok, title="Visible listing")
    for st in (ProviderStatus.suspended, ProviderStatus.in_review, ProviderStatus.rejected):
        _, p = mk_provider(db, name=f"Not ok {st.value}", status=st)
        mk_listing(db, p, title=f"Hidden by {st.value}")
    titles = [x["title"] for x in get(client, f, ws, "/listings").json()["data"]["listings"]]
    assert titles == ["Visible listing"]


def test_profile_and_listing_detail_404_when_hidden(client, db, fx):
    f, ws = fx
    _, sus = mk_provider(db, status=ProviderStatus.suspended)
    live = mk_listing(db, sus)
    _, none = mk_provider(db)
    _, draft = mk_provider(db)
    d = mk_listing(db, draft, status=ListingStatus.draft)
    for path in (
        f"/providers/{sus.id}",
        f"/providers/{none.id}",
        f"/providers/{draft.id}",
        f"/listings/{live.id}",
        f"/listings/{d.id}",
        f"/listings/{live.id}/slots",
        f"/listings/{uuid.uuid4()}",
        f"/providers/{uuid.uuid4()}",
    ):
        assert get(client, f, ws, path).status_code == 404, path


def test_an_expert_vanishes_the_moment_they_are_suspended(client, db, fx):
    f, ws = fx
    _, prov = mk_provider(db)
    lst = mk_listing(db, prov)
    assert get(client, f, ws, f"/providers/{prov.id}").status_code == 200
    prov.status = ProviderStatus.suspended
    db.flush()
    assert get(client, f, ws, f"/providers/{prov.id}").status_code == 404
    assert get(client, f, ws, f"/listings/{lst.id}").status_code == 404
    assert get(client, f, ws, "/providers").json()["data"]["providers"] == []


def test_no_staff_or_vetting_fields_leak(client, db, fx):
    f, ws = fx
    u, prov = mk_provider(db, name="Leak Check")
    prov.commission_bps = 900
    prov.review_message = "INTERNAL-NOTE"
    prov.suspended_reason = "INTERNAL-SUSPEND"
    prov.vetting_json = {
        "checks": {"id": {"done": True, "by": str(uuid.uuid4()), "note": "NIN-SECRET"}},
        "prescreen": {"flags": [{"rule": "contact_details", "field": "bio"}]},
        "profile_flag": {"flags": []},
    }
    lst = mk_listing(
        db,
        prov,
        meeting_url="https://meet.example.com/SECRET-ROOM",
        prescreen_json={"flags": [{"rule": "x", "field": "y"}]},
        review_message="LISTING-INTERNAL",
        pending_revision_json={"title": "UNREVIEWED-TITLE"},
    )
    for path in ("/providers", f"/providers/{prov.id}", "/listings", f"/listings/{lst.id}"):
        body = get(client, f, ws, path).text
        for secret in (
            "SECRET-ROOM",
            "INTERNAL",
            "NIN-SECRET",
            "UNREVIEWED-TITLE",
            u.email,
            str(u.id),
            "vetting_json",
            "prescreen",
            "commission",
        ):
            assert secret not in body, (path, secret)
    card = get(client, f, ws, "/providers").json()["data"]["providers"][0]
    assert set(card) == {
        "id",
        "display_name",
        "headline",
        "category",
        "categories",
        "languages",
        "location",
        "timezone",
        "avatar_url",
        "badge",
        "rating",
        "from_price_minor",
        "currency",
        "listing_count",
        "listing_types",
        "next_available_at",
    }
    item = get(client, f, ws, "/listings").json()["data"]["listings"][0]
    assert set(item) == {
        "id",
        "type",
        "title",
        "summary",
        "description",
        "category",
        "tags",
        "price_minor",
        "currency",
        "duration_min",
        "delivery_days",
        "deliverables",
        "revisions_included",
        "next_available_at",
        "provider",
    }
    assert set(item["provider"]) == {
        "id",
        "display_name",
        "headline",
        "avatar_url",
        "badge",
        "rating",
    }
    profile = get(client, f, ws, f"/providers/{prov.id}").json()["data"]
    assert set(profile) == set(card) | {"bio", "portfolio", "listings"}


def test_badge_comes_from_the_ticks_and_rating_reads_new_until_three_reviews(client, db, fx):
    f, ws = fx
    _, new = mk_provider(db, name="New Nia", rating=(5, 2))
    _, old = mk_provider(db, name="Rated Ray", rating=(4.5, 3))
    for p in (new, old):
        mk_listing(db, p)
    cards = {
        c["display_name"]: c for c in get(client, f, ws, "/providers").json()["data"]["providers"]
    }
    assert cards["New Nia"]["rating"] == {"avg": None, "count": 2, "is_new": True}
    assert cards["Rated Ray"]["rating"] == {"avg": 4.5, "count": 3, "is_new": False}
    assert cards["Rated Ray"]["badge"] == {
        "vetted": True,
        "checks": ["Identity verified", "1 client reference checked"],
    }


# ---------------------------------------------------------------- filters, sort, paging


def seed(db):
    _, a = mk_provider(
        db, name="Ada Design", category="Design", languages=("English", "Yoruba"), rating=(4.8, 10)
    )
    _, b = mk_provider(
        db, name="Bola Dev", category="Development", languages=("English",), rating=(4.0, 5)
    )
    _, c = mk_provider(
        db, name="Chidi Legal", category="Legal", languages=("English", "Igbo"), rating=(0, 0)
    )
    mk_listing(
        db, a, title="Logo sprint", type_="package", price_minor=5_000_000, category="Design"
    )
    mk_listing(db, a, title="Design critique", price_minor=1_500_000, category="Design")
    mk_listing(db, b, title="API review", price_minor=3_000_000, category="Development")
    mk_listing(db, c, title="Contract review", price_minor=2_000_000, category="Legal")
    return a, b, c


def names(r):
    return [p["display_name"] for p in r.json()["data"]["providers"]]


def test_provider_filters(client, db, fx):
    f, ws = fx
    seed(db)
    assert sorted(names(get(client, f, ws, "/providers"))) == [
        "Ada Design",
        "Bola Dev",
        "Chidi Legal",
    ]
    assert names(get(client, f, ws, "/providers", category="Legal")) == ["Chidi Legal"]
    assert names(get(client, f, ws, "/providers", min_price=4_000_000)) == ["Ada Design"]
    assert sorted(names(get(client, f, ws, "/providers", max_price=2_000_000))) == [
        "Ada Design",
        "Chidi Legal",
    ]
    assert sorted(names(get(client, f, ws, "/providers", min_rating=4))) == [
        "Ada Design",
        "Bola Dev",
    ]
    assert names(get(client, f, ws, "/providers", min_rating=4.5)) == ["Ada Design"]
    assert names(get(client, f, ws, "/providers", language="igbo")) == [
        "Chidi Legal"
    ]  # case-insensitive
    assert names(get(client, f, ws, "/providers", language="YORUBA")) == ["Ada Design"]
    assert names(get(client, f, ws, "/providers", language="Hausa")) == []
    assert names(get(client, f, ws, "/providers", q="critique")) == [
        "Ada Design"
    ]  # matches a listing title
    assert names(get(client, f, ws, "/providers", q="bola")) == ["Bola Dev"]
    assert names(get(client, f, ws, "/providers", q="100%_")) == []  # wildcards are literal
    assert get(client, f, ws, "/providers", category="Cooking").status_code == 422
    assert get(client, f, ws, "/providers", min_rating=9).status_code == 422
    assert get(client, f, ws, "/providers", min_price=-1).status_code == 422


def test_provider_from_price_is_the_cheapest_live_listing(client, db, fx):
    f, ws = fx
    seed(db)
    ada = next(
        c
        for c in get(client, f, ws, "/providers").json()["data"]["providers"]
        if c["display_name"] == "Ada Design"
    )
    assert ada["from_price_minor"] == 1_500_000 and ada["listing_count"] == 2
    assert ada["listing_types"] == ["consultation", "package"]
    # filtering by a price band must not hide the cheaper listing from the card
    narrow = get(client, f, ws, "/providers", min_price=4_000_000).json()["data"]["providers"][0]
    assert narrow["from_price_minor"] == 1_500_000


def test_provider_sorts(client, db, fx):
    f, ws = fx
    seed(db)
    assert names(get(client, f, ws, "/providers", sort="price_asc")) == [
        "Ada Design",
        "Chidi Legal",
        "Bola Dev",
    ]
    assert names(get(client, f, ws, "/providers", sort="price_desc")) == [
        "Bola Dev",
        "Chidi Legal",
        "Ada Design",
    ]
    assert names(get(client, f, ws, "/providers", sort="rating")) == [
        "Ada Design",
        "Bola Dev",
        "Chidi Legal",
    ]
    first = names(get(client, f, ws, "/providers", sort="relevance"))
    assert first == names(get(client, f, ws, "/providers"))  # relevance is the default
    assert first == names(get(client, f, ws, "/providers", sort="relevance"))  # and deterministic
    assert first[0] == "Ada Design"  # rating counts once there are three reviews
    assert get(client, f, ws, "/providers", sort="vibes").status_code == 422


def test_provider_paging(client, db, fx):
    f, ws = fx
    for i in range(14):
        _, p = mk_provider(db, name=f"Expert {i:02d}")
        mk_listing(db, p, price_minor=1_000_000 + i)
    one = get(client, f, ws, "/providers", sort="price_asc").json()["data"]
    assert len(one["providers"]) == 12 and one["pagination"] == {
        "page": 1,
        "page_size": 12,
        "total": 14,
    }
    two = get(client, f, ws, "/providers", sort="price_asc", page=2).json()["data"]
    assert len(two["providers"]) == 2 and two["pagination"]["page"] == 2
    ids = [p["id"] for p in one["providers"] + two["providers"]]
    assert len(set(ids)) == 14
    assert get(client, f, ws, "/providers", page=3).json()["data"]["providers"] == []
    assert get(client, f, ws, "/providers", page=0).status_code == 422


def test_available_this_week_needs_real_slots(client, db, fx):
    f, ws = fx
    u1, p1 = mk_provider(db, name="Has Slots")
    mk_listing(db, p1)
    _, p2 = mk_provider(db, name="No Schedule")
    mk_listing(db, p2)
    _, p3 = mk_provider(db, name="Package Only")
    mk_listing(db, p3, type_="package")
    open_week(client, u1)
    assert names(get(client, f, ws, "/providers", available="true")) == ["Has Slots"]
    everyone = get(client, f, ws, "/providers").json()["data"]["providers"]
    by_name = {c["display_name"]: c for c in everyone}
    assert (
        by_name["Has Slots"]["next_available_at"]
        and by_name["No Schedule"]["next_available_at"] is None
    )


def test_listing_filters_and_sorts(client, db, fx):
    f, ws = fx
    seed(db)

    def titles(**p):
        r = get(client, f, ws, "/listings", **p)
        assert r.status_code == 200, r.text
        return [x["title"] for x in r.json()["data"]["listings"]]

    assert sorted(titles()) == ["API review", "Contract review", "Design critique", "Logo sprint"]
    assert titles(type="package") == ["Logo sprint"]
    assert sorted(titles(type="consultation")) == [
        "API review",
        "Contract review",
        "Design critique",
    ]
    assert titles(category="Legal") == ["Contract review"]
    assert sorted(titles(min_price=2_000_000, max_price=3_000_000)) == [
        "API review",
        "Contract review",
    ]
    assert titles(max_delivery_days=5) == ["Logo sprint"]  # only packages have delivery days
    assert titles(max_delivery_days=4) == []
    assert titles(q="logo") == ["Logo sprint"]
    assert sorted(titles(q="ada")) == [
        "Design critique",
        "Logo sprint",
    ]  # matches the expert's name
    assert titles(sort="price_asc") == [
        "Design critique",
        "Contract review",
        "API review",
        "Logo sprint",
    ]
    assert titles(sort="price_desc")[0] == "Logo sprint"
    assert get(client, f, ws, "/listings", type="bundle").status_code == 422
    assert get(client, f, ws, "/listings", sort="nope").status_code == 422
    newest = titles(sort="newest")
    assert newest[0] == "Contract review"  # created last


def test_listing_detail_has_the_provider_summary_and_no_meeting_link(client, db, fx):
    f, ws = fx
    u, prov = mk_provider(db, name="Detail Dee")
    lst = mk_listing(db, prov, meeting_url="https://meet.example.com/private")
    d = get(client, f, ws, f"/listings/{lst.id}").json()["data"]
    assert d["provider"]["display_name"] == "Detail Dee" and d["price_minor"] == 1_000_000
    assert "meet.example.com" not in json.dumps(d)


# ---------------------------------------------------------------- profile


def test_profile_shows_bio_listings_and_portfolio(client, db, fx):
    f, ws = fx
    u, prov = mk_provider(db, name="Profile Pat")
    mk_listing(db, prov, title="Live one")
    mk_listing(db, prov, status=ListingStatus.draft, title="Draft one")
    from app.db.models.marketplace import MarketplaceProviderPortfolio

    item = MarketplaceProviderPortfolio(
        provider_id=prov.id, title="Case study", url="https://work.example.com", position=0
    )
    db.add(item)
    db.flush()
    d = get(client, f, ws, f"/providers/{prov.id}").json()["data"]
    assert d["bio"].startswith("Fifteen years")
    assert [x["title"] for x in d["listings"]] == ["Live one"]
    assert d["portfolio"] == [
        {
            "id": str(item.id),
            "title": "Case study",
            "description": None,
            "url": "https://work.example.com",
            "file_url": None,
        }
    ]


# ---------------------------------------------------------------- slots


def next_weekday(weekday, min_days=3):
    d = date.today() + timedelta(days=min_days)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


def test_slots_follow_the_schedule_exceptions_and_timezone(client, db, fx):
    f, ws = fx
    u, prov = mk_provider(db, tz="Africa/Lagos")
    lst = mk_listing(db, prov, duration_min=60)
    mon, tue = next_weekday(0), next_weekday(1)
    body = {
        "min_notice_h": 0,
        "buffer_min": 0,
        "rules": [
            {"weekday": 0, "start": "09:00", "end": "11:00", "slot_min": 60},
            {"weekday": 1, "start": "09:00", "end": "11:00", "slot_min": 60},
        ],
        "exceptions": [{"on_date": tue.isoformat(), "kind": "blocked"}],
    }
    resp = client.put(f"{P}/availability", json=body, headers=hdr(u))
    assert resp.status_code == 200
    r = get(
        client,
        f,
        ws,
        f"/listings/{lst.id}/slots",
        **{"from": mon.isoformat(), "to": tue.isoformat()},
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["timezone"] == "Africa/Lagos" and d["duration_min"] == 60
    assert [s["starts_at"] for s in d["slots"]] == [
        f"{mon.isoformat()}T08:00:00+00:00",
        f"{mon.isoformat()}T09:00:00+00:00",
    ]  # Tuesday is blocked
    assert d["slots"][0]["local_start"] == f"{mon.isoformat()}T09:00:00+01:00"
    assert d["slots"][0]["ends_at"] == f"{mon.isoformat()}T09:00:00+00:00"


def test_slots_respect_minimum_notice(client, db, fx):
    f, ws = fx
    u, prov = mk_provider(db)
    lst = mk_listing(db, prov)
    day = date.today() + timedelta(days=1)
    body = {
        "min_notice_h": 24 * 10,
        "buffer_min": 0,
        "rules": [
            {"weekday": d, "start": "08:00", "end": "20:00", "slot_min": 60} for d in range(7)
        ],
        "exceptions": [],
    }
    client.put(f"{P}/availability", json=body, headers=hdr(u))
    r = get(
        client,
        f,
        ws,
        f"/listings/{lst.id}/slots",
        **{"from": day.isoformat(), "to": (day + timedelta(days=6)).isoformat()},
    )
    assert r.json()["data"]["slots"] == []


def test_slot_range_rules(client, db, fx):
    f, ws = fx
    u, prov = mk_provider(db)
    lst = mk_listing(db, prov)
    pkg = mk_listing(db, prov, type_="package", title="A package")
    today = date.today()
    path = f"/listings/{lst.id}/slots"
    ok = get(client, f, ws, path)  # defaults: today and the next six days
    assert ok.status_code == 200
    long = get(
        client,
        f,
        ws,
        path,
        **{"from": today.isoformat(), "to": (today + timedelta(days=14)).isoformat()},
    )
    assert long.status_code == 422 and long.json()["error"]["code"] == "RANGE_TOO_LONG"
    back = get(
        client,
        f,
        ws,
        path,
        **{"from": (today + timedelta(days=3)).isoformat(), "to": today.isoformat()},
    )
    assert back.status_code == 422
    far = get(client, f, ws, path, **{"from": (today + timedelta(days=90)).isoformat()})
    assert far.status_code == 422 and far.json()["error"]["code"] == "RANGE_OUT_OF_BOUNDS"
    past = get(client, f, ws, path, **{"from": "2020-01-01", "to": "2020-01-03"})
    assert past.status_code == 422
    assert get(client, f, ws, path, **{"from": "not-a-date"}).status_code == 422
    nope = get(client, f, ws, f"/listings/{pkg.id}/slots")
    assert nope.status_code == 422 and nope.json()["error"]["code"] == "NOT_A_CONSULTATION"


# ---------------------------------------------------------------- avatar and portfolio files


def _file(db, user, purpose, status=ScanStatus.clean):
    key = f"marketplace/{user.id}/{uuid.uuid4().hex}.png"
    get_storage().save_private(key, PNG, "image/png")
    row = MarketplaceFile(
        owner_user_id=user.id,
        purpose=purpose,
        filename="a.png",
        mime="image/png",
        size_bytes=len(PNG),
        storage_key=key,
        scan_status=status,
    )
    db.add(row)
    db.flush()
    return row


def test_avatar_is_served_to_founders_only_while_the_expert_is_visible(client, db, fx):
    f, ws = fx
    u, prov = mk_provider(db)
    mk_listing(db, prov)
    av = _file(db, u, MarketplaceFilePurpose.avatar)
    prov.avatar_file_id = av.id
    db.flush()
    card = get(client, f, ws, f"/providers/{prov.id}").json()["data"]
    assert card["avatar_url"] == f"/api/v1/marketplace/providers/{prov.id}/avatar"
    r = client.get(card["avatar_url"], headers=fh(f, ws))
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get(card["avatar_url"]).status_code == 401
    av.scan_status = ScanStatus.pending
    db.flush()
    unclean = client.get(card["avatar_url"], headers=fh(f, ws))
    assert unclean.status_code == 409 and unclean.json()["error"]["code"] == "FILE_NOT_CLEAN"
    av.scan_status = ScanStatus.clean
    prov.status = ProviderStatus.suspended
    db.flush()
    assert client.get(card["avatar_url"], headers=fh(f, ws)).status_code == 404


def test_portfolio_files_are_scoped_to_the_expert_and_purpose(client, db, fx):
    f, ws = fx
    u, prov = mk_provider(db)
    mk_listing(db, prov)
    other_u, other = mk_provider(db, name="Other One")
    from app.db.models.marketplace import MarketplaceProviderPortfolio

    pf = _file(db, u, MarketplaceFilePurpose.portfolio)
    cred = _file(db, u, MarketplaceFilePurpose.credential)
    ok = MarketplaceProviderPortfolio(
        provider_id=prov.id, title="Poster", file_id=pf.id, position=0
    )
    sneaky = MarketplaceProviderPortfolio(
        provider_id=prov.id, title="Sneaky", file_id=cred.id, position=1
    )
    db.add_all([ok, sneaky])
    db.flush()
    base = f"{M}/providers/{prov.id}/portfolio"
    assert client.get(f"{base}/{ok.id}/file", headers=fh(f, ws)).content == PNG
    # a credential scan can never be reached through a portfolio item, even if one points at it
    assert client.get(f"{base}/{sneaky.id}/file", headers=fh(f, ws)).status_code == 404
    # nor through a different expert's URL
    assert (
        client.get(
            f"{M}/providers/{other.id}/portfolio/{ok.id}/file", headers=fh(f, ws)
        ).status_code
        == 404
    )
    assert client.get(f"{base}/{uuid.uuid4()}/file", headers=fh(f, ws)).status_code == 404


# ---------------------------------------------------------------- reports (new item types)


def report(client, user, ws, item_type, item_id, reason="spam", **extra):
    return client.post(
        "/api/v1/reports",
        json={"item_type": item_type, "item_id": str(item_id), "reason": reason, **extra},
        headers=fh(user, ws),
    )


def test_founder_reports_a_listing_and_an_expert(client, db, fx):
    f, ws = fx
    u, prov = mk_provider(db, name="Reported Rae")
    lst = mk_listing(db, prov, title="Dodgy offer")
    r = report(client, f, ws, "listing", lst.id, "abusive")
    assert r.status_code == 201 and r.json()["data"]["status"] == "received"
    row = db.query(ContentReport).filter_by(item_id=lst.id).one()
    assert row.item_type == ReportItemType.listing and row.item_author_id == u.id
    assert row.snippet.startswith("Dodgy offer")
    assert report(client, f, ws, "listing", lst.id).status_code == 409  # one live report each
    p = report(client, f, ws, "provider", prov.id, "other", note="Impersonating someone")
    assert p.status_code == 201
    assert (
        db.query(ContentReport).filter_by(item_id=prov.id).one().snippet.startswith("Reported Rae")
    )


def test_reports_need_a_visible_item_and_not_your_own(client, db, fx):
    f, ws = fx
    _, hidden = mk_provider(db, status=ProviderStatus.suspended)
    h_listing = mk_listing(db, hidden)
    _, drafty = mk_provider(db)
    d_listing = mk_listing(db, drafty, status=ListingStatus.draft)
    for t, i in (
        ("listing", h_listing.id),
        ("listing", d_listing.id),
        ("listing", uuid.uuid4()),
        ("provider", hidden.id),
        ("provider", drafty.id),
    ):
        assert report(client, f, ws, t, i).status_code == 404, (t, i)
    assert (
        report(client, f, ws, "review", uuid.uuid4()).status_code == 422
    )  # not reportable until slice F
    own_u, own = mk_provider(db, user=f)
    mine = mk_listing(db, own)
    r = report(client, f, ws, "listing", mine.id)
    assert r.status_code == 422 and r.json()["error"]["code"] == "OWN_ITEM"
    assert report(client, f, ws, "provider", own.id).json()["error"]["code"] == "OWN_ITEM"


def _admin_reports_post(client, actor, path, **extra):
    import pyotp

    from app.core.redis import get_redis

    redis = get_redis()
    for k in redis.scan_iter("staff_stepup_used:*"):
        redis.delete(k)
    body = {
        "reason": "Reviewed against the marketplace rules",
        "totp_code": pyotp.TOTP(actor.test_secret).now(),
        **extra,
    }
    return client.post(f"/api/v1/admin/reports{path}", json=body, headers=hdr(actor))


def test_report_removal_unlists_and_restore_relists(client, db, moderator, fx):
    f, ws = fx
    u, prov = mk_provider(db)
    lst = mk_listing(db, prov)
    rid = report(client, f, ws, "listing", lst.id, "harmful").json()["data"]["id"]
    r = _admin_reports_post(client, moderator, f"/{rid}/remove")
    assert r.status_code == 200, r.text
    db.refresh(lst)
    assert (
        lst.status == ListingStatus.unlisted and lst.unlisted_reason == "Unlisted after a report."
    )
    assert get(client, f, ws, f"/listings/{lst.id}").status_code == 404
    back = _admin_reports_post(client, moderator, f"/{rid}/restore")
    assert back.status_code == 200
    db.refresh(lst)
    assert lst.status == ListingStatus.active and lst.unlisted_reason is None


def test_report_removal_of_a_provider_points_at_vetting(client, db, moderator, fx):
    f, ws = fx
    _, prov = mk_provider(db)
    mk_listing(db, prov)
    rid = report(client, f, ws, "provider", prov.id, "harmful").json()["data"]["id"]
    r = _admin_reports_post(client, moderator, f"/{rid}/remove")
    assert r.status_code == 422 and r.json()["error"]["code"] == "USE_VETTING"
    assert db.get(type(prov), prov.id).status == ProviderStatus.approved


def test_staff_cannot_decide_a_report_about_their_own_listing(client, db, moderator, fx):
    f, ws = fx
    _, prov = mk_provider(db, user=moderator)
    lst = mk_listing(db, prov)
    rid = report(client, f, ws, "listing", lst.id, "harmful").json()["data"]["id"]
    r = _admin_reports_post(client, moderator, f"/{rid}/remove")
    assert r.status_code == 422 and r.json()["error"]["code"] == "CANNOT_ACT_ON_SELF"
    db.refresh(lst)
    assert lst.status == ListingStatus.active
    assert db.get(ContentReport, uuid.UUID(rid)).status == ReportStatus.open


# ---------------------------------------------------------------- review fixes (Opus review of #224)


def _audit_rows(db, listing_id, action):
    from app.db.models.audit import AuditLog

    return db.query(AuditLog).filter_by(action=action, entity_id=listing_id).all()


def test_report_unlist_and_relist_are_audited_and_carry_the_actor(client, db, moderator, fx):
    f, ws = fx
    _, prov = mk_provider(db)
    lst = mk_listing(db, prov)
    rid = report(client, f, ws, "listing", lst.id, "harmful").json()["data"]["id"]
    assert _admin_reports_post(client, moderator, f"/{rid}/remove").status_code == 200
    db.refresh(lst)
    assert lst.status == ListingStatus.unlisted and lst.unlisted_by == moderator.id
    (row,) = _audit_rows(db, lst.id, "marketplace.listing.unlisted")
    assert row.actor_user_id == moderator.id and row.details == {"via": "report", "report_id": rid}
    assert _admin_reports_post(client, moderator, f"/{rid}/restore").status_code == 200
    db.refresh(lst)
    assert lst.status == ListingStatus.active and lst.unlisted_by is None
    (back,) = _audit_rows(db, lst.id, "marketplace.listing.relisted")
    assert back.details == {"via": "report", "report_id": rid}


def test_restoring_a_report_never_relists_what_a_moderator_unlisted(client, db, moderator, fx):
    f, ws = fx
    _, prov = mk_provider(db)
    lst = mk_listing(db, prov)
    rid = report(client, f, ws, "listing", lst.id, "harmful").json()["data"]["id"]
    # a moderator takes it down for fraud, with step-up
    r = staff_post(client, moderator, f"/listings/{lst.id}/unlist", reason="Fraudulent claims")
    assert r.status_code == 200
    # the report removal is a no-op on a listing that is already down...
    assert _admin_reports_post(client, moderator, f"/{rid}/remove").status_code == 200
    db.refresh(lst)
    assert lst.unlisted_reason == "Fraudulent claims"  # not overwritten by the report flow
    assert (_audit_rows(db, lst.id, "marketplace.listing.unlisted")[0].details or {}).get(
        "via"
    ) != "report"
    # ...and restoring it must not bring the listing back
    assert _admin_reports_post(client, moderator, f"/{rid}/restore").status_code == 200
    db.refresh(lst)
    assert lst.status == ListingStatus.unlisted
    assert _audit_rows(db, lst.id, "marketplace.listing.relisted") == []
    assert get(client, f, ws, f"/listings/{lst.id}").status_code == 404


def test_a_later_moderator_unlist_also_blocks_a_report_restore(client, db, moderator, fx):
    f, ws = fx
    _, prov = mk_provider(db)
    lst = mk_listing(db, prov)
    rid = report(client, f, ws, "listing", lst.id, "harmful").json()["data"]["id"]
    assert (
        _admin_reports_post(client, moderator, f"/{rid}/remove").status_code == 200
    )  # report unlists
    assert staff_post(client, moderator, f"/listings/{lst.id}/relist").status_code == 200
    assert (
        staff_post(
            client, moderator, f"/listings/{lst.id}/unlist", reason="Second offence"
        ).status_code
        == 200
    )
    assert _admin_reports_post(client, moderator, f"/{rid}/restore").status_code == 200
    db.refresh(lst)
    assert lst.status == ListingStatus.unlisted  # the latest unlisting is the moderator's


def test_staff_cannot_dismiss_or_escalate_reports_about_their_own_items(client, db, moderator, fx):
    f, ws = fx
    _, prov = mk_provider(db, user=moderator)
    lst = mk_listing(db, prov)
    mh = hdr(moderator)
    for item_type, item_id in (("listing", lst.id), ("provider", prov.id)):
        rid = report(client, f, ws, item_type, item_id, "harmful").json()["data"]["id"]
        for action in ("dismiss", "escalate"):
            r = client.post(
                f"/api/v1/admin/reports/{rid}/{action}",
                json={"reason": "Reviewed against the marketplace rules"},
                headers=mh,
            )
            assert r.status_code == 422, (item_type, action, r.text)
            assert r.json()["error"]["code"] == "CANNOT_ACT_ON_SELF"
        assert db.get(ContentReport, uuid.UUID(rid)).status == ReportStatus.open


def test_review_is_not_a_reportable_type_yet(client, db, fx):
    f, ws = fx
    r = report(client, f, ws, "review", uuid.uuid4())
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert not any(t.value == "review" for t in ReportItemType)


def test_avatar_and_portfolio_files_need_a_live_listing_too(client, db, fx):
    f, ws = fx
    u, prov = mk_provider(db)
    av = _file(db, u, MarketplaceFilePurpose.avatar)
    prov.avatar_file_id = av.id
    db.flush()
    url = f"{M}/providers/{prov.id}/avatar"
    assert client.get(url, headers=fh(f, ws)).status_code == 404  # approved but nothing live
    mk_listing(db, prov)
    assert client.get(url, headers=fh(f, ws)).status_code == 200

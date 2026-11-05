"""Live: Marketplace slice B. An approved expert sets availability and lists services; staff review
them (approve, reject, a live edit going back to review, pause, unlist, relist); founders and team
members browse the directory, profile, listings and slots; a mentor is refused; a founder reports a
listing and staff unlist it; suspending the expert hides everything.

One real server on a database every other journey shares, so nothing here asserts exact counts.
Responses go to e2e/_captures/marketplace_listings/ and are the verbatim source of
docs/fe-integration-guide-marketplace-listings.md.
"""

import os
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta

import httpx
import pyotp

A = "/api/v1/admin/marketplace"
AR = "/api/v1/admin/reports"
S = "/api/v1/super-admin"
P = "/api/v1/provider"
M = "/api/v1/marketplace"
T = "/api/v1/team"
G = "marketplace_listings"
PDF = b"%PDF-1.4\n%placeholder credential scan\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
BIO = "Fifteen years advising founders on contracts, shareholder agreements and fundraising."
DESC = "A focused one hour session reviewing your contract or term sheet, with written notes after."


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


class Authenticator:
    """A phone's authenticator app: a code not yet spent on a step-up, waiting for the next one
    when all are used."""

    def __init__(self, secret: str) -> None:
        self.totp = pyotp.TOTP(secret)
        self.used: set[str] = set()

    def fresh(self) -> str:
        for _ in range(80):
            now = datetime.now(UTC)
            for k in (0, -1, 1):
                c = self.totp.at(now + timedelta(seconds=30 * k))
                if c not in self.used:
                    self.used.add(c)
                    return c
            time.sleep(1)
        raise AssertionError("no unused authenticator code became available")


def _enroll(http: httpx.Client, u: dict) -> Authenticator:
    login = http.post("/api/v1/auth/login", json=u).json()["data"]
    auth = _auth(login["access_token"])
    secret = http.post("/api/v1/auth/mfa/totp/setup", headers=auth).json()["data"]["secret"]
    r = http.post(
        "/api/v1/auth/mfa/totp/verify", json={"code": pyotp.TOTP(secret).now()}, headers=auth
    )
    assert r.status_code == 200, r.text
    return Authenticator(secret)


def _sign_in(http: httpx.Client, u: dict, app: Authenticator) -> dict:
    gate = http.post("/api/v1/auth/login", json=u).json()["data"]
    r = http.post(
        "/api/v1/auth/mfa/challenge",
        json={"mfa_ticket": gate["mfa_ticket"], "code": app.totp.now()},
    )
    assert r.status_code == 200, r.text
    return _auth(r.json()["data"]["access_token"])


def _login(http: httpx.Client, u: dict) -> dict:
    return _auth(http.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])


def _onboard(c: httpx.Client, auth: dict, name: str) -> str:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Obi"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": name})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
    )
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Get customers"]})
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
    return c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]


def _next(weekday: int, after_days: int = 3) -> date:
    d = date.today() + timedelta(days=after_days)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


def test_marketplace_listings_journey(
    http: httpx.Client, base_url, make_verified_user, mailbox, capture, capture_json
):
    # ---- staff ---------------------------------------------------------------------------------
    sa_user = make_verified_user(http)
    sa_app = _enroll(http, sa_user)
    run = subprocess.run(
        [sys.executable, "-m", "app.scripts.create_super_admin", sa_user["email"]],
        capture_output=True,
        text=True,
        env=os.environ,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    sa = _sign_in(http, sa_user, sa_app)
    mod_user, sup_user = make_verified_user(http), make_verified_user(http)
    apps, heads = {}, {}
    for key, user, scopes in (("mod", mod_user, ["moderation"]), ("sup", sup_user, ["support"])):
        apps[key] = _enroll(http, user)
        r = http.post(
            f"{S}/staff",
            json={
                "email": user["email"],
                "role": "admin",
                "scopes": scopes,
                "reason": "Marketplace review rota",
                "totp_code": sa_app.fresh(),
            },
            headers=sa,
        )
        assert r.status_code == 201, r.text
        heads[key] = _sign_in(http, user, apps[key])
    mod, sup, mod_app = heads["mod"], heads["sup"], apps["mod"]

    def step_up(extra: dict | None = None) -> dict:
        return {
            "reason": "Reviewed against the marketplace rules",
            "totp_code": mod_app.fresh(),
            **(extra or {}),
        }

    # ---- an approved Legal expert (slice A flow, compressed) -----------------------------------------
    expert = make_verified_user(http)
    r = http.post(
        f"{A}/providers/invite",
        json={"email": expert["email"], "category": "Legal", "display_name": "Ngozi Eze"},
        headers=mod,
    )
    pid = r.json()["data"]["provider"]["id"]
    token = mailbox.latest_token_for(expert["email"], subject_contains="invited")
    e = _login(http, expert)
    assert (
        http.post(
            f"{P}/invites/accept", json={"token": token, "accept_terms": True}, headers=e
        ).status_code
        == 200
    )
    http.patch(
        f"{P}/me",
        json={
            "display_name": "Ngozi Eze",
            "headline": "Corporate lawyer for early-stage startups",
            "bio": BIO,
            "category": "Legal",
            "languages": ["English", "Igbo"],
            "timezone": "Africa/Lagos",
            "location": "Lagos, Nigeria",
        },
        headers=e,
    )
    http.post(
        f"{P}/files",
        data={"purpose": "credential"},
        files={"file": ("bar-licence.pdf", PDF, "application/pdf")},
        headers=e,
    )
    assert http.post(f"{P}/submit", headers=e).status_code == 200
    r = http.put(
        f"{A}/providers/{pid}/vetting",
        json={
            "checks": {
                "id": {"done": True},
                "licence": {
                    "done": True,
                    "expires_on": (date.today() + timedelta(days=365)).isoformat(),
                },
                "references": {"done": True, "count": 2},
            }
        },
        headers=mod,
    )
    assert r.status_code == 200, r.text
    r = http.post(f"{A}/providers/{pid}/approve", json=step_up(), headers=mod)
    assert r.status_code == 200 and r.json()["data"]["status"] == "approved", r.text
    # an avatar, so founders get a picture
    r = http.post(
        f"{P}/files",
        data={"purpose": "avatar"},
        files={"file": ("me.png", PNG, "image/png")},
        headers=e,
    )
    avatar_id = r.json()["data"]["id"]
    assert http.patch(f"{P}/me", json={"avatar_file_id": avatar_id}, headers=e).status_code == 200

    # ---- people who browse ---------------------------------------------------------------------
    f_user = make_verified_user(http)
    fc = httpx.Client(base_url=base_url, timeout=15.0)
    f_auth = _login(fc, f_user)
    ws = _onboard(fc, f_auth, "Browse Co")
    f = {**f_auth, "X-Workspace-Id": ws}
    invited = {}
    for role in ("team_member", "mentor"):
        u = make_verified_user(http)
        r = fc.post(
            T + "/invitations", headers=f, json={"invites": [{"email": u["email"], "role": role}]}
        )
        assert r.status_code == 200, r.text
        tok = mailbox.latest_token_for(u["email"], subject_contains="invited")
        c = httpx.Client(base_url=base_url, timeout=15.0)
        a = _login(c, u)
        assert (
            c.post("/api/v1/invitations/accept", headers=a, json={"token": tok}).status_code == 200
        )
        invited[role] = (c, {**a, "X-Workspace-Id": ws})
    tm_c, tm = invited["team_member"]
    men_c, men = invited["mentor"]

    # ---- guards on the expert's listing routes -------------------------------------------------
    stranger = make_verified_user(http)
    s_auth = _login(http, stranger)
    r = http.get(f"{P}/listings", headers=s_auth)
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_A_PROVIDER"
    capture(G, "provider_listings_not_a_provider_403", r)
    r = http.get(f"{P}/listings")
    assert r.status_code == 401
    capture(G, "provider_listings_anonymous_401", r)

    # ---- availability --------------------------------------------------------------------------
    monday, tuesday = _next(0), _next(1)
    r = http.get(f"{P}/availability", headers=e)
    assert r.status_code == 200
    capture(G, "availability_empty_200", r)
    bad = {
        "min_notice_h": 0,
        "buffer_min": 0,
        "rules": [{"weekday": 0, "start": "10:00", "end": "09:00", "slot_min": 30}],
        "exceptions": [],
    }
    r = http.put(f"{P}/availability", json=bad, headers=e)
    assert r.status_code == 422
    capture(G, "availability_end_before_start_422", r)
    r = http.put(
        f"{P}/availability",
        json={**bad, "rules": [{"weekday": 0, "start": "09:00", "end": "10:00", "slot_min": 20}]},
        headers=e,
    )
    assert r.status_code == 422
    capture(G, "availability_bad_slot_422", r)
    r = http.put(
        f"{P}/availability",
        json={
            "min_notice_h": 0,
            "buffer_min": 15,
            "rules": [
                {"weekday": d, "start": "09:00", "end": "13:00", "slot_min": 60} for d in range(5)
            ],
            "exceptions": [
                {"on_date": tuesday.isoformat(), "kind": "blocked", "start": None, "end": None}
            ],
        },
        headers=e,
    )
    assert r.status_code == 200, r.text
    capture(G, "availability_put_200", r)

    # ---- listings: create, validate, submit ----------------------------------------------------
    body = {
        "type": "consultation",
        "title": "Contract review session",
        "summary": "A one hour review of your contract with written notes.",
        "description": DESC,
        "category": "Legal",
        "price_minor": 1_000_000,
        "duration_min": 60,
        "meeting_url": "https://meet.example.com/ngozi-eze",
        "tags": ["contracts", "term sheets"],
    }
    r = http.post(f"{P}/listings", json={**body, "price_minor": "5000"}, headers=e)
    assert r.status_code == 422
    capture(G, "listing_price_as_string_422", r)
    r = http.post(f"{P}/listings", json={**body, "category": "Design"}, headers=e)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CATEGORY_NOT_ALLOWED"
    capture(G, "listing_category_not_allowed_422", r)
    r = http.post(f"{P}/listings", json={**body, "duration_min": 20}, headers=e)
    assert r.status_code == 422
    capture(G, "listing_bad_duration_422", r)
    r = http.post(f"{P}/listings", json=body, headers=e)
    assert r.status_code == 201, r.text
    capture(G, "listing_create_consultation_201", r)
    consult = r.json()["data"]["id"]
    r = http.post(
        f"{P}/listings",
        json={
            "type": "package",
            "title": "Shareholder agreement drafting",
            "summary": "A tailored shareholder agreement for your startup.",
            "category": "Legal",
            "price_minor": 25_000_000,
        },
        headers=e,
    )
    assert r.status_code == 201
    package = r.json()["data"]["id"]
    r = http.post(f"{P}/listings/{package}/submit", headers=e)
    assert r.status_code == 422 and r.json()["error"]["code"] == "SUBMISSION_INCOMPLETE"
    capture(G, "listing_submit_incomplete_422", r)
    r = http.patch(
        f"{P}/listings/{package}",
        json={
            "description": DESC.replace(
                "one hour session reviewing", "tailored agreement drafting"
            ),
            "delivery_days": 7,
            "deliverables": ["Shareholder agreement (Word)", "One round of edits"],
            "revisions_included": 2,
        },
        headers=e,
    )
    assert r.status_code == 200, r.text
    capture(G, "listing_patch_draft_200", r)
    for lid in (consult, package):
        assert http.post(f"{P}/listings/{lid}/submit", headers=e).status_code == 200
    r = http.patch(f"{P}/listings/{consult}", json={"title": "Edited while locked"}, headers=e)
    assert r.status_code == 409 and r.json()["error"]["code"] == "LISTING_LOCKED"
    capture(G, "listing_patch_pending_review_409", r)
    r = http.get(f"{P}/listings", headers=e)
    capture(G, "listings_mine_200", r)

    # ---- staff review --------------------------------------------------------------------------
    r = http.get(f"{A}/listings/counts", headers=mod)
    assert r.status_code == 200
    capture(G, "admin_listing_counts", r)
    r = http.get(f"{A}/listings", params={"queue": "true", "query": "Ngozi"}, headers=mod)
    assert {x["id"] for x in r.json()["data"]["listings"]} == {consult, package}
    capture(G, "admin_listing_queue", r)
    r = http.get(f"{A}/listings/{package}", headers=mod)
    capture(G, "admin_listing_detail_pending", r)
    r = http.get(f"{A}/listings", headers=sup)
    assert r.status_code == 403
    capture(G, "admin_listings_wrong_scope_403", r)
    r = http.post(f"{A}/listings/{consult}/approve", json={"reason": "ok"}, headers=mod)
    assert r.status_code == 422
    capture(G, "admin_listing_approve_reason_too_short_422", r)
    r = http.post(
        f"{A}/listings/{consult}/approve",
        json={"reason": "Scope and price are clear"},
        headers=mod,
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "active"
    capture(G, "admin_listing_approve_200", r)
    capture_json(G, "email_listing_approved", mailbox.latest_for(expert["email"]))
    r = http.post(
        f"{A}/listings/{package}/reject",
        json={"template": "nope", "reason": "Price does not match scope"},
        headers=mod,
    )
    assert r.status_code == 422
    capture(G, "admin_listing_reject_bad_template_422", r)
    r = http.post(
        f"{A}/listings/{package}/reject",
        json={
            "template": "pricing",
            "message": "N250,000 looks too high for the deliverables listed. Please revise.",
            "reason": "Price does not match scope",
        },
        headers=mod,
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "rejected"
    capture(G, "admin_listing_reject_200", r)
    r = http.get(f"{P}/listings/{package}", headers=e)
    assert r.json()["data"]["review_message"].startswith("N250,000")
    capture(G, "listing_mine_rejected_with_message", r)

    # ---- founders browse -----------------------------------------------------------------------
    r = fc.get(f"{M}/providers", headers=f)
    assert r.status_code == 200
    cards = [c for c in r.json()["data"]["providers"] if c["id"] == pid]
    assert len(cards) == 1 and cards[0]["from_price_minor"] == 1_000_000
    capture(G, "directory_providers_200", r)
    r = fc.get(
        f"{M}/providers",
        params={"category": "Legal", "language": "igbo", "sort": "price_asc"},
        headers=f,
    )
    assert pid in [c["id"] for c in r.json()["data"]["providers"]]
    capture(G, "directory_providers_filtered_200", r)
    r = fc.get(f"{M}/providers", params={"category": "Cooking"}, headers=f)
    assert r.status_code == 422
    capture(G, "directory_bad_category_422", r)
    r = fc.get(f"{M}/providers", params={"sort": "vibes"}, headers=f)
    assert r.status_code == 422
    capture(G, "directory_bad_sort_422", r)
    r = fc.get(f"{M}/providers/{pid}", headers=f)
    assert r.status_code == 200 and [x["id"] for x in r.json()["data"]["listings"]] == [consult]
    capture(G, "directory_profile_200", r)
    r = fc.get(f"{M}/listings", params={"q": "Contract review"}, headers=f)
    assert consult in [x["id"] for x in r.json()["data"]["listings"]]
    assert package not in [
        x["id"] for x in fc.get(f"{M}/listings", headers=f).json()["data"]["listings"]
    ]
    capture(G, "directory_listings_200", r)
    r = fc.get(f"{M}/listings/{consult}", headers=f)
    assert r.status_code == 200 and "meet.example.com" not in r.text
    capture(G, "directory_listing_detail_200", r)
    r = fc.get(f"{M}/listings/{package}", headers=f)
    assert r.status_code == 404
    capture(G, "directory_rejected_listing_404", r)

    # slots: Monday 09:00-13:00 Lagos, one-hour slots, a 15-minute buffer irrelevant with no bookings
    r = fc.get(
        f"{M}/listings/{consult}/slots",
        params={"from": monday.isoformat(), "to": (monday + timedelta(days=1)).isoformat()},
        headers=f,
    )
    assert r.status_code == 200, r.text
    slots = r.json()["data"]["slots"]
    assert len(slots) == 4 and slots[0]["starts_at"].startswith(f"{monday.isoformat()}T08:00")
    assert not any(s["starts_at"].startswith(tuesday.isoformat()) for s in slots)  # blocked day
    capture(G, "slots_200", r)
    r = fc.get(
        f"{M}/listings/{consult}/slots",
        params={"from": monday.isoformat(), "to": (monday + timedelta(days=20)).isoformat()},
        headers=f,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "RANGE_TOO_LONG"
    capture(G, "slots_range_too_long_422", r)

    # the avatar is served to founders
    r = fc.get(cards[0]["avatar_url"], headers=f)
    assert r.status_code == 200 and r.content == PNG
    capture_json(
        G,
        "avatar_headers",
        {
            "status": r.status_code,
            "content_type": r.headers["content-type"],
            "x_content_type_options": r.headers["x-content-type-options"],
            "cache_control": r.headers["cache-control"],
        },
    )

    # roles: team member browses, mentor is refused, nobody anonymous
    r = tm_c.get(f"{M}/providers", headers=tm)
    assert r.status_code == 200
    r = men_c.get(f"{M}/providers", headers=men)
    assert r.status_code == 403
    capture(G, "directory_mentor_403", r)
    r = http.get(f"{M}/providers")
    assert r.status_code == 401
    capture(G, "directory_anonymous_401", r)

    # ---- a live edit goes back to review -------------------------------------------------------
    r = http.patch(
        f"{P}/listings/{consult}",
        json={"title": "Contract and term sheet review", "price_minor": 1_200_000},
        headers=e,
    )
    assert r.status_code == 200
    assert r.json()["data"]["title"] == "Contract review session"
    assert r.json()["data"]["price_minor"] == 1_200_000
    capture(G, "listing_live_edit_200", r)
    r = fc.get(f"{M}/listings/{consult}", headers=f)
    assert r.json()["data"]["title"] == "Contract review session"
    assert r.json()["data"]["price_minor"] == 1_200_000
    capture(G, "directory_listing_during_pending_edit", r)
    r = http.get(f"{A}/listings", params={"queue": "true", "query": "Ngozi"}, headers=mod)
    assert [x["id"] for x in r.json()["data"]["listings"]] == [consult]
    capture(G, "admin_listing_queue_revision", r)
    r = http.get(f"{A}/listings/{consult}", headers=mod)
    assert r.json()["data"]["diff"][0]["field"] == "title"
    capture(G, "admin_listing_detail_revision", r)
    r = http.post(
        f"{A}/listings/{consult}/approve", json={"reason": "The edit reads fine"}, headers=mod
    )
    assert r.status_code == 200 and r.json()["data"]["title"] == "Contract and term sheet review"
    capture(G, "admin_listing_approve_revision_200", r)
    r = fc.get(f"{M}/listings/{consult}", headers=f)
    assert r.json()["data"]["title"] == "Contract and term sheet review"

    # ---- pause hides, resume shows --------------------------------------------------------------
    r = http.post(f"{P}/listings/{consult}/pause", headers=e)
    assert r.status_code == 200 and r.json()["data"]["status"] == "paused"
    capture(G, "listing_pause_200", r)
    assert fc.get(f"{M}/listings/{consult}", headers=f).status_code == 404
    r = fc.get(f"{M}/providers/{pid}", headers=f)
    assert r.status_code == 404  # no live listing, no profile
    capture(G, "directory_profile_without_live_listing_404", r)
    assert http.post(f"{P}/listings/{consult}/resume", headers=e).status_code == 200

    # ---- founder reports, staff unlist, relist ---------------------------------------------------
    r = fc.post(
        "/api/v1/reports",
        headers=f,
        json={
            "item_type": "listing",
            "item_id": consult,
            "reason": "other",
            "note": "Misleading price",
        },
    )
    assert r.status_code == 201, r.text
    capture(G, "report_listing_201", r)
    report_id = r.json()["data"]["id"]
    r = fc.post(
        "/api/v1/reports",
        headers=f,
        json={"item_type": "review", "item_id": consult, "reason": "spam"},
    )
    assert r.status_code == 404
    capture(G, "report_review_not_yet_404", r)
    r = http.get(AR, params={"tab": "open"}, headers=mod)
    assert report_id in [x["id"] for x in r.json()["data"]["reports"]]
    capture(G, "admin_reports_queue_with_listing", r)
    r = http.post(f"{AR}/{report_id}/remove", json=step_up(), headers=mod)
    assert r.status_code == 200, r.text
    capture(G, "admin_report_remove_listing_200", r)
    assert fc.get(f"{M}/listings/{consult}", headers=f).status_code == 404
    r = http.get(f"{P}/listings/{consult}", headers=e)
    assert r.json()["data"]["status"] == "unlisted"
    capture(G, "listing_mine_unlisted", r)
    r = http.patch(f"{P}/listings/{consult}", json={"title": "Trying to edit"}, headers=e)
    assert r.status_code == 403 and r.json()["error"]["code"] == "LISTING_UNLISTED"
    capture(G, "listing_patch_unlisted_403", r)
    r = http.post(f"{A}/listings/{consult}/relist", json=step_up(), headers=mod)
    assert r.status_code == 200 and r.json()["data"]["status"] == "active"
    capture(G, "admin_listing_relist_200", r)
    r = http.post(f"{A}/listings/{consult}/unlist", json=step_up(), headers=mod)
    assert r.status_code == 200 and r.json()["data"]["status"] == "unlisted"
    capture(G, "admin_listing_unlist_200", r)
    r = http.post(f"{A}/listings/{consult}/unlist", json=step_up(), headers=mod)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    capture(G, "admin_listing_unlist_twice_409", r)
    assert (
        http.post(f"{A}/listings/{consult}/relist", json=step_up(), headers=mod).status_code == 200
    )

    # ---- suspending the expert hides them everywhere ----------------------------------------------
    r = http.post(f"{A}/providers/{pid}/suspend", json=step_up(), headers=mod)
    assert r.status_code == 200
    r = fc.get(f"{M}/providers/{pid}", headers=f)
    assert r.status_code == 404
    capture(G, "directory_suspended_profile_404", r)
    r = http.get(f"{P}/listings", headers=e)
    assert r.status_code == 403 and r.json()["error"]["code"] == "PROVIDER_NOT_APPROVED"
    capture(G, "provider_listings_suspended_403", r)
    assert pid not in [
        c["id"] for c in fc.get(f"{M}/providers", headers=f).json()["data"]["providers"]
    ]
    r = http.post(f"{A}/providers/{pid}/unsuspend", json=step_up(), headers=mod)
    assert r.status_code == 200
    assert fc.get(f"{M}/providers/{pid}", headers=f).status_code == 200

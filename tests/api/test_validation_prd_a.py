"""Validation Hub PRD slice (a): overview + riskiest nudge, AI experiment draft, hosted smoke-test
pages at /p/{slug} (D-VAL-1) with real visit / CTA / signup capture, server-side verdict,
signup-milestone notification, waitlist, and deletes (gap list rows 2-6, 14)."""

from datetime import UTC, date, datetime

import pytest

from app.core.config import settings
from app.core.rate_limit import limiter
from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from app.db.models.job import Job
from app.db.models.validation import Experiment, SmokeTestEvent
from app.platform.events import event_bus
from app.services.validation import smoke
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/validation"


def _member(db, *, role=MembershipRole.founder, startup=None):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    if startup is None:
        startup = create_startup(db, owner=u, name="NovaPay")
    create_membership(db, u, startup, role=role)
    db.flush()
    h = {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(startup.id),
    }
    return u, startup, h


def _assumption(client, h, statement, risk, status=None):
    body = {"statement": statement, "risk": risk, **({"status": status} if status else {})}
    return client.post(f"{BASE}/assumptions", json=body, headers=h).json()["data"]


def _smoke(client, h, slug="novapay-early", status="live", **config):
    cfg = {
        "headline": "Get paid the same day",
        "subheadline": "Instant settlement for market traders",
        "cta_text": "Join the waitlist",
        "require_email": True,
        "metric_type": "email_signup",
        "target_conversion": 10,
        "slug": slug,
        **config,
    }
    r = client.post(
        f"{BASE}/experiments",
        json={"name": "Same-day settlement", "type": "smoke_test", "config": cfg},
        headers=h,
    )
    assert r.status_code == 201, r.text
    exp = r.json()["data"]
    if status != "draft":
        r = client.patch(f"{BASE}/experiments/{exp['id']}", json={"status": status}, headers=h)
        assert r.status_code == 200, r.text
        exp = r.json()["data"]
    return exp


@pytest.fixture(autouse=True)
def _no_rate_limit():
    limiter.enabled = False
    yield
    limiter.enabled = True


# --- overview + riskiest nudge (rows 2, 3) --------------------------------------------------


def test_overview_counts_and_riskiest_untested(client, db):
    _u, _s, h = _member(db)
    _assumption(client, h, "Traders will pay 0.5%", "medium")
    riskiest = _assumption(client, h, "Banks will partner with us", "high")
    _assumption(client, h, "Sellers hate chargebacks", "high", status="validated")
    _assumption(client, h, "Cash is king", "low", status="invalidated")
    _smoke(client, h)
    r = client.get(f"{BASE}/overview", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["assumptions"] == {"untested": 2, "testing": 0, "validated": 1, "invalidated": 1}
    assert d["live_experiments"] == 1
    assert d["responses_collected"] == 0
    assert d["riskiest_untested"] == {
        "id": riskiest["id"],
        "statement": "Banks will partner with us",
        "risk": "high",
    }


def test_overview_with_nothing_to_test(client, db):
    _u, _s, h = _member(db)
    d = client.get(f"{BASE}/overview", headers=h).json()["data"]
    assert d["riskiest_untested"] is None
    assert d["assumptions"] == {"untested": 0, "testing": 0, "validated": 0, "invalidated": 0}


def test_overview_is_founder_and_team_member_only(client, db):
    _u, s, _h = _member(db)
    _m, _s2, mh = _member(db, role=MembershipRole.mentor, startup=s)
    assert client.get(f"{BASE}/overview", headers=mh).status_code == 403


# --- AI experiment draft (row 3), never auto-saved --------------------------------------------


def test_design_experiment_drafts_without_saving(client, db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    _u, s, h = _member(db)
    a = _assumption(client, h, "Traders will pay 0.5% for same-day settlement", "high")
    r = client.post(f"{BASE}/assumptions/{a['id']}/experiment-draft", headers=h)
    assert r.status_code == 202, r.text
    job = db.get(Job, r.json()["data"]["job_id"])
    from app.worker.handlers.validation import handle_experiment_draft

    handle_experiment_draft(db, job)
    assert job.result["status"] == "ready"
    draft = job.result["draft"]
    assert set(draft) >= {
        "name",
        "headline",
        "subheadline",
        "cta_text",
        "metric_type",
        "target_conversion",
    }
    assert draft["metric_type"] in {"email_signup", "cta_click", "preorder_click"}
    assert db.query(Experiment).filter_by(startup_id=s.id).count() == 0


def test_design_experiment_unknown_assumption_404(client, db):
    _u, _s, h = _member(db)
    r = client.post(
        f"{BASE}/assumptions/00000000-0000-0000-0000-000000000000/experiment-draft", headers=h
    )
    assert r.status_code == 404


# --- hosted page: slug + publish (row 4) -----------------------------------------------------


def test_slug_is_unique_across_workspaces_and_validated(client, db):
    _u, _s, h = _member(db)
    _smoke(client, h, slug="novapay-early")
    _u2, _s2, h2 = _member(db)
    r = client.post(
        f"{BASE}/experiments",
        json={"name": "Copy", "type": "smoke_test", "config": {"slug": "novapay-early"}},
        headers=h2,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "SLUG_TAKEN"
    for bad in ("No Spaces", "-dash", "a", "admin"):
        r = client.post(
            f"{BASE}/experiments",
            json={"name": "Bad", "type": "smoke_test", "config": {"slug": bad}},
            headers=h2,
        )
        assert r.status_code == 422, (bad, r.text)


def test_a_released_slug_stays_with_its_workspace(client, db):
    """Security review: a deleted or renamed test must not let another workspace take over its
    public URL (and the emails of anyone still following an old link)."""
    _u, _s, h = _member(db)
    exp = _smoke(client, h, slug="keep-mine")
    assert client.delete(f"{BASE}/experiments/{exp['id']}", headers=h).status_code == 200
    _u2, _s2, h2 = _member(db)
    r = client.post(
        f"{BASE}/experiments",
        json={"name": "Grab", "type": "smoke_test", "config": {"slug": "keep-mine"}},
        headers=h2,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "SLUG_TAKEN"
    again = _smoke(client, h, slug="keep-mine")  # the owner can reuse it
    assert again["slug"] == "keep-mine"


def test_events_need_a_visitor_id(client, db):
    _u, _s, h = _member(db)
    _smoke(client, h, slug="needs-id")
    assert client.post(f"{BASE}/pages/needs-id/events", json={"kind": "visit"}).status_code == 422
    assert (
        client.post(
            f"{BASE}/pages/needs-id/events", json={"kind": "visit", "visitor_id": "short"}
        ).status_code
        == 422
    )


def test_public_page_shows_only_the_page_and_only_when_live(client, db):
    _u, _s, h = _member(db)
    exp = _smoke(client, h, slug="trader-pay", status="draft")
    assert client.get(f"{BASE}/pages/trader-pay").status_code == 404
    client.patch(f"{BASE}/experiments/{exp['id']}", json={"status": "live"}, headers=h)
    r = client.get(f"{BASE}/pages/trader-pay")
    assert r.status_code == 200, r.text
    page = r.json()["data"]
    assert page == {
        "headline": "Get paid the same day",
        "subheadline": "Instant settlement for market traders",
        "cta_text": "Join the waitlist",
        "require_email": True,
        "show_pricing": False,
        "metric_type": "email_signup",
        "post_signup_message": "You're on the list, we'll be in touch soon.",
        "powered_by": "Powered by Cofoundaz",
    }
    client.patch(f"{BASE}/experiments/{exp['id']}", json={"status": "ended"}, headers=h)
    assert client.get(f"{BASE}/pages/trader-pay").status_code == 404
    assert client.get(f"{BASE}/pages/nope").status_code == 404


def test_visits_clicks_and_signups_are_captured(client, db):
    _u, s, h = _member(db)
    exp = _smoke(client, h, slug="trader-pay")
    for visitor in ("visitor-1", "visitor-1", "visitor-2", "visitor-3"):
        r = client.post(
            f"{BASE}/pages/trader-pay/events", json={"kind": "visit", "visitor_id": visitor}
        )
        assert r.status_code == 201, r.text
    client.post(
        f"{BASE}/pages/trader-pay/events", json={"kind": "cta_click", "visitor_id": "visitor-2"}
    )
    r = client.post(
        f"{BASE}/pages/trader-pay/signups",
        json={"email": "Ada@Example.com", "visitor_id": "visitor-2"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["data"] == {"message": "You're on the list, we'll be in touch soon."}
    again = client.post(f"{BASE}/pages/trader-pay/signups", json={"email": "ada@example.com"})
    assert again.status_code == 201  # idempotent, same answer, no duplicate
    assert (
        client.post(f"{BASE}/pages/trader-pay/signups", json={"email": "nope"}).status_code == 422
    )
    assert client.post(f"{BASE}/pages/trader-pay/events", json={"kind": "hack"}).status_code == 422

    stats = client.get(f"{BASE}/smoke-tests/{exp['id']}/stats", headers=h).json()["data"]
    assert stats["visits"] == 3  # distinct visitors
    assert stats["signups"] == 1
    assert stats["cta_clicks"] == 1
    assert stats["conversion"] == pytest.approx(33.3)
    assert db.query(SmokeTestEvent).filter_by(startup_id=s.id, kind="signup").count() == 1


def test_custom_post_signup_message(client, db):
    _u, _s, h = _member(db)
    _smoke(client, h, slug="custom-msg", post_signup_message="Thanks! Talk soon.")
    r = client.post(f"{BASE}/pages/custom-msg/signups", json={"email": "x@example.com"})
    assert r.json()["data"]["message"] == "Thanks! Talk soon."


def test_waitlist_is_members_only(client, db):
    _u, s, h = _member(db)
    exp = _smoke(client, h, slug="trader-pay")
    client.post(f"{BASE}/pages/trader-pay/signups", json={"email": "a@example.com"})
    r = client.get(f"{BASE}/smoke-tests/{exp['id']}/signups", headers=h)
    assert r.status_code == 200
    rows = r.json()["data"]["signups"]
    assert [x["email"] for x in rows] == ["a@example.com"] and rows[0]["signed_up_at"]
    _m, _s2, mh = _member(db, role=MembershipRole.mentor, startup=s)
    assert client.get(f"{BASE}/smoke-tests/{exp['id']}/signups", headers=mh).status_code == 403


# --- verdict (row 5) --------------------------------------------------------------------------


def test_verdict_only_once_the_sample_is_big_enough(client, db, monkeypatch):
    monkeypatch.setattr(smoke, "VERDICT_MIN_VISITS", 4)
    _u, _s, h = _member(db)
    exp = _smoke(client, h, slug="verdict", target_conversion=30)
    for v in ("visitor-a", "visitor-b", "visitor-c"):
        client.post(f"{BASE}/pages/verdict/events", json={"kind": "visit", "visitor_id": v})
    client.post(
        f"{BASE}/pages/verdict/signups", json={"email": "a@example.com", "visitor_id": "visitor-a"}
    )
    stats = client.get(f"{BASE}/smoke-tests/{exp['id']}/stats", headers=h).json()["data"]
    assert stats["verdict"] is None and stats["sample_needed"] == 4
    client.post(f"{BASE}/pages/verdict/events", json={"kind": "visit", "visitor_id": "visitor-d"})
    client.post(
        f"{BASE}/pages/verdict/signups", json={"email": "d@example.com", "visitor_id": "visitor-d"}
    )
    stats = client.get(f"{BASE}/smoke-tests/{exp['id']}/stats", headers=h).json()["data"]
    assert stats["conversion"] == 50.0 and stats["target_conversion"] == 30
    assert stats["verdict"] == "validated"


def test_cta_click_metric_drives_conversion(client, db, monkeypatch):
    monkeypatch.setattr(smoke, "VERDICT_MIN_VISITS", 2)
    _u, _s, h = _member(db)
    exp = _smoke(client, h, slug="clicky", metric_type="cta_click", target_conversion=80)
    for v in ("visitor-a", "visitor-b"):
        client.post(f"{BASE}/pages/clicky/events", json={"kind": "visit", "visitor_id": v})
    client.post(
        f"{BASE}/pages/clicky/events", json={"kind": "cta_click", "visitor_id": "visitor-a"}
    )
    stats = client.get(f"{BASE}/smoke-tests/{exp['id']}/stats", headers=h).json()["data"]
    assert stats["conversion"] == 50.0 and stats["verdict"] == "weak"


def test_manual_numbers_still_count(client, db):
    _u, _s, h = _member(db)
    exp = client.post(
        f"{BASE}/experiments",
        json={"name": "Offline", "type": "smoke_test", "metrics": {"visits": 50, "signups": 5}},
        headers=h,
    ).json()["data"]
    stats = client.get(f"{BASE}/smoke-tests/{exp['id']}/stats", headers=h).json()["data"]
    assert stats["visits"] == 50 and stats["signups"] == 5 and stats["conversion"] == 10.0


# --- signup milestone (row 6) -----------------------------------------------------------------


def test_signup_milestone_event(client, db, monkeypatch):
    monkeypatch.setattr(smoke, "SIGNUP_MILESTONES", (2, 5))
    published = []
    monkeypatch.setattr(
        event_bus, "publish", lambda _db, name, payload: published.append((name, payload))
    )
    _u, _s, h = _member(db)
    _smoke(client, h, slug="miles")
    for i in range(3):
        client.post(f"{BASE}/pages/miles/signups", json={"email": f"p{i}@example.com"})
    client.post(f"{BASE}/pages/miles/signups", json={"email": "p0@example.com"})  # repeat
    milestones = [p for n, p in published if n == "validation.smoke_test.milestone"]
    assert [m["signups"] for m in milestones] == [2]
    assert milestones[0]["name"] == "Same-day settlement"


def test_milestone_notification_copy():
    from app.services.notifications.registry import SPECS

    spec = SPECS["validation.smoke_test.milestone"]
    assert spec.title({"name": "Same-day settlement", "signups": 25}) == (
        "Same-day settlement just passed 25 signups"
    )


# --- deletes (row 14) -------------------------------------------------------------------------


def test_deletes_clean_up_links_and_events(client, db):
    _u, s, h = _member(db)
    a = _assumption(client, h, "Traders will pay", "high")
    exp = _smoke(client, h, slug="del-me")
    client.patch(f"{BASE}/experiments/{exp['id']}", json={"assumption_ids": [a["id"]]}, headers=h)
    iv = client.post(
        f"{BASE}/interviews",
        json={
            "interviewee": "Funke",
            "held_on": str(date(2026, 10, 15)),
            "verdict": "supports",
            "assumption_ids": [a["id"]],
        },
        headers=h,
    ).json()["data"]
    client.post(f"{BASE}/pages/del-me/signups", json={"email": "x@example.com"})

    assert client.delete(f"{BASE}/assumptions/{a['id']}", headers=h).status_code == 200
    assert (
        client.get(f"{BASE}/interviews", headers=h).json()["data"]["interviews"][0][
            "assumption_ids"
        ]
        == []
    )
    assert client.delete(f"{BASE}/experiments/{exp['id']}", headers=h).status_code == 200
    assert db.query(SmokeTestEvent).filter_by(startup_id=s.id).count() == 0
    assert client.get(f"{BASE}/pages/del-me").status_code == 404
    assert client.delete(f"{BASE}/interviews/{iv['id']}", headers=h).status_code == 200
    survey = client.post(f"{BASE}/surveys", json={"title": "Pricing"}, headers=h).json()["data"]
    assert client.delete(f"{BASE}/surveys/{survey['id']}", headers=h).status_code == 200
    assert client.get(f"{BASE}/surveys", headers=h).json()["data"]["surveys"] == []


def test_deletes_are_tenant_scoped(client, db):
    _u, _s, h = _member(db)
    a = _assumption(client, h, "Mine", "low")
    _u2, _s2, h2 = _member(db)
    assert client.delete(f"{BASE}/assumptions/{a['id']}", headers=h2).status_code == 404


# --- public routes carry rate limits ----------------------------------------------------------


def test_public_page_routes_are_rate_limited():
    registered = " ".join(str(key) for key in limiter._route_limits)
    for name in ("public_page_endpoint", "page_event_endpoint", "page_signup_endpoint"):
        assert name in registered

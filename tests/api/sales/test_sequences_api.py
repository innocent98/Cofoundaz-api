import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.routing import APIRoute

from app.api.v1.endpoints import sales_sequences as seq_endpoints
from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from app.db.models.job import Job
from app.db.models.sales_sequences import SalesDraft, SequenceEnrollment
from app.platform import llm_budget
from app.services.sales import sending as sending_svc
from app.worker.handlers.sales_outreach import handle_draft, handle_send_tick, normalize_draft
from tests.factories import create_membership, create_startup, create_user
from tests.outreach_fakes import FakeSender
from tests.sales_outreach_helpers import NOW, configure, contact

BASE = "/api/v1/sales"
PUBLIC = "/api/v1/public/sales/unsubscribe"
_ID = str(uuid.uuid4())
OTHER_ROLES = [MembershipRole.mentor, MembershipRole.business_consultant, MembershipRole.accountant]


def _h(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _member(db, *, role=MembershipRole.founder, startup=None, name="Acme"):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    u.profile.full_name = "Fiona Founder"
    if startup is None:
        startup = create_startup(db, owner=u, name=name)
    create_membership(db, u, startup, role=role)
    db.commit()
    return u, startup, _h(u, startup)


STEPS = [
    {"wait_days": 0, "subject": "Hi {{first_name}}", "body": "Hello {{first_name}} at {{company}}"},
    {"wait_days": 3, "subject": "Re: Hi", "body": "Following up, {{sender_name}}"},
]


def _create(client, h, **over):
    body = {"name": "Cold intro", "steps": STEPS, "stop_on_stage_change": False, **over}
    return client.post(f"{BASE}/sequences", headers=h, json=body)


def test_create_list_get_update_archive(client, db):
    u, s, h = _member(db)
    r = _create(client, h)
    assert r.status_code == 201, r.text
    seq = r.json()["data"]
    assert seq["status"] == "active" and seq["step_count"] == 2 and seq["active_enrollments"] == 0
    sid = seq["id"]
    lst = client.get(f"{BASE}/sequences", headers=h).json()["data"]
    assert lst["total"] == 1 and lst["items"][0]["id"] == sid
    upd = client.patch(
        f"{BASE}/sequences/{sid}", headers=h, json={"name": "Renamed", "status": "paused"}
    )
    assert upd.json()["data"]["name"] == "Renamed" and upd.json()["data"]["status"] == "paused"
    assert (
        client.patch(f"{BASE}/sequences/{sid}", headers=h, json={"status": "archived"}).status_code
        == 422
    )
    archived = client.delete(f"{BASE}/sequences/{sid}", headers=h)
    assert archived.json()["data"]["archived"] is True
    assert client.get(f"{BASE}/sequences", headers=h).json()["data"]["total"] == 0
    assert (
        client.get(f"{BASE}/sequences?include_archived=true", headers=h).json()["data"]["total"]
        == 1
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"steps": []},
        {"steps": [{"wait_days": -1, "subject": "s", "body": "b"}]},
        {"steps": [{"wait_days": 0, "subject": "Hi {{nope}}", "body": "b"}]},
        {"steps": [{"wait_days": 0, "subject": "s", "body": "   "}]},
        {"name": "  "},
    ],
)
def test_validation_errors_are_422(client, db, bad):
    _, _, h = _member(db)
    assert _create(client, h, **bad).status_code == 422


def test_enroll_list_replied_stop_and_cross_tenant(client, db):
    u, s, h = _member(db)
    ou, os_, oh = _member(db)
    sid = _create(client, h).json()["data"]["id"]
    c1, c2 = contact(db, s, email="a@prospect.test"), contact(db, s, email="b@prospect.test")
    r = client.post(
        f"{BASE}/sequences/{sid}/enroll", headers=h, json={"contact_ids": [str(c1.id), str(c2.id)]}
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["enrolled"] == 2 and data["skipped"] == []
    assert data["enrollments"][0]["sender_email"] == u.email
    again = client.post(
        f"{BASE}/sequences/{sid}/enroll", headers=h, json={"contact_ids": [str(c1.id)]}
    )
    assert again.json()["data"]["skipped"] == [{"id": str(c1.id), "reason": "already_active"}]
    en = client.get(f"{BASE}/sequences/{sid}/enrollments", headers=h).json()["data"]
    assert en["total"] == 2
    e1, e2 = en["items"][0]["id"], en["items"][1]["id"]
    assert (
        client.post(f"{BASE}/enrollments/{e1}/replied", headers=h).json()["data"]["status"]
        == "replied"
    )
    assert client.post(f"{BASE}/enrollments/{e1}/replied", headers=h).status_code == 409
    assert (
        client.post(f"{BASE}/enrollments/{e2}/stop", headers=h).json()["data"]["status"]
        == "stopped"
    )
    only = client.get(f"{BASE}/sequences/{sid}/enrollments?status=replied", headers=h).json()[
        "data"
    ]
    assert only["total"] == 1
    # another workspace sees nothing of this
    assert client.get(f"{BASE}/sequences/{sid}", headers=oh).status_code == 404
    assert client.post(f"{BASE}/enrollments/{e1}/stop", headers=oh).status_code == 404
    assert client.get(f"{BASE}/sequences/{sid}/enrollments", headers=oh).status_code == 404
    assert (
        client.post(
            f"{BASE}/sequences/{sid}/enroll", headers=oh, json={"contact_ids": [str(c1.id)]}
        ).status_code
        == 404
    )
    assert client.post(f"{BASE}/sequences/{sid}/enroll", headers=h, json={}).status_code == 422


def test_sequences_save_and_enroll_when_sending_is_not_configured(client, db, monkeypatch):
    monkeypatch.setattr(settings, "SALES_OUTREACH_PROVIDER", "")
    u, s, h = _member(db)
    sid = _create(client, h).json()["data"]["id"]  # no 500
    st = client.get(f"{BASE}/sending", headers=h)
    assert st.status_code == 200
    assert st.json()["data"] == {
        "configured": False,
        "reason": "not_configured",
        "domain": None,
        "sent_today": 0,
        "daily_cap": 100,
        "hourly_cap": None,
        "paused": False,
        "paused_reason": None,
        "paused_at": None,
    }
    c = contact(db, s)
    assert (
        client.post(
            f"{BASE}/sequences/{sid}/enroll", headers=h, json={"contact_ids": [str(c.id)]}
        ).status_code
        == 200
    )


def test_sending_status_when_configured_and_resume_is_founder_only(client, db, monkeypatch):
    configure(monkeypatch, hourly=12)
    u, s, h = _member(db)
    tm, _, tmh = _member(db, role=MembershipRole.team_member, startup=s)
    st = client.get(f"{BASE}/sending", headers=tmh).json()["data"]
    assert st["configured"] is True and st["domain"] == "mail.example.test"
    assert st["hourly_cap"] == 12 and st["paused"] is False
    from app.services.sales.stages import get_settings

    row = get_settings(db, s.id)
    row.paused_at, row.paused_reason = NOW, "bounces"
    db.commit()
    assert client.get(f"{BASE}/sending", headers=tmh).json()["data"]["paused"] is True
    assert client.post(f"{BASE}/sending/resume", headers=tmh).status_code == 403
    out = client.post(f"{BASE}/sending/resume", headers=h)
    assert out.status_code == 200 and out.json()["data"]["paused"] is False


def _routes(router):
    out = []
    for route in router.routes:
        assert isinstance(route, APIRoute)
        path = re.sub(r"\{[^}]+\}", _ID, route.path)
        out.extend((m, path) for m in sorted(route.methods - {"HEAD", "OPTIONS"}))
    return out


ROUTES = _routes(seq_endpoints.router)


def test_route_table_is_enumerated():
    assert len(ROUTES) >= 14 and ("GET", "/sending") in ROUTES


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_sequence_route_requires_auth(client, method, path):
    kw = {"json": {}} if method in ("POST", "PUT", "PATCH") else {}
    assert client.request(method, BASE + path, **kw).status_code == 401


@pytest.mark.parametrize("role", OTHER_ROLES)
def test_every_sequence_route_is_403_for_non_sales_roles(client, db, role):
    _, s, _ = _member(db)
    _, _, h = _member(db, role=role, startup=s)
    bad = []
    for method, path in ROUTES:
        kw = {"json": {}} if method in ("POST", "PUT", "PATCH") else {}
        r = client.request(method, BASE + path, headers=h, **kw)
        if r.status_code != 403:
            bad.append((method, path, r.status_code))
    assert bad == []


# ---- AI drafts and follow-up ----
class _LLM:
    last_usage_tokens = 5

    def __init__(self, data):
        self.data = data

    def complete_json(self, messages, *, schema, max_tokens):
        return self.data


def _run_draft(db, monkeypatch, draft_id, data):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 100_000)
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _LLM(data))
    job = db.query(Job).filter(Job.payload["draft_id"].astext == draft_id).one()
    handle_draft(db, job)
    db.commit()


def test_write_with_ai_returns_a_draft_and_appends_nothing(client, db, monkeypatch):
    u, s, h = _member(db)
    sid = _create(client, h).json()["data"]["id"]
    r = client.post(
        f"{BASE}/sequences/{sid}/steps/1/write", headers=h, json={"instructions": "friendly"}
    )
    assert r.status_code == 202 and r.json()["data"]["status"] == "generating"
    did = r.json()["data"]["id"]
    _run_draft(
        db,
        monkeypatch,
        did,
        {"subject": "Quick — follow up {{nope}}", "body": "Hi {{first_name}} – hello {{bogus}}"},
    )
    d = client.get(f"{BASE}/drafts/{did}", headers=h).json()["data"]
    assert d["status"] == "ready" and "—" not in d["subject"] and "–" not in d["body"]
    assert (
        "{{nope}}" not in d["subject"] and "{{first_name}}" in d["body"]
    )  # only known tags survive
    assert client.get(f"{BASE}/sequences/{sid}", headers=h).json()["data"]["steps"] == STEPS
    assert (
        client.post(f"{BASE}/sequences/{sid}/steps/9/write", headers=h, json={}).status_code == 422
    )
    _, _, oh = _member(db)
    assert client.get(f"{BASE}/drafts/{did}", headers=oh).status_code == 404


def test_draft_failure_modes_end_as_failed_not_stuck(client, db, monkeypatch):
    u, s, h = _member(db)
    sid = _create(client, h).json()["data"]["id"]
    did = client.post(f"{BASE}/sequences/{sid}/steps/0/write", headers=h, json={}).json()["data"][
        "id"
    ]
    _run_draft(db, monkeypatch, did, {"subject": "", "body": ""})
    d = client.get(f"{BASE}/drafts/{did}", headers=h).json()["data"]
    assert (d["status"], d["error"]) == ("failed", "bad_output")
    assert normalize_draft({"subject": "a", "body": "b"}) == {"subject": "a", "body": "b"}


def _deal_with_contact(db, s, u, email="buyer@prospect.test"):
    from app.schemas.sales import DealCreate
    from app.services.sales import deals as deals_svc
    from app.services.sales import stages as stages_svc

    stages = {st.name: st for st in stages_svc.ensure_stages(db, s.id)}
    c = contact(db, s, email=email)
    d = deals_svc.create_deal(
        db,
        startup_id=s.id,
        actor_id=u.id,
        data=DealCreate(name="Acme deal", stage_id=stages["Qualified"].id, contact_id=c.id),
    )
    db.commit()
    return d


def test_follow_up_draft_then_send_once_logs_an_email_activity(client, db, monkeypatch):
    configure(monkeypatch)
    fake = FakeSender()
    monkeypatch.setattr(sending_svc, "get_outreach_sender", lambda: fake)
    u, s, h = _member(db)
    deal = _deal_with_contact(db, s, u)
    r = client.post(f"{BASE}/deals/{deal.id}/follow-up", headers=h)
    assert r.status_code == 202
    did = r.json()["data"]["id"]
    assert client.post(f"{BASE}/deals/{deal.id}/follow-up", headers=h).status_code == 409
    sent_early = client.post(
        f"{BASE}/deals/{deal.id}/follow-up/send",
        headers=h,
        json={"draft_id": did, "subject": "s", "body": "b"},
    )
    assert sent_early.status_code == 409 and sent_early.json()["error"]["code"] == "DRAFT_NOT_READY"
    _run_draft(db, monkeypatch, did, {"subject": "Next step", "body": "Can we talk Tuesday?"})
    assert (
        client.get(f"{BASE}/deals/{deal.id}/follow-up", headers=h).json()["data"]["status"]
        == "ready"
    )
    body = {"draft_id": did, "subject": "Next step", "body": "Can we talk Tuesday?"}
    ok = client.post(f"{BASE}/deals/{deal.id}/follow-up/send", headers=h, json=body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["sent"] is True and ok.json()["data"]["to"] == "buyer@prospect.test"
    assert fake.sent[0].reply_to == u.email
    acts = client.get(f"{BASE}/activities?deal_id={deal.id}", headers=h).json()["data"]
    assert any(
        a["kind"] == "email" and a["body"].startswith("Sent follow-up") for a in acts["items"]
    )
    again = client.post(f"{BASE}/deals/{deal.id}/follow-up/send", headers=h, json=body)
    assert again.status_code == 409 and again.json()["error"]["code"] == "DRAFT_ALREADY_SENT"
    # a draft of another deal / workspace cannot be used here
    _, _, oh = _member(db)
    assert (
        client.post(f"{BASE}/deals/{deal.id}/follow-up/send", headers=oh, json=body).status_code
        == 404
    )
    assert db.query(SalesDraft).count() == 1


def test_follow_up_send_says_not_configured_cleanly(client, db, monkeypatch):
    monkeypatch.setattr(settings, "SALES_OUTREACH_PROVIDER", "")
    u, s, h = _member(db)
    deal = _deal_with_contact(db, s, u)
    did = client.post(f"{BASE}/deals/{deal.id}/follow-up", headers=h).json()["data"]["id"]
    _run_draft(db, monkeypatch, did, {"subject": "x", "body": "y"})
    r = client.post(
        f"{BASE}/deals/{deal.id}/follow-up/send",
        headers=h,
        json={"draft_id": did, "subject": "x", "body": "y"},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "SENDING_NOT_CONFIGURED"


# ---- public unsubscribe ----
def test_public_unsubscribe_end_to_end_from_a_real_sent_mail(client, db, monkeypatch):
    configure(monkeypatch)
    u, s, h = _member(db, name="Acme Ltd")
    sid = _create(client, h).json()["data"]["id"]
    c = contact(db, s, email="buyer@prospect.test")
    client.post(f"{BASE}/sequences/{sid}/enroll", headers=h, json={"contact_ids": [str(c.id)]})
    e = db.query(SequenceEnrollment).one()
    e.next_send_at = NOW - timedelta(minutes=1)
    db.commit()
    fake = FakeSender()
    sending_svc.run_send_tick(db, now=NOW, sender=fake)
    msg = fake.sent[0]
    page = re.search(r"Unsubscribe: \S+/unsubscribe/(\S+)", msg.text).group(1)
    api_url = re.search(r"<(.+)>", msg.headers["List-Unsubscribe"]).group(1)
    assert api_url.endswith(
        f"/public/sales/unsubscribe/{page}"
    )  # header and footer carry one token
    got = client.get(f"{PUBLIC}/{page}")  # no auth header at all
    assert got.status_code == 200 and got.json()["data"] == {
        "email": "b***@prospect.test",
        "sender": "Acme Ltd",
        "unsubscribed": False,
    }
    # RFC 8058: the mail client POSTs the form body "List-Unsubscribe=One-Click"
    one = client.post(
        f"{PUBLIC}/{page}",
        content="List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert one.status_code == 200 and one.json()["data"]["unsubscribed"] is True
    assert client.post(f"{PUBLIC}/{page}").status_code == 200  # idempotent
    assert (
        client.get(f"{BASE}/sequences/{sid}/enrollments?status=unsubscribed", headers=h).json()[
            "data"
        ]["total"]
        == 1
    )
    assert client.get(f"{PUBLIC}/{page}x").status_code == 404
    assert client.get(f"{PUBLIC}/garbage").status_code == 404
    assert client.post(f"{PUBLIC}/garbage").status_code == 404


def test_send_tick_job_handler_runs_the_tick(db, monkeypatch):
    configure(monkeypatch)
    sender = FakeSender()
    monkeypatch.setattr(sending_svc, "get_outreach_sender", lambda: sender)
    handle_send_tick(db, Job(type="x", payload={}))  # nothing due: must not raise


def test_list_unsubscribe_get_from_a_mail_client_redirects_to_the_fe_page(client, db, monkeypatch):
    from app.services.sales.tokens import sign_send

    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.test")
    token = sign_send(uuid.uuid4())
    r = client.get(
        f"{PUBLIC}/{token}",
        headers={"Accept": "text/html,application/xhtml+xml"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"https://app.example.test/unsubscribe/{token}"
    # a forged token never redirects, and the FE's own JSON fetch is untouched
    assert (
        client.get(
            f"{PUBLIC}/forged", headers={"Accept": "text/html"}, follow_redirects=False
        ).status_code
        == 404
    )
    assert (
        client.get(f"{PUBLIC}/{token}", headers={"Accept": "application/json"}).status_code == 404
    )  # unknown send

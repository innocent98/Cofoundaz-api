from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import InvitationStatus, MembershipRole
from app.db.models.invitation import Invitation
from app.services.auth.emails import invitation_email
from tests.factories import create_membership, create_startup, create_user

URL = "/api/v1/team/invitations"


def _member(db, *, role=MembershipRole.founder, startup=None, email=None):
    kw = {"email_verified_at": datetime.now(UTC)}
    if email:
        kw["email"] = email
    u = create_user(db, **kw)
    if startup is None:
        startup = create_startup(db, owner=u, name="NovaPay")
    create_membership(db, u, startup, role=role)
    db.flush()
    h = {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(startup.id),
    }
    return u, startup, h


@pytest.fixture
def sent(monkeypatch):
    out = []

    class _Sender:
        def send(self, msg):
            out.append(msg)

    monkeypatch.setattr("app.services.onboarding.invites.get_email_sender", lambda: _Sender())
    return out


def test_founder_invites_after_onboarding_and_email_links_to_invite_page(client, db, sent):
    _u, s, h = _member(db)
    db.commit()
    r = client.post(
        URL,
        json={"invites": [{"email": "mate@example.com", "role": "team_member"}]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"created": ["mate@example.com"], "skipped": [], "undelivered": []}
    inv = db.query(Invitation).filter_by(startup_id=s.id).one()
    assert (inv.role, inv.status) == (MembershipRole.team_member, InvitationStatus.pending)
    (msg,) = sent
    assert msg.subject == "You're invited to join NovaPay on Cofoundaz"
    assert "/invite/" in msg.html and "Accept invitation" in msg.html
    assert "as Team Member" in msg.html


def test_cofounder_joins_as_founder_role(client, db, sent):
    _u, s, h = _member(db)
    db.commit()
    r = client.post(
        URL, json={"invites": [{"email": "co@example.com", "role": "founder"}]}, headers=h
    )
    assert r.status_code == 200, r.text
    assert db.query(Invitation).filter_by(startup_id=s.id).one().role == MembershipRole.founder
    assert "as Co-Founder" in sent[0].html


def test_existing_member_and_pending_invite_are_skipped(client, db, sent):
    _f, s, h = _member(db)
    _member(db, role=MembershipRole.team_member, startup=s, email="already@example.com")
    db.commit()
    body = {
        "invites": [
            {"email": "already@example.com", "role": "mentor"},
            {"email": "new@example.com", "role": "mentor"},
            {"email": "new@example.com", "role": "mentor"},
        ]
    }
    r = client.post(URL, json=body, headers=h)
    assert r.json()["data"] == {
        "created": ["new@example.com"],
        "skipped": ["already@example.com", "new@example.com"],
        "undelivered": [],
    }
    again = client.post(URL, json={"invites": [body["invites"][1]]}, headers=h)
    assert again.json()["data"] == {
        "created": [],
        "skipped": ["new@example.com"],
        "undelivered": [],
    }
    assert len(sent) == 1


@pytest.mark.parametrize(
    "role", [MembershipRole.team_member, MembershipRole.mentor, MembershipRole.investor]
)
def test_only_founders_can_invite(client, db, sent, role):
    _f, s, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=s)
    db.commit()
    r = client.post(
        URL, json={"invites": [{"email": "x@example.com", "role": "mentor"}]}, headers=h
    )
    assert r.status_code == 403
    assert sent == []


@pytest.mark.parametrize(
    "body",
    [
        {"invites": []},
        {"invites": [{"email": "not-an-email", "role": "mentor"}]},
        {"invites": [{"email": "a@example.com", "role": "investor"}]},
        {"invites": [{"email": f"u{i}@example.com", "role": "mentor"} for i in range(21)]},
    ],
)
def test_validation(client, db, sent, body):
    _u, _s, h = _member(db)
    db.commit()
    r = client.post(URL, json=body, headers=h)
    assert r.status_code == 422
    assert sent == []


def test_invitation_email_escapes_user_supplied_names():
    msg = invitation_email(
        "a@example.com",
        "tok123",
        startup_name="<b>Evil</b>",
        inviter_name="<script>x</script>",
        role="mentor",
    )
    assert "<script>" not in msg.html and "<b>Evil</b>" not in msg.html
    assert "&lt;script&gt;" in msg.html
    assert msg.html.count("/invite/tok123") == 3  # button href + fallback href + its text


def test_daily_invite_cap_per_workspace(client, db, sent, monkeypatch):
    monkeypatch.setattr("app.services.onboarding.invites.DAILY_INVITE_CAP", 2)
    _u, _s, h = _member(db)
    db.commit()
    ok = client.post(
        URL,
        json={"invites": [{"email": f"c{i}@example.com", "role": "mentor"} for i in range(2)]},
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    over = client.post(
        URL, json={"invites": [{"email": "c9@example.com", "role": "mentor"}]}, headers=h
    )
    assert over.status_code == 429
    assert over.json()["error"]["code"] == "INVITE_LIMIT_REACHED"
    assert len(sent) == 2


def test_route_is_rate_limited_per_user(client, db, sent):
    from app.main import app

    _u, _s, h = _member(db)
    db.commit()
    app.state.limiter.enabled = True
    app.state.limiter.reset()
    try:
        codes = [
            client.post(
                URL, json={"invites": [{"email": f"r{i}@example.com", "role": "mentor"}]}, headers=h
            ).status_code
            for i in range(11)
        ]
    finally:
        app.state.limiter.enabled = False
        app.state.limiter.reset()
    assert codes[:10] == [200] * 10
    assert codes[10] == 429


def test_one_failed_send_is_reported_and_does_not_roll_back_the_batch(client, db, monkeypatch):
    attempted = []

    class _Flaky:
        def send(self, msg):
            attempted.append(msg.to)
            if msg.to == "bad@example.com":
                raise RuntimeError("provider rejected")

    monkeypatch.setattr("app.services.onboarding.invites.get_email_sender", lambda: _Flaky())
    _u, s, h = _member(db)
    db.commit()
    r = client.post(
        URL,
        json={
            "invites": [
                {"email": "bad@example.com", "role": "mentor"},
                {"email": "good@example.com", "role": "mentor"},
            ]
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["undelivered"] == ["bad@example.com"]
    assert attempted == ["bad@example.com", "good@example.com"]
    # Both invitations were committed before any send, so the cap counts them.
    assert db.query(Invitation).filter_by(startup_id=s.id).count() == 2

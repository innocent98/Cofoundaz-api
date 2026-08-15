import re
from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import InvitationStatus, UserStatus
from app.db.models.invitation import Invitation
from app.platform.email import ConsoleEmailSender
from app.services.onboarding import invites as invites_module
from tests.factories import create_user


def _auth(db):
    u = create_user(db, status=UserStatus.active, email_verified_at=datetime.now(UTC))
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


def _install_recording_sender(monkeypatch: pytest.MonkeyPatch) -> ConsoleEmailSender:
    """`get_email_sender()` returns a *new* ConsoleEmailSender on every call, so a
    shared instance can't be observed via the factory. Patch the invites service
    module's bound reference to always hand back the same instance, so its
    `.sent` list accumulates across calls within a test."""
    sender = ConsoleEmailSender()
    monkeypatch.setattr(invites_module, "get_email_sender", lambda: sender)
    return sender


def test_invites_create_rows_and_email(client, db, monkeypatch):
    sender = _install_recording_sender(monkeypatch)
    u, h = _auth(db)
    db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    r = client.post(
        "/api/v1/onboarding/invites",
        headers=h,
        json={"invites": [{"email": "teammate@x.com", "role": "team_member"}]},
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]["created"]) == 1
    rows = db.query(Invitation).filter(Invitation.email == "teammate@x.com").all()
    assert len(rows) == 1 and rows[0].status == InvitationStatus.pending

    assert len(sender.sent) == 1
    msg = sender.sent[0]
    assert msg.to == "teammate@x.com"
    # The accept flow needs the raw token in-hand — it's only ever surfaced via
    # this email, never persisted in the clear.
    match = re.search(r"<code>(.+?)</code>", msg.html)
    assert match is not None
    raw_token = match.group(1)
    assert raw_token and raw_token != rows[0].token_hash  # only the hash is stored


def test_invites_invalid_role_is_422(client, db):
    u, h = _auth(db)
    db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    r = client.post(
        "/api/v1/onboarding/invites",
        headers=h,
        json={"invites": [{"email": "bad-role@x.com", "role": "not_a_role"}]},
    )
    assert r.status_code == 422, r.text


def test_invites_dedupe_pending(client, db):
    u, h = _auth(db)
    db.commit()
    client.get("/api/v1/onboarding/state", headers=h)
    body = {"invites": [{"email": "dup@x.com", "role": "mentor"}]}
    client.post("/api/v1/onboarding/invites", headers=h, json=body)
    r = client.post("/api/v1/onboarding/invites", headers=h, json=body)  # again
    assert r.status_code == 200
    assert r.json()["data"]["skipped"] == ["dup@x.com"]
    assert db.query(Invitation).filter(Invitation.email == "dup@x.com").count() == 1

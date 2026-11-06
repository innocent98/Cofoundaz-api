"""Gap 22 (optional security email): a reset or a change sends a "password changed" notice."""

from datetime import timedelta

from app.db.models.enums import AuthTokenPurpose
from app.services.auth.sessions import issue_token_pair
from app.services.auth.tokens import issue_auth_token
from tests.auth_helpers import PASSWORD, active_user, bearer, record_emails


def test_reset_sends_a_password_changed_notice(client, db, monkeypatch):
    sender = record_emails(monkeypatch)
    user = active_user(db, "reset-notice@x.com")
    raw = issue_auth_token(db, user, AuthTokenPurpose.password_reset, timedelta(hours=1))
    db.flush()
    r = client.post("/api/v1/auth/password/reset", json={"token": raw, "password": "new-pass-123"})
    assert r.status_code == 200, r.text
    (msg,) = sender.sent
    assert msg.to == "reset-notice@x.com"
    assert msg.subject == "Your Cofoundaz password was changed"


def test_change_sends_a_password_changed_notice(client, db, monkeypatch):
    sender = record_emails(monkeypatch)
    user = active_user(db, "change-notice@x.com")
    _access, refresh = issue_token_pair(db, user)
    db.flush()
    r = client.post(
        "/api/v1/auth/password/change",
        headers=bearer(user),
        json={
            "current_password": PASSWORD,
            "new_password": "another-pass-9",
            "refresh_token": refresh,
        },
    )
    assert r.status_code == 200, r.text
    assert [m.subject for m in sender.sent] == ["Your Cofoundaz password was changed"]


def test_a_failed_notice_never_fails_the_reset(client, db, monkeypatch):
    class Boom:
        def send(self, msg):
            from app.core.errors import EmailDeliveryFailed

            raise EmailDeliveryFailed()

    monkeypatch.setattr("app.api.v1.endpoints.auth.password.get_email_sender", lambda: Boom())
    user = active_user(db, "boom@x.com")
    raw = issue_auth_token(db, user, AuthTokenPurpose.password_reset, timedelta(hours=1))
    db.flush()
    r = client.post("/api/v1/auth/password/reset", json={"token": raw, "password": "new-pass-123"})
    assert r.status_code == 200 and r.json()["data"] == {"reset": True}

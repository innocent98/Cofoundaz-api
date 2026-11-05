"""Gap 21: finishing onboarding sends "Welcome to Cofoundaz — let's build {startup}", once."""

from tests.api.onboarding.test_complete import _fill
from tests.auth_helpers import active_user, bearer, record_emails


def _founder(client, db):
    u = active_user(db, "ada@x.com")
    db.commit()
    h = bearer(u)
    _fill(client, h)
    return h


def test_completing_onboarding_sends_one_welcome_email(client, db, monkeypatch):
    sender = record_emails(monkeypatch)
    h = _founder(client, db)
    r = client.post("/api/v1/onboarding/complete", headers=h)
    assert r.status_code == 200, r.text
    (msg,) = sender.sent
    assert msg.to == "ada@x.com"
    assert msg.subject == "Welcome to Cofoundaz — let's build Cofoundaz"
    assert "/dashboard" in msg.html and "Open your dashboard" in msg.html
    assert "Hi Ada" in msg.html

    client.post("/api/v1/onboarding/complete", headers=h)  # a repeat call
    assert len(sender.sent) == 1


def test_a_failed_welcome_email_does_not_fail_the_completion(client, db, monkeypatch):
    class Boom:
        def send(self, msg):
            raise RuntimeError("provider down")

    monkeypatch.setattr("app.api.v1.endpoints.onboarding.complete.get_email_sender", lambda: Boom())
    h = _founder(client, db)
    r = client.post("/api/v1/onboarding/complete", headers=h)
    assert r.status_code == 200 and r.json()["data"]["completed"] is True


def test_an_incomplete_onboarding_sends_nothing(client, db, monkeypatch):
    sender = record_emails(monkeypatch)
    u = active_user(db, "early@x.com")
    db.commit()
    client.get("/api/v1/onboarding/state", headers=bearer(u))
    assert client.post("/api/v1/onboarding/complete", headers=bearer(u)).status_code == 422
    assert sender.sent == []

"""Gap 7: onboarding invites validate each address and report a result per row, so the FE can
show which rows failed and why. Nothing invalid is stored or emailed."""

from app.db.models.invitation import Invitation
from tests.auth_helpers import active_user, bearer, record_emails

INVITES = "/api/v1/onboarding/invites"


def _founder(client, db):
    u = active_user(db, "founder@x.com")
    db.commit()
    h = bearer(u)
    client.get("/api/v1/onboarding/state", headers=h)
    return u, h


def _rows(r):
    return [(x["email"], x["status"], x["reason"]) for x in r.json()["data"]["results"]]


def test_an_invalid_address_is_reported_and_never_stored_or_emailed(client, db, monkeypatch):
    sender = record_emails(monkeypatch)
    _u, h = _founder(client, db)
    r = client.post(
        INVITES,
        headers=h,
        json={
            "invites": [
                {"email": "nope", "role": "team_member"},
                {"email": "good@x.com", "role": "team_member"},
                {"email": "a@b", "role": "mentor"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _rows(r) == [
        ("nope", "invalid", "invalid_email"),
        ("good@x.com", "created", None),
        ("a@b", "invalid", "invalid_email"),
    ]
    assert r.json()["data"]["created"] == ["good@x.com"]
    assert [m.to for m in sender.sent] == ["good@x.com"]
    assert db.query(Invitation).filter(Invitation.email.in_(["nope", "a@b"])).count() == 0


def test_every_skip_has_a_reason_and_a_message(client, db, monkeypatch):
    record_emails(monkeypatch)
    _u, h = _founder(client, db)
    item = {"email": "dup@x.com", "role": "mentor"}
    first = client.post(INVITES, headers=h, json={"invites": [item]})
    assert _rows(first) == [("dup@x.com", "created", None)]
    r = client.post(
        INVITES,
        headers=h,
        json={
            "invites": [
                {"email": "founder@x.com", "role": "mentor"},
                item,
                {"email": "new@x.com", "role": "mentor"},
                {"email": "NEW@x.com", "role": "mentor"},
            ]
        },
    )
    assert _rows(r) == [
        ("founder@x.com", "skipped", "self_invite"),
        ("dup@x.com", "skipped", "already_invited"),
        ("new@x.com", "created", None),
        ("NEW@x.com", "skipped", "duplicate_in_request"),
    ]
    messages = [x["message"] for x in r.json()["data"]["results"]]
    assert messages[0] == "That's your own email address."
    assert messages[2] is None
    assert all(isinstance(m, str) and m for i, m in enumerate(messages) if i != 2)


def test_the_old_created_and_skipped_lists_are_still_there(client, db, monkeypatch):
    record_emails(monkeypatch)
    _u, h = _founder(client, db)
    r = client.post(
        INVITES, headers=h, json={"invites": [{"email": "ok@x.com", "role": "team_member"}]}
    )
    data = r.json()["data"]
    assert data["created"] == ["ok@x.com"] and data["skipped"] == [] and data["undelivered"] == []

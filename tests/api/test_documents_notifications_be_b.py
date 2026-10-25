"""Row 28: notification copy carries names and titles, the version event reaches exactly the people
who can open the document, and grantees get told."""

import pytest

from app.core.config import settings
from app.db.models.enums import MembershipRole
from app.db.models.notification import Notification
from tests.documents_helpers import headers, new_doc, outsider, team

API = "/api/v1"
M = MembershipRole


@pytest.fixture
def world(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))
    startup, users = team(db)
    users[M.team_member].profile.full_name = "Tola Member"
    users[M.founder].profile.full_name = "Funmi Founder"
    users[M.mentor].profile.full_name = "Ade Mentor"
    db.flush()
    return startup, users, {r: headers(u, startup) for r, u in users.items()}


def _titles(db, user, kind):
    rows = db.query(Notification).filter_by(user_id=user.id, type=kind).all()
    return [(n.title, n.body, n.data) for n in rows]


def _save(client, h, doc, body):
    r = client.put(
        f"{API}/documents/{doc['id']}",
        json={
            "title": doc["title"],
            "sections": [{"heading": "H", "body": body}],
            "version": doc["version"],
        },
        headers=h,
    )
    assert r.status_code == 200
    return r.json()["data"]


def test_version_created_tells_the_owner_not_the_author(client, db, world):
    _s, users, heads = world
    doc = new_doc(client, heads[M.founder], title="Plan")
    _save(client, heads[M.team_member], doc, "edited")
    [(title, body, data)] = _titles(db, users[M.founder], "document.version.created")
    assert title == "New version uploaded: Plan (v2) by Tola Member"
    assert body == "Open document"
    assert data["href"] == f"/documents/{doc['id']}" and data["version"] == 2
    assert _titles(db, users[M.team_member], "document.version.created") == []  # the author
    for role in (M.mentor, M.business_consultant, M.investor, M.accountant, M.legal_advisor):
        assert _titles(db, users[role], "document.version.created") == []  # no grant, no news


def test_creation_and_metadata_changes_send_no_version_notification(client, db, world):
    _s, users, heads = world
    doc = new_doc(client, heads[M.team_member])
    client.patch(
        f"{API}/documents/{doc['id']}", json={"title": "Renamed"}, headers=heads[M.team_member]
    )
    assert db.query(Notification).filter_by(type="document.version.created").count() == 0


def test_version_created_reaches_member_grantees_until_revoked_or_trashed(client, db, world):
    _s, users, heads = world
    doc = new_doc(client, heads[M.founder], title="Plan")
    g = client.post(
        f"{API}/documents/{doc['id']}/grants",
        json={"user_id": str(users[M.mentor].id), "permission": "view"},
        headers=heads[M.founder],
    ).json()["data"]
    outside = outsider(db, email="out@example.com")
    client.post(
        f"{API}/documents/{doc['id']}/grants",
        json={"email": "out@example.com"},
        headers=heads[M.founder],
    )
    doc = _save(client, heads[M.founder], doc, "one")
    [(title, _b, _d)] = _titles(db, users[M.mentor], "document.version.created")
    assert title == "New version uploaded: Plan (v2) by Funmi Founder"
    assert _titles(db, outside, "document.version.created") == []  # no workspace inbox: email only
    client.delete(f"{API}/documents/{doc['id']}/grants/{g['id']}", headers=heads[M.founder])
    _save(client, heads[M.founder], doc, "two")
    assert len(_titles(db, users[M.mentor], "document.version.created")) == 1  # nothing new


def test_the_grantee_is_told_when_a_document_is_shared_with_them(client, db, world):
    _s, users, heads = world
    doc = new_doc(client, heads[M.founder], title="Term sheet")
    client.post(
        f"{API}/documents/{doc['id']}/grants",
        json={"user_id": str(users[M.mentor].id), "permission": "comment"},
        headers=heads[M.founder],
    )
    [(title, body, data)] = _titles(db, users[M.mentor], "document.grant.created")
    assert title == "Funmi Founder shared Term sheet with you" and body == "Open document"
    assert data["href"] == "/documents/shared-with-me"
    # The notification is readable by a mentor through the normal inbox (open type, exact audience).
    inbox = client.get(f"{API}/notifications", headers=heads[M.mentor]).json()["data"]
    assert any(n["type"] == "document.grant.created" for n in inbox["notifications"])
    others = [u for r, u in users.items() if r != M.mentor]
    assert all(_titles(db, u, "document.grant.created") == [] for u in others)


def test_a_grant_to_someone_outside_the_workspace_has_no_inbox_row(client, db, world):
    _s, _users, heads = world
    doc = new_doc(client, heads[M.founder])
    outsider(db, email="out@example.com")
    client.post(
        f"{API}/documents/{doc['id']}/grants",
        json={"email": "out@example.com"},
        headers=heads[M.founder],
    )
    assert db.query(Notification).filter_by(type="document.grant.created").count() == 0


def test_token_share_copy_names_the_sharer_and_the_document(client, db, world):
    _s, users, heads = world
    doc = new_doc(client, heads[M.founder], title="Deck")
    client.post(
        f"{API}/documents/{doc['id']}/shares",
        json={"email": "x@example.com"},
        headers=heads[M.team_member],
    )
    [(title, body, data)] = _titles(db, users[M.founder], "document.shared")
    assert title == "Tola Member shared a document: Deck" and body == "Open document"
    assert data["href"] == f"/documents/{doc['id']}"


def test_signature_copy_has_the_signer_and_document_and_a_link(client, db, world):
    import io

    from app.platform.email import get_email_sender  # noqa: F401 - mail is a no-op in tests

    _s, users, heads = world
    f = client.post(
        f"{API}/documents/files",
        files={"file": ("nda.pdf", io.BytesIO(b"%PDF-1.4 body"), "application/pdf")},
        headers=heads[M.founder],
    ).json()["data"]
    req = client.post(
        f"{API}/documents/files/{f['id']}/signature-requests",
        json={"signers": [{"email": "s1@example.com", "name": "S"}], "title": "NDA with Globex"},
        headers=heads[M.founder],
    ).json()["data"]
    link = req["signer_links"][0]
    token = link.rsplit("/", 1)[1]
    done = client.post(f"{API}/sign/{token}", json={"typed_name": "Sade Signer"})
    assert done.status_code == 200
    [(signed, body, data)] = _titles(db, users[M.founder], "document.signature.signed")
    assert signed == "Sade Signer signed NDA with Globex" and body == "View request"
    assert data["href"] == "/documents/signatures"
    [(complete, _b, _d)] = _titles(db, users[M.founder], "document.signature.completed")
    assert complete == "NDA with Globex is fully signed"

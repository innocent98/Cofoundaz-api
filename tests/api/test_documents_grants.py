"""D-DOC-1 per-person grants: the only widening of the founder + team member rule.

Roles exercised: founder, team member, mentor (a member), consultant (a member with no grant),
a grantee who is not a member, a non-grantee, and a revoked grantee.
"""

from datetime import UTC, datetime

import pytest

from app.db.models.document import DocumentGrant
from app.db.models.enums import MembershipRole
from app.platform.events import event_bus
from app.services.documents.grants import bind_pending_grants
from tests.documents_helpers import headers, new_doc, outsider, team, upload

API = "/api/v1"
M = MembershipRole


@pytest.fixture
def world(db, tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))
    startup, users = team(db)
    heads = {r: headers(u, startup) for r, u in users.items()}
    return startup, users, heads


def _grant(client, h, doc, **body):
    body.setdefault("permission", "view")
    return client.post(f"{API}/documents/{doc['id']}/grants", json=body, headers=h)


def _shared(client, user):
    r = client.get(f"{API}/documents/shared-with-me", headers=headers(user))
    assert r.status_code == 200, r.text
    return r.json()["data"]["documents"]


# ---- the BE-A rule stays intact ------------------------------------------------------------


def test_a_grant_does_not_open_any_editor_route_for_a_mentor(client, world):
    _s, users, heads = world
    doc = new_doc(client, heads[M.founder])
    assert _grant(client, heads[M.founder], doc, user_id=str(users[M.mentor].id)).status_code == 201
    mentor = heads[M.mentor]
    for method, path in [
        ("GET", "/documents"),
        ("GET", f"/documents/{doc['id']}"),
        ("GET", f"/documents/{doc['id']}/versions"),
        ("GET", f"/documents/{doc['id']}/grants"),
        ("GET", f"/documents/{doc['id']}/comments"),
        ("GET", "/documents/folders"),
        ("GET", "/documents/trash"),
        ("GET", "/documents/grants"),
        ("PATCH", f"/documents/{doc['id']}"),
        ("DELETE", f"/documents/{doc['id']}"),
    ]:
        r = client.request(
            method, API + path, json={} if method == "PATCH" else None, headers=mentor
        )
        assert r.status_code == 403, (method, path, r.text)


# ---- grantee by account (a workspace member) ----------------------------------------------


def test_member_grantee_sees_only_the_granted_document(client, world):
    _s, users, heads = world
    granted = new_doc(
        client,
        heads[M.founder],
        title="Granted",
        sections=[{"heading": "H", "body": "secret body"}],
    )
    hidden = new_doc(client, heads[M.founder], title="Hidden")
    g = _grant(
        client, heads[M.founder], granted, user_id=str(users[M.mentor].id), permission="view"
    )
    assert g.status_code == 201
    row = g.json()["data"]
    assert row["permission"] == "view" and row["status"] == "active"
    assert row["grantee"]["user_id"] == str(users[M.mentor].id)

    listing = _shared(client, users[M.mentor])
    assert [d["id"] for d in listing] == [granted["id"]]
    assert set(listing[0]) == {
        "id",
        "title",
        "kind",
        "status",
        "version",
        "updated_at",
        "permission",
        "workspace",
        "shared_by",
        "shared_at",
    }
    full = client.get(
        f"{API}/documents/shared-with-me/{granted['id']}", headers=headers(users[M.mentor])
    )
    assert full.status_code == 200
    assert full.json()["data"]["sections"][0]["body"] == "secret body"
    assert "folder" not in full.json()["data"] and "tags" not in full.json()["data"]
    miss = client.get(
        f"{API}/documents/shared-with-me/{hidden['id']}", headers=headers(users[M.mentor])
    )
    assert miss.status_code == 404


def test_non_grantee_member_and_founder_see_an_empty_list(client, world):
    _s, users, heads = world
    doc = new_doc(client, heads[M.founder])
    _grant(client, heads[M.founder], doc, user_id=str(users[M.mentor].id))
    assert _shared(client, users[M.business_consultant]) == []
    assert _shared(client, users[M.founder]) == []
    other = client.get(
        f"{API}/documents/shared-with-me/{doc['id']}", headers=headers(users[M.business_consultant])
    )
    assert other.status_code == 404


def test_grant_requires_a_verified_account(client, db, world):
    _s, users, heads = world
    doc = new_doc(client, heads[M.founder])
    _grant(client, heads[M.founder], doc, user_id=str(users[M.mentor].id))
    users[M.mentor].email_verified_at = None
    db.flush()
    r = client.get(f"{API}/documents/shared-with-me", headers=headers(users[M.mentor]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


def test_unauthenticated_is_401(client):
    assert client.get(f"{API}/documents/shared-with-me").status_code == 401


# ---- grantee by email ----------------------------------------------------------------------


def test_email_grant_for_someone_with_no_account_binds_when_they_exist(client, db, world):
    _s, _users, heads = world
    doc = new_doc(client, heads[M.founder], title="For Ada")
    g = _grant(client, heads[M.founder], doc, email="  Ada@Example.com ")
    assert g.status_code == 201
    assert g.json()["data"]["status"] == "pending"
    assert g.json()["data"]["grantee"] == {
        "user_id": None,
        "email": "ada@example.com",
        "name": None,
    }
    ada = outsider(db, email="ada@example.com")  # signs up and verifies later
    listing = _shared(client, ada)
    assert [d["id"] for d in listing] == [doc["id"]]
    assert listing[0]["workspace"]["name"] == "Acme"
    bound = db.query(DocumentGrant).one()
    assert bound.grantee_user_id == ada.id
    # An outsider stays "pending" for the owner even once bound (no account probing).
    rows = client.get(f"{API}/documents/{doc['id']}/grants", headers=heads[M.founder]).json()[
        "data"
    ]["grants"]
    assert rows[0]["status"] == "pending" and rows[0]["grantee"]["user_id"] is None


def test_email_grant_to_an_existing_account_binds_immediately(client, db, world):
    _s, _users, heads = world
    doc = new_doc(client, heads[M.founder])
    inv = outsider(db, email="investor@example.com")
    g = _grant(client, heads[M.founder], doc, email="Investor@example.com").json()["data"]
    # Bound in the database, but the response cannot be used to learn that an account exists.
    assert g["status"] == "pending" and g["grantee"] == {
        "user_id": None,
        "email": "investor@example.com",
        "name": None,
    }
    assert db.query(DocumentGrant).one().grantee_user_id == inv.id
    assert [d["id"] for d in _shared(client, inv)] == [doc["id"]]


def test_an_unverified_account_never_binds(client, db, world):
    _s, _users, heads = world
    doc = new_doc(client, heads[M.founder])
    _grant(client, heads[M.founder], doc, email="victim@example.com")
    squatter = outsider(db, email="victim@example.com", verified=False)
    assert bind_pending_grants(db, squatter) == 0
    assert db.query(DocumentGrant).one().grantee_user_id is None
    assert (
        client.get(f"{API}/documents/shared-with-me", headers=headers(squatter)).status_code == 403
    )
    squatter.email_verified_at = datetime.now(UTC)
    db.flush()
    assert bind_pending_grants(db, squatter) == 1


def test_grant_to_an_unverified_existing_account_stays_pending_until_verified(client, db, world):
    _s, _users, heads = world
    doc = new_doc(client, heads[M.founder])
    pending = outsider(db, email="late@example.com", verified=False)
    g = _grant(client, heads[M.founder], doc, email="late@example.com").json()["data"]
    assert g["status"] == "pending"
    pending.email_verified_at = datetime.now(UTC)
    db.flush()
    event_bus.publish(db, "auth.user.verified", {"user_id": str(pending.id)})  # sign-up verify
    assert db.query(DocumentGrant).one().grantee_user_id == pending.id


def test_a_grant_is_not_usable_by_a_different_account(client, db, world):
    _s, _users, heads = world
    doc = new_doc(client, heads[M.founder])
    _grant(client, heads[M.founder], doc, email="ada@example.com")
    eve = outsider(db, email="eve@example.com")
    assert _shared(client, eve) == []
    assert (
        client.get(f"{API}/documents/shared-with-me/{doc['id']}", headers=headers(eve)).status_code
        == 404
    )


# ---- validation ---------------------------------------------------------------------------


def test_grant_validation(client, db, world):
    _s, users, heads = world
    h = heads[M.founder]
    doc = new_doc(client, h)
    assert _grant(client, h, doc).status_code == 422  # neither
    both = _grant(client, h, doc, email="a@example.com", user_id=str(users[M.mentor].id))
    assert both.status_code == 422
    assert _grant(client, h, doc, email="not-an-email").status_code == 422
    stranger = outsider(db)
    out = _grant(client, h, doc, user_id=str(stranger.id))
    assert out.status_code == 422 and "isn't in this workspace" in out.json()["error"]["message"]
    full = _grant(client, h, doc, user_id=str(users[M.team_member].id))
    assert full.status_code == 422 and "full access" in full.json()["error"]["message"]
    by_email = _grant(client, h, doc, email=users[M.founder].email)
    assert by_email.status_code == 422
    bad_perm = client.post(
        f"{API}/documents/{doc['id']}/grants",
        json={"email": "a@example.com", "permission": "edit"},
        headers=h,
    )
    assert bad_perm.status_code == 422  # edit comes later (D-DOC-1)


def test_duplicate_live_grant_conflicts_but_a_revoked_one_can_be_regranted(client, world):
    _s, users, heads = world
    h = heads[M.founder]
    doc = new_doc(client, h)
    first = _grant(client, h, doc, user_id=str(users[M.mentor].id)).json()["data"]
    dup = _grant(client, h, doc, email=users[M.mentor].email.upper())
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "GRANT_EXISTS"
    _resp = client.delete(f"{API}/documents/{doc['id']}/grants/{first['id']}", headers=h)
    assert _resp.status_code == 200
    assert _grant(client, h, doc, user_id=str(users[M.mentor].id)).status_code == 201


# ---- revoke / list / permission --------------------------------------------------------------


def test_revoked_grantee_loses_access_everywhere(client, world):
    _s, users, heads = world
    h = heads[M.founder]
    doc = new_doc(client, h)
    g = _grant(client, h, doc, user_id=str(users[M.mentor].id), permission="comment").json()["data"]
    mentor = headers(users[M.mentor])
    assert (
        client.get(f"{API}/documents/shared-with-me/{doc['id']}", headers=mentor).status_code == 200
    )
    r = client.delete(f"{API}/documents/{doc['id']}/grants/{g['id']}", headers=heads[M.team_member])
    assert r.status_code == 200  # a team member can revoke too
    assert _shared(client, users[M.mentor]) == []
    assert (
        client.get(f"{API}/documents/shared-with-me/{doc['id']}", headers=mentor).status_code == 404
    )
    comments = f"{API}/documents/shared-with-me/{doc['id']}/comments"
    assert client.get(comments, headers=mentor).status_code == 404
    _resp = client.post(comments, json={"body": "hi"}, headers=mentor)
    assert _resp.status_code == 404
    live = client.get(f"{API}/documents/{doc['id']}/grants", headers=h).json()["data"]["grants"]
    assert live == []
    hist = client.get(
        f"{API}/documents/{doc['id']}/grants", params={"include_revoked": True}, headers=h
    )
    assert hist.json()["data"]["grants"][0]["status"] == "revoked"
    again = client.delete(f"{API}/documents/{doc['id']}/grants/{g['id']}", headers=h)
    assert again.status_code == 200  # idempotent
    patch = client.patch(
        f"{API}/documents/{doc['id']}/grants/{g['id']}", json={"permission": "view"}, headers=h
    )
    assert patch.status_code == 404


def test_permission_can_be_changed_and_workspace_grants_list(client, world):
    _s, users, heads = world
    h = heads[M.founder]
    doc = new_doc(client, h)
    g = _grant(client, h, doc, user_id=str(users[M.mentor].id)).json()["data"]
    upd = client.patch(
        f"{API}/documents/{doc['id']}/grants/{g['id']}", json={"permission": "comment"}, headers=h
    )
    assert upd.json()["data"]["permission"] == "comment"
    [row] = client.get(f"{API}/documents/grants", headers=h).json()["data"]["grants"]
    assert row["id"] == g["id"]
    foreign = client.delete(
        f"{API}/documents/{doc['id']}/grants/00000000-0000-0000-0000-000000000000", headers=h
    )
    assert foreign.status_code == 404


def test_grants_cannot_cross_workspaces(client, db, world):
    _s, users, heads = world
    doc = new_doc(client, heads[M.founder])
    _s2, users2 = team(db, name="Other")
    other = headers(users2[M.founder], _s2)
    assert _grant(client, other, doc, email="a@example.com").status_code == 404
    g = _grant(client, heads[M.founder], doc, user_id=str(users[M.mentor].id)).json()["data"]
    _resp = client.delete(f"{API}/documents/{doc['id']}/grants/{g['id']}", headers=other)
    assert _resp.status_code == 404
    assert client.get(f"{API}/documents/grants", headers=other).json()["data"]["grants"] == []


def test_a_grant_on_one_document_gives_nothing_on_another(client, world):
    _s, users, heads = world
    a = new_doc(client, heads[M.founder], title="A")
    b = new_doc(client, heads[M.founder], title="B")
    _grant(client, heads[M.founder], a, user_id=str(users[M.mentor].id))
    got = client.get(f"{API}/documents/shared-with-me/{b['id']}", headers=headers(users[M.mentor]))
    assert got.status_code == 404


# ---- trash ----------------------------------------------------------------------------------


def test_trashed_document_is_invisible_to_grantees_until_restored(client, db, world):
    _s, users, heads = world
    h = heads[M.founder]
    doc = new_doc(client, h)
    _grant(client, h, doc, user_id=str(users[M.mentor].id), permission="comment")
    _grant(client, h, doc, email="ada@example.com")
    ada = outsider(db, email="ada@example.com")
    client.delete(f"{API}/documents/{doc['id']}", headers=h)
    for user in (users[M.mentor], ada):
        assert _shared(client, user) == []
        assert (
            client.get(
                f"{API}/documents/shared-with-me/{doc['id']}", headers=headers(user)
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"{API}/documents/shared-with-me/{doc['id']}/comments", headers=headers(user)
            ).status_code
            == 404
        )
    assert client.get(f"{API}/documents/grants", headers=h).json()["data"]["grants"] == []
    client.post(f"{API}/documents/{doc['id']}/restore", headers=h)
    assert [d["id"] for d in _shared(client, users[M.mentor])] == [doc["id"]]
    assert [d["id"] for d in _shared(client, ada)] == [doc["id"]]


def test_permanent_delete_removes_the_grants(client, db, world):
    _s, users, heads = world
    h = heads[M.founder]
    doc = new_doc(client, h)
    _grant(client, h, doc, user_id=str(users[M.mentor].id))
    client.delete(f"{API}/documents/{doc['id']}", headers=h)
    client.delete(f"{API}/documents/{doc['id']}/permanent", headers=h)
    assert db.query(DocumentGrant).count() == 0
    assert _shared(client, users[M.mentor]) == []


# ---- comments -------------------------------------------------------------------------------


def test_comment_permission_gates_posting_and_owners_see_the_thread(client, world):
    _s, users, heads = world
    h = heads[M.founder]
    doc = new_doc(client, h)
    _grant(client, h, doc, user_id=str(users[M.mentor].id), permission="view")
    _grant(client, h, doc, user_id=str(users[M.business_consultant].id), permission="comment")
    url = f"{API}/documents/shared-with-me/{doc['id']}/comments"
    viewer = client.post(url, json={"body": "can I?"}, headers=headers(users[M.mentor]))
    assert viewer.status_code == 403 and viewer.json()["error"]["code"] == "FORBIDDEN"
    ok = client.post(
        url, json={"body": "  Looks good  "}, headers=headers(users[M.business_consultant])
    )
    assert ok.status_code == 201 and ok.json()["data"]["body"] == "Looks good"
    assert ok.json()["data"]["is_mine"] is True
    # Everyone with access reads the same thread.
    mine = client.get(url, headers=headers(users[M.mentor])).json()["data"]["comments"]
    assert [c["body"] for c in mine] == ["Looks good"] and mine[0]["is_mine"] is False
    owner = client.get(f"{API}/documents/{doc['id']}/comments", headers=h).json()["data"][
        "comments"
    ]
    assert [c["body"] for c in owner] == ["Looks good"]
    reply = client.post(f"{API}/documents/{doc['id']}/comments", json={"body": "Thanks"}, headers=h)
    assert reply.status_code == 201
    thread = client.get(url, headers=headers(users[M.mentor])).json()["data"]["comments"]
    # (Same-transaction inserts share one created_at in tests, so compare as a set.)
    assert {c["body"] for c in thread} == {"Looks good", "Thanks"}
    _resp = client.post(url, json={"body": ""}, headers=headers(users[M.business_consultant]))
    assert _resp.status_code == 422
    _resp = client.post(
        url, json={"body": "x" * 5001}, headers=headers(users[M.business_consultant])
    )
    assert _resp.status_code == 422


def test_comments_on_a_trashed_document_are_404_for_the_owner_too(client, world):
    _s, _users, heads = world
    h = heads[M.founder]
    doc = new_doc(client, h)
    client.delete(f"{API}/documents/{doc['id']}", headers=h)
    assert client.get(f"{API}/documents/{doc['id']}/comments", headers=h).status_code == 404
    _resp = client.post(f"{API}/documents/{doc['id']}/comments", json={"body": "x"}, headers=h)
    assert _resp.status_code == 404


def test_files_are_not_reachable_through_a_grant(client, world):
    _s, users, heads = world
    h = heads[M.founder]
    doc = new_doc(client, h)
    f = upload(client, h)
    _grant(client, h, doc, user_id=str(users[M.mentor].id))
    mentor = headers(users[M.mentor], _s)
    for path in (
        f"/documents/files/{f['id']}",
        f"/documents/files/{f['id']}/download",
        f"/documents/files/{f['id']}/content",
    ):
        assert client.get(API + path, headers=mentor).status_code == 403


def test_revoking_works_while_the_document_is_in_the_trash(client, world):
    _s, users, heads = world
    h = heads[M.founder]
    doc = new_doc(client, h)
    g = _grant(client, h, doc, user_id=str(users[M.mentor].id)).json()["data"]
    client.delete(f"{API}/documents/{doc['id']}", headers=h)
    assert (
        len(client.get(f"{API}/documents/{doc['id']}/grants", headers=h).json()["data"]["grants"])
        == 1
    )
    _resp = client.delete(f"{API}/documents/{doc['id']}/grants/{g['id']}", headers=h)
    assert _resp.status_code == 200
    client.post(f"{API}/documents/{doc['id']}/restore", headers=h)
    assert _shared(client, users[M.mentor]) == []  # it does not come back to life


def test_grantee_comments_carry_no_internal_ids(client, world):
    _s, users, heads = world
    h = heads[M.founder]
    doc = new_doc(client, h)
    _grant(client, h, doc, user_id=str(users[M.mentor].id), permission="comment")
    client.post(f"{API}/documents/{doc['id']}/comments", json={"body": "internal"}, headers=h)
    url = f"{API}/documents/shared-with-me/{doc['id']}/comments"
    made = client.post(url, json={"body": "mine"}, headers=headers(users[M.mentor])).json()["data"]
    listed = client.get(url, headers=headers(users[M.mentor])).json()["data"]["comments"]
    for c in [made, *listed]:
        assert "mention_user_ids" not in c and set(c["author"]) == {"name"}


def test_blank_comment_is_422(client, world):
    _s, users, heads = world
    doc = new_doc(client, heads[M.founder])
    _resp = client.post(
        f"{API}/documents/{doc['id']}/comments", json={"body": "   "}, headers=heads[M.founder]
    )
    assert _resp.status_code == 422


def test_permanent_delete_removes_the_comments_too(client, db, world):
    from app.db.models.comment import Comment

    _s, _users, heads = world
    h = heads[M.founder]
    doc = new_doc(client, h)
    client.post(f"{API}/documents/{doc['id']}/comments", json={"body": "x"}, headers=h)
    assert db.query(Comment).count() == 1
    client.delete(f"{API}/documents/{doc['id']}", headers=h)
    client.delete(f"{API}/documents/{doc['id']}/permanent", headers=h)
    assert db.query(Comment).count() == 0


def test_tag_filter_treats_wildcards_literally(client, world):
    _s, _users, heads = world
    h = heads[M.founder]
    new_doc(client, h, tags=["a"])
    assert (
        client.get(f"{API}/documents", params={"tag": "%"}, headers=h).json()["data"]["documents"]
        == []
    )


def test_team_comment_routes_cannot_touch_document_comments(client, world):
    """A (possibly revoked) grantee is still a workspace member: the open team /comments API must
    not edit, delete or resolve a document comment."""
    _s, users, heads = world
    h = heads[M.founder]
    doc = new_doc(client, h)
    _grant(client, h, doc, user_id=str(users[M.mentor].id), permission="comment")
    made = client.post(
        f"{API}/documents/shared-with-me/{doc['id']}/comments",
        json={"body": "mine"},
        headers=headers(users[M.mentor]),
    ).json()["data"]
    mentor = heads[M.mentor]
    for method, path, body in [
        ("PATCH", f"/comments/{made['id']}", {"body": "edited"}),
        ("DELETE", f"/comments/{made['id']}", None),
        ("POST", f"/comments/{made['id']}/resolve", {}),
    ]:
        r = client.request(method, API + path, json=body, headers=mentor)
        assert r.status_code == 404, (method, r.text)
        r = client.request(method, API + path, json=body, headers=h)
        assert r.status_code == 404, (method, r.text)

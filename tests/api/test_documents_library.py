"""Documents BE-B library core: versions, trash, rename/move/tag, folders, search, summary
fields, the inline content route."""

import io
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.db.models.document import DocumentFile, DocumentVersion
from app.db.models.enums import MembershipRole
from app.platform.events import event_bus
from tests.documents_helpers import headers, new_doc, team, upload

API = "/api/v1"


@pytest.fixture
def ws(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))
    startup, users = team(db)
    return startup, users, {r: headers(u, startup) for r, u in users.items()}


def F(ws):  # noqa: N802 - the founder's headers
    return ws[2][MembershipRole.founder]


# ---- versions ---------------------------------------------------------------------------


def _save(client, h, doc, *, body="B2", title=None):
    r = client.put(
        f"{API}/documents/{doc['id']}",
        json={
            "title": title or doc["title"],
            "sections": [{"heading": "H", "body": body}],
            "status": "draft",
            "folder": None,
            "version": doc["version"],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_create_writes_version_one_and_each_save_adds_one(client, ws):
    h = F(ws)
    doc = new_doc(client, h, sections=[{"heading": "H", "body": "B1"}])
    v2 = _save(client, h, doc, body="B2")
    assert v2["version"] == 2
    out = client.get(f"{API}/documents/{doc['id']}/versions", headers=h).json()["data"]
    assert out["current"] == 2
    assert [v["version"] for v in out["versions"]] == [2, 1]  # newest first
    assert out["versions"][0]["is_current"] and not out["versions"][1]["is_current"]
    assert out["versions"][1]["note"] == "Created"
    assert "sections" not in out["versions"][0]  # list rows carry no content


def test_get_old_version_returns_its_content(client, ws):
    h = F(ws)
    doc = new_doc(client, h, sections=[{"heading": "H", "body": "B1"}])
    _save(client, h, doc, body="B2")
    v1 = client.get(f"{API}/documents/{doc['id']}/versions/1", headers=h).json()["data"]
    assert v1["sections"][0]["body"] == "B1" and v1["version"] == 1
    assert client.get(f"{API}/documents/{doc['id']}/versions/9", headers=h).status_code == 404


def test_restore_creates_a_new_version_and_keeps_history(client, db, ws):
    h = F(ws)
    doc = new_doc(client, h, title="One", sections=[{"heading": "H", "body": "B1"}])
    doc = _save(client, h, doc, body="B2", title="Two")
    restored = client.post(f"{API}/documents/{doc['id']}/versions/1/restore", headers=h)
    assert restored.status_code == 200
    body = restored.json()["data"]
    assert body["version"] == 3 and body["title"] == "One"
    assert body["sections"][0]["body"] == "B1"
    versions = client.get(f"{API}/documents/{doc['id']}/versions", headers=h).json()["data"]
    assert [v["version"] for v in versions["versions"]] == [3, 2, 1]
    assert versions["versions"][0]["note"] == "Restored from version 1"
    # Searchable content follows the restore.
    hit = client.get(f"{API}/documents", params={"q": "B1"}, headers=h).json()["data"]["documents"]
    assert [d["id"] for d in hit] == [doc["id"]]


def test_version_created_event_for_saves_and_restores_not_for_creation(client, ws):
    h = F(ws)
    before = len(event_bus.published)
    doc = new_doc(client, h)
    _save(client, h, doc)
    client.post(f"{API}/documents/{doc['id']}/versions/1/restore", headers=h)
    events = [(e, p) for e, p in event_bus.published[before:] if e == "document.version.created"]
    assert [p["version"] for _e, p in events] == [2, 3]
    assert events[0][1]["href"] == f"/documents/{doc['id']}"


def test_stale_save_still_conflicts_and_adds_no_version(client, db, ws):
    h = F(ws)
    doc = new_doc(client, h)
    _save(client, h, doc)
    stale = client.put(
        f"{API}/documents/{doc['id']}",
        json={"title": "x", "sections": [], "version": 1},
        headers=h,
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "DOCUMENT_VERSION_CONFLICT"
    assert db.query(DocumentVersion).filter_by(document_id=doc["id"]).count() == 2


def test_versions_of_a_trashed_or_foreign_document_are_404(client, db, ws):
    h = F(ws)
    doc = new_doc(client, h)
    client.delete(f"{API}/documents/{doc['id']}", headers=h)
    for path in ("versions", "versions/1"):
        assert client.get(f"{API}/documents/{doc['id']}/{path}", headers=h).status_code == 404
    _resp = client.post(f"{API}/documents/{doc['id']}/versions/1/restore", headers=h)
    assert _resp.status_code == 404
    _s2, _u2, other = _other_workspace(db)
    live = new_doc(client, h)
    assert client.get(f"{API}/documents/{live['id']}/versions", headers=other).status_code == 404


def _other_workspace(db):
    startup, users = team(db, name="Other")
    return startup, users, headers(users[MembershipRole.founder], startup)


# ---- trash ------------------------------------------------------------------------------


def test_delete_moves_to_trash_and_restore_brings_it_back(client, ws):
    h = F(ws)
    doc = new_doc(client, h, title="Gone")
    out = client.delete(f"{API}/documents/{doc['id']}", headers=h)
    assert out.status_code == 200 and out.json()["data"]["deleted"] is True
    assert client.get(f"{API}/documents/{doc['id']}", headers=h).status_code == 404
    assert client.get(f"{API}/documents", headers=h).json()["data"]["documents"] == []
    trash = client.get(f"{API}/documents/trash", headers=h).json()["data"]
    [row] = trash["documents"]
    assert row["id"] == doc["id"] and row["trashed_at"] and row["purges_at"]
    gap = datetime.fromisoformat(row["purges_at"]) - datetime.fromisoformat(row["trashed_at"])
    assert gap == timedelta(days=30)
    back = client.post(f"{API}/documents/{doc['id']}/restore", headers=h)
    assert back.status_code == 200 and back.json()["data"]["trashed_at"] is None
    assert client.get(f"{API}/documents/{doc['id']}", headers=h).status_code == 200


def test_trashed_document_rejects_edits_shares_and_second_trash(client, ws):
    h = F(ws)
    doc = new_doc(client, h)
    client.delete(f"{API}/documents/{doc['id']}", headers=h)
    put = client.put(
        f"{API}/documents/{doc['id']}",
        json={"title": "x", "sections": [], "version": 1},
        headers=h,
    )
    assert put.status_code == 404
    _resp = client.patch(f"{API}/documents/{doc['id']}", json={"title": "x"}, headers=h)
    assert _resp.status_code == 404
    share = client.post(
        f"{API}/documents/{doc['id']}/shares", json={"email": "a@example.com"}, headers=h
    )
    assert share.status_code == 404
    _resp = client.delete(f"{API}/documents/{doc['id']}", headers=h)
    assert _resp.status_code == 404


def test_restore_of_a_live_document_is_404_and_permanent_needs_the_trash(client, ws):
    h = F(ws)
    doc = new_doc(client, h)
    _resp = client.post(f"{API}/documents/{doc['id']}/restore", headers=h)
    assert _resp.status_code == 404
    live = client.delete(f"{API}/documents/{doc['id']}/permanent", headers=h)
    assert live.status_code == 409 and live.json()["error"]["code"] == "NOT_IN_TRASH"
    client.delete(f"{API}/documents/{doc['id']}", headers=h)
    gone = client.delete(f"{API}/documents/{doc['id']}/permanent", headers=h)
    assert gone.status_code == 200
    assert client.get(f"{API}/documents/trash", headers=h).json()["data"]["documents"] == []


def test_token_link_dies_with_the_trash_and_returns_on_restore(client, ws):
    h = F(ws)
    doc = new_doc(client, h, title="Linked")
    share = client.post(
        f"{API}/documents/{doc['id']}/shares", json={"email": "a@example.com"}, headers=h
    ).json()["data"]
    token = share["link"].rsplit("/", 1)[1]
    assert client.get(f"{API}/shared/{token}").status_code == 200
    client.delete(f"{API}/documents/{doc['id']}", headers=h)
    assert client.get(f"{API}/shared/{token}").status_code == 404
    assert client.get(f"{API}/documents/shares", headers=h).json()["data"]["shares"] == []
    client.post(f"{API}/documents/{doc['id']}/restore", headers=h)
    assert client.get(f"{API}/shared/{token}").status_code == 200


def test_public_share_payload_carries_content_only(client, ws):
    h = F(ws)
    doc = new_doc(client, h, tags=["secret-tag"], folder="Private")
    share = client.post(
        f"{API}/documents/{doc['id']}/shares", json={"email": "a@example.com"}, headers=h
    ).json()["data"]
    public = client.get(f"{API}/shared/{share['link'].rsplit('/', 1)[1]}").json()["data"][
        "document"
    ]
    assert set(public) == {
        "kind",
        "title",
        "status",
        "ai_generated",
        "template_key",
        "version",
        "updated_at",
        "sections",
    }


def test_trashed_documents_leave_global_search(client, ws):
    h = F(ws)
    doc = new_doc(client, h, title="Zebra strategy")
    assert client.get(f"{API}/search", params={"q": "zebra"}, headers=h).json()["data"]["results"]
    client.delete(f"{API}/documents/{doc['id']}", headers=h)
    assert not client.get(f"{API}/search", params={"q": "zebra"}, headers=h).json()["data"][
        "results"
    ]


def test_team_member_can_trash_and_restore_but_mentor_cannot(client, ws):
    doc = new_doc(client, F(ws))
    tm, mentor = ws[2][MembershipRole.team_member], ws[2][MembershipRole.mentor]
    _resp = client.delete(f"{API}/documents/{doc['id']}", headers=mentor)
    assert _resp.status_code == 403
    _resp = client.delete(f"{API}/documents/{doc['id']}", headers=tm)
    assert _resp.status_code == 200
    _resp = client.post(f"{API}/documents/{doc['id']}/restore", headers=mentor)
    assert _resp.status_code == 403
    _resp = client.post(f"{API}/documents/{doc['id']}/restore", headers=tm)
    assert _resp.status_code == 200


# ---- files in the trash -------------------------------------------------------------------


def test_file_trash_restore_permanent(client, ws, tmp_path):
    h = F(ws)
    f = upload(client, h)
    assert any(tmp_path.rglob("*.pdf"))
    _resp = client.delete(f"{API}/documents/files/{f['id']}", headers=h)
    assert _resp.status_code == 200
    assert client.get(f"{API}/documents/files/{f['id']}", headers=h).status_code == 404
    assert client.get(f"{API}/documents/files", headers=h).json()["data"]["files"] == []
    assert client.get(f"{API}/documents/files/{f['id']}/download", headers=h).status_code == 404
    assert any(tmp_path.rglob("*.pdf"))  # trash keeps the object
    [row] = client.get(f"{API}/documents/trash", headers=h).json()["data"]["files"]
    assert row["id"] == f["id"] and row["purges_at"]
    _resp = client.post(f"{API}/documents/files/{f['id']}/restore", headers=h)
    assert _resp.status_code == 200
    assert client.get(f"{API}/documents/files/{f['id']}", headers=h).status_code == 200
    live = client.delete(f"{API}/documents/files/{f['id']}/permanent", headers=h)
    assert live.status_code == 409 and live.json()["error"]["code"] == "NOT_IN_TRASH"
    client.delete(f"{API}/documents/files/{f['id']}", headers=h)
    _resp = client.delete(f"{API}/documents/files/{f['id']}/permanent", headers=h)
    assert _resp.status_code == 200
    assert not list(tmp_path.rglob("*.pdf"))  # permanent delete removes the object


def test_issued_download_link_dies_when_the_file_is_trashed(client, ws):
    h = F(ws)
    f = upload(client, h)
    link = client.get(f"{API}/documents/files/{f['id']}/download", headers=h).json()["data"]["url"]
    token = link.split("token=")[1]
    assert client.get(f"{API}/documents/files/download", params={"token": token}).status_code == 200
    client.delete(f"{API}/documents/files/{f['id']}", headers=h)
    assert client.get(f"{API}/documents/files/download", params={"token": token}).status_code == 404


def test_file_with_a_signature_request_cannot_be_trashed(client, ws):
    h = F(ws)
    f = upload(client, h)
    client.post(
        f"{API}/documents/files/{f['id']}/signature-requests",
        json={"signers": [{"email": "s@example.com"}]},
        headers=h,
    )
    r = client.delete(f"{API}/documents/files/{f['id']}", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "FILE_HAS_SIGNATURE_REQUESTS"


# ---- rename / move / tag ------------------------------------------------------------------


def test_patch_renames_tags_moves_and_sets_status_without_a_new_version(client, db, ws):
    h = F(ws)
    doc = new_doc(client, h, title="Old")
    out = client.patch(
        f"{API}/documents/{doc['id']}",
        json={
            "title": " New  name ",
            "tags": ["Legal", "legal", " NDA "],
            "folder": "Legal/NDAs",
            "status": "final",
        },
        headers=h,
    )
    assert out.status_code == 200
    body = out.json()["data"]
    assert body["title"] == "New  name"  # trimmed at the ends, inner spacing kept
    assert body["tags"] == ["Legal", "NDA"] and body["folder"] == "Legal/NDAs"
    assert body["status"] == "final" and body["version"] == 1
    assert db.query(DocumentVersion).filter_by(document_id=doc["id"]).count() == 1
    tree = client.get(f"{API}/documents/folders", headers=h).json()["data"]
    assert {f["path"] for f in tree["folders"]} == {"Legal", "Legal/NDAs"}
    moved_out = client.patch(f"{API}/documents/{doc['id']}", json={"folder": None}, headers=h)
    assert moved_out.json()["data"]["folder"] is None
    kept = client.patch(f"{API}/documents/{doc['id']}", json={"status": "draft"}, headers=h)
    assert kept.json()["data"]["tags"] == ["Legal", "NDA"]  # unsent fields are untouched


@pytest.mark.parametrize(
    "body",
    [
        {"title": "   "},
        {"title": "x" * 201},
        {"tags": ["t"] * 1 + [f"tag{i}" for i in range(10)]},
        {"tags": ["x" * 31]},
        {"folder": "a//b"},
        {"folder": "a/b/c/d/e"},
        {"folder": "x" * 61},
    ],
)
def test_patch_validation(client, ws, body):
    h = F(ws)
    doc = new_doc(client, h)
    r = client.patch(f"{API}/documents/{doc['id']}", json=body, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_patch_file_rename_move_tag(client, ws):
    h = F(ws)
    f = upload(client, h)
    out = client.patch(
        f"{API}/documents/files/{f['id']}",
        json={"filename": "../evil/contract final.pdf", "folder": "Legal", "tags": ["nda"]},
        headers=h,
    ).json()["data"]
    assert out["filename"] == "contract final.pdf"  # a path never survives
    assert out["folder"] == "Legal" and out["tags"] == ["nda"]
    assert out["content_type"] == "application/pdf"
    bad = client.patch(f"{API}/documents/files/{f['id']}", json={"filename": "  "}, headers=h)
    assert bad.status_code == 422
    long = client.patch(f"{API}/documents/files/{f['id']}", json={"filename": "x" * 256}, headers=h)
    assert long.status_code == 422


def test_cross_tenant_patch_is_404(client, db, ws):
    doc = new_doc(client, F(ws))
    _s, _u, other = _other_workspace(db)
    _resp = client.patch(f"{API}/documents/{doc['id']}", json={"title": "x"}, headers=other)
    assert _resp.status_code == 404


# ---- folders ----------------------------------------------------------------------------


def _tree(client, h):
    return client.get(f"{API}/documents/folders", headers=h).json()["data"]


def test_folder_create_nest_duplicate_and_counts(client, ws):
    h = F(ws)
    made = client.post(f"{API}/documents/folders", json={"name": "Legal"}, headers=h)
    assert made.status_code == 201
    legal = made.json()["data"]["folders"][0]
    nested = client.post(
        f"{API}/documents/folders", json={"name": "NDAs", "parent_id": legal["id"]}, headers=h
    )
    assert nested.status_code == 201
    dup = client.post(f"{API}/documents/folders", json={"name": "Legal"}, headers=h)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "FOLDER_EXISTS"
    for bad in ("", "a/b", "x" * 61):
        _resp = client.post(f"{API}/documents/folders", json={"name": bad}, headers=h)
        assert _resp.status_code == 422
    new_doc(client, h, folder="Legal/NDAs")
    upload(client, h, folder="Legal")
    new_doc(client, h)  # uncategorised
    tree = _tree(client, h)
    by = {f["path"]: f for f in tree["folders"]}
    assert by["Legal"]["file_count"] == 1 and by["Legal"]["document_count"] == 0
    assert by["Legal"]["total_count"] == 2 and by["Legal/NDAs"]["total_count"] == 1
    assert by["Legal/NDAs"]["parent_id"] == by["Legal"]["id"]
    assert tree["uncategorized"] == 1 and tree["total"] == 3 and tree["trash"] == 0


def test_using_a_new_folder_path_creates_the_chain(client, ws):
    h = F(ws)
    new_doc(client, h, folder="A/B/C")
    assert {f["path"] for f in _tree(client, h)["folders"]} == {"A", "A/B", "A/B/C"}


def test_rename_repoints_subtree_and_items(client, ws):
    h = F(ws)
    doc = new_doc(client, h, folder="Legal/NDAs")
    f = upload(client, h, folder="Legal")
    legal = next(x for x in _tree(client, h)["folders"] if x["path"] == "Legal")
    r = client.patch(
        f"{API}/documents/folders/{legal['id']}", json={"name": "Contracts"}, headers=h
    )
    assert r.status_code == 200
    assert {x["path"] for x in r.json()["data"]["folders"]} == {"Contracts", "Contracts/NDAs"}
    assert (
        client.get(f"{API}/documents/{doc['id']}", headers=h).json()["data"]["folder"]
        == "Contracts/NDAs"
    )
    assert (
        client.get(f"{API}/documents/files/{f['id']}", headers=h).json()["data"]["folder"]
        == "Contracts"
    )
    clash = client.post(f"{API}/documents/folders", json={"name": "Other"}, headers=h)
    other = next(x for x in clash.json()["data"]["folders"] if x["path"] == "Other")
    taken = client.patch(
        f"{API}/documents/folders/{other['id']}", json={"name": "Contracts"}, headers=h
    )
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "FOLDER_EXISTS"
    same = client.patch(f"{API}/documents/folders/{other['id']}", json={"name": "Other"}, headers=h)
    assert same.status_code == 200
    _resp = client.patch(f"{API}/documents/folders/{other['id']}", json={"name": "a/b"}, headers=h)
    assert _resp.status_code == 422


def test_delete_folder_only_when_empty_and_trashed_items_come_back(client, ws):
    h = F(ws)
    doc = new_doc(client, h, folder="Temp")
    temp = next(x for x in _tree(client, h)["folders"] if x["path"] == "Temp")
    full = client.delete(f"{API}/documents/folders/{temp['id']}", headers=h)
    assert full.status_code == 409 and full.json()["error"]["code"] == "FOLDER_NOT_EMPTY"
    client.delete(f"{API}/documents/{doc['id']}", headers=h)
    assert _tree(client, h)["trash"] == 1
    _resp = client.delete(f"{API}/documents/folders/{temp['id']}", headers=h)
    assert _resp.status_code == 200
    assert _tree(client, h)["folders"] == []
    client.post(f"{API}/documents/{doc['id']}/restore", headers=h)
    assert {x["path"] for x in _tree(client, h)["folders"]} == {"Temp"}  # recreated for the item
    _resp = client.delete(f"{API}/documents/folders/{temp['id']}", headers=h)
    assert _resp.status_code == 404


def test_standard_structure_is_idempotent(client, ws):
    h = F(ws)
    for _ in range(2):
        r = client.post(f"{API}/documents/folders/standard", headers=h)
        assert r.status_code == 201
    names = [f["path"] for f in _tree(client, h)["folders"]]
    assert sorted(names) == sorted(
        ["Corporate", "Financials", "Legal", "Fundraising", "Marketing", "Archive"]
    )


def test_folders_are_per_workspace(client, db, ws):
    new_doc(client, F(ws), folder="Mine")
    _s, _u, other = _other_workspace(db)
    assert _tree(client, other)["folders"] == []
    mine = next(x for x in _tree(client, F(ws))["folders"])
    _resp = client.patch(f"{API}/documents/folders/{mine['id']}", json={"name": "x"}, headers=other)
    assert _resp.status_code == 404
    _resp = client.delete(f"{API}/documents/folders/{mine['id']}", headers=other)
    assert _resp.status_code == 404


# ---- search -----------------------------------------------------------------------------


def _ids(client, h, **params):
    return {
        d["id"]
        for d in client.get(f"{API}/documents", params=params, headers=h).json()["data"][
            "documents"
        ]
    }


def test_search_reads_title_body_and_tags_with_every_term(client, ws):
    h = F(ws)
    a = new_doc(
        client,
        h,
        title="Quarterly report",
        sections=[{"heading": "Revenue", "body": "Executive summary of growth"}],
    )
    b = new_doc(client, h, title="Hiring plan", tags=["people"])
    new_doc(client, h, title="Unrelated")
    assert _ids(client, h, q="executive") == {a["id"]}  # body text, case-insensitive
    assert _ids(client, h, q="EXECUT") == {a["id"]}  # half-typed word
    assert _ids(client, h, q="quarterly growth") == {a["id"]}  # both terms, different places
    assert _ids(client, h, q="quarterly hiring") == set()  # every term must match
    assert _ids(client, h, q="people") == {b["id"]}  # tag
    assert _ids(client, h, q="   ") == _ids(client, h)  # blank means no filter
    assert _ids(client, h, tag="PEOPLE") == {b["id"]}


def test_search_treats_wildcards_literally(client, ws):
    h = F(ws)
    a = new_doc(client, h, title="100% done")
    new_doc(client, h, title="plain")
    assert _ids(client, h, q="%") == {a["id"]}
    assert _ids(client, h, q="_") == set()


def test_search_does_not_match_the_owner_name(client, ws):
    h = F(ws)
    new_doc(client, h, title="Plan")
    assert _ids(client, h, q="You") == set()


def test_file_search_by_name_and_tag(client, ws):
    h = F(ws)
    a = upload(client, h, name="board-deck.pdf")
    b = upload(client, h, name="other.pdf")
    client.patch(f"{API}/documents/files/{b['id']}", json={"tags": ["finance"]}, headers=h)

    def names(**p):
        return {
            x["id"]
            for x in client.get(f"{API}/documents/files", params=p, headers=h).json()["data"][
                "files"
            ]
        }

    assert (
        names(q="board") == {a["id"]}
        and names(q="finance") == {b["id"]}
        and names(tag="finance") == {b["id"]}
    )


# ---- summary fields ---------------------------------------------------------------------


def test_summary_carries_owner_tags_share_count_and_mime(client, db, ws):
    startup, users, heads = ws
    h = heads[MembershipRole.team_member]
    users[MembershipRole.team_member].profile.full_name = "Tola Member"
    db.flush()
    doc = new_doc(client, h, tags=["a"])
    client.post(f"{API}/documents/{doc['id']}/shares", json={"email": "x@example.com"}, headers=h)
    client.post(f"{API}/documents/{doc['id']}/grants", json={"email": "y@example.com"}, headers=h)
    [row] = client.get(f"{API}/documents", headers=F(ws)).json()["data"]["documents"]
    assert row["owner"] == {"id": str(users[MembershipRole.team_member].id), "name": "Tola Member"}
    assert row["tags"] == ["a"] and row["share_count"] == 2
    assert row["mime"] == "application/vnd.cofoundaz.document+json"
    assert "sections" not in row
    f = upload(client, F(ws))
    assert (
        f["owner"]["id"]
        and f["tags"] == []
        and f["share_count"] == 0
        and f["mime"] == "application/pdf"
    )


def test_share_count_ignores_revoked_and_expired(client, db, ws):
    from app.db.models.document import DocumentShare

    h = F(ws)
    doc = new_doc(client, h)
    s1 = client.post(
        f"{API}/documents/{doc['id']}/shares", json={"email": "a@example.com"}, headers=h
    ).json()["data"]
    s2 = client.post(
        f"{API}/documents/{doc['id']}/shares", json={"email": "b@example.com"}, headers=h
    ).json()["data"]
    client.delete(f"{API}/documents/{doc['id']}/shares/{s1['id']}", headers=h)
    db.query(DocumentShare).filter_by(id=s2["id"]).update(
        {"expires_at": datetime.now(UTC) - timedelta(days=1)}
    )
    db.flush()
    [row] = client.get(f"{API}/documents", headers=h).json()["data"]["documents"]
    assert row["share_count"] == 0


# ---- inline content route ---------------------------------------------------------------


def test_content_link_is_inline_with_a_viewer_hint(client, ws):
    h = F(ws)
    f = upload(client, h)
    link = client.get(f"{API}/documents/files/{f['id']}/content", headers=h).json()["data"]
    assert link["viewer"] == "pdf" and link["content_type"] == "application/pdf"
    assert "/documents/files/content?token=" in link["url"]
    got = client.get(
        f"{API}/documents/files/content", params={"token": link["url"].split("token=")[1]}
    )
    assert got.status_code == 200 and got.content == b"%PDF-1.4 body"
    assert got.headers["content-disposition"].startswith("inline;")
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in got.headers["content-security-policy"]
    dl = client.get(f"{API}/documents/files/{f['id']}/download", headers=h).json()["data"]
    assert "viewer" not in dl and "/documents/files/download?token=" in dl["url"]
    attach = client.get(
        f"{API}/documents/files/download", params={"token": dl["url"].split("token=")[1]}
    )
    assert attach.headers["content-disposition"].startswith("attachment;")


@pytest.mark.parametrize(
    ("name", "ct", "body", "viewer"),
    [
        ("a.png", "image/png", b"\x89PNG\r\n\x1a\n0000", "image"),
        ("a.txt", "text/plain", b"hello", "text"),
        ("a.md", "text/markdown", b"# hi", "markdown"),
        ("a.csv", "text/csv", b"a,b\n1,2", "csv"),
    ],
)
def test_viewer_hint_by_type(client, ws, name, ct, body, viewer):
    h = F(ws)
    f = upload(client, h, name=name, ct=ct, body=body)
    assert (
        client.get(f"{API}/documents/files/{f['id']}/content", headers=h).json()["data"]["viewer"]
        == viewer
    )


def test_content_link_refused_for_unclean_trashed_foreign_and_bad_tokens(client, db, ws):
    h = F(ws)
    f = upload(client, h)
    db.query(DocumentFile).filter_by(id=f["id"]).update({"scan_status": "blocked"})
    db.flush()
    r = client.get(f"{API}/documents/files/{f['id']}/content", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "FILE_NOT_CLEAN"
    ok = upload(client, h)
    _s, _u, other = _other_workspace(db)
    assert client.get(f"{API}/documents/files/{ok['id']}/content", headers=other).status_code == 404
    assert client.get(f"{API}/documents/files/content", params={"token": "nope"}).status_code == 404
    client.delete(f"{API}/documents/files/{ok['id']}", headers=h)
    assert client.get(f"{API}/documents/files/{ok['id']}/content", headers=h).status_code == 404


def test_serve_signed_content_of_a_trashed_file_is_404(client, ws):
    h = F(ws)
    f = upload(client, h)
    token = (
        client.get(f"{API}/documents/files/{f['id']}/content", headers=h)
        .json()["data"]["url"]
        .split("token=")[1]
    )
    client.delete(f"{API}/documents/files/{f['id']}", headers=h)
    assert client.get(f"{API}/documents/files/content", params={"token": token}).status_code == 404


def test_upload_with_bad_folder_is_422_and_stores_nothing(client, ws, tmp_path):
    h = F(ws)
    r = client.post(
        f"{API}/documents/files",
        files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
        data={"folder": "a//b"},
        headers=h,
    )
    assert r.status_code == 422
    assert client.get(f"{API}/documents/files", headers=h).json()["data"]["files"] == []

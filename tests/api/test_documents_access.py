"""D-DOC-1: every authenticated Documents route is founder + team member only (rows 1-3).

One matrix, every route x every role. A route added under /documents or /document-templates
without a row here fails `test_matrix_covers_every_documents_route`, so a new endpoint cannot
ship ungated by omission. Denied roles must get the standard 403 envelope and no payload at
all, in particular none of the signer or share-recipient emails.
"""

import io
import uuid
from datetime import UTC, datetime

import pytest

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.enums import DOCUMENT_ROLES, MembershipRole, StartupStage
from app.main import app
from tests.factories import create_membership, create_startup, create_user

SIGNER_EMAIL = "signer-secret@example.com"
SHARE_EMAIL = "recipient-secret@example.com"
GRANT_EMAIL = "grantee-secret@example.com"
DENIED = sorted(set(MembershipRole) - DOCUMENT_ROLES, key=lambda r: r.value)
ALLOWED = sorted(DOCUMENT_ROLES, key=lambda r: r.value)


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


@pytest.fixture
def world(client, db, tmp_path, monkeypatch):
    """A workspace with one member per role and a document, file, share and signature request."""
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))
    founder = create_user(db, email_verified_at=datetime.now(UTC))
    startup = create_startup(db, owner=founder, stage=StartupStage.validation)
    heads = {}
    for role in MembershipRole:
        u = founder if role == MembershipRole.founder else create_user(db)
        u.email_verified_at = datetime.now(UTC)
        create_membership(db, u, startup, role=role)
        heads[role] = _headers(u, startup)
    db.flush()
    h = heads[MembershipRole.founder]
    doc = client.post("/api/v1/documents", json={"title": "Secret plan"}, headers=h).json()["data"]
    file = client.post(
        "/api/v1/documents/files",
        files={"file": ("nda.pdf", io.BytesIO(b"%PDF-1.4 body"), "application/pdf")},
        headers=h,
    ).json()["data"]
    share = client.post(
        f"/api/v1/documents/{doc['id']}/shares",
        json={"email": SHARE_EMAIL, "access_level": "view"},
        headers=h,
    ).json()["data"]
    req = client.post(
        f"/api/v1/documents/files/{file['id']}/signature-requests",
        json={"signers": [{"email": SIGNER_EMAIL, "name": "S"}]},
        headers=h,
    ).json()["data"]
    folder = client.post("/api/v1/documents/folders", json={"name": "Legal"}, headers=h).json()[
        "data"
    ]["folders"][0]
    grant = client.post(
        f"/api/v1/documents/{doc['id']}/grants",
        json={"email": GRANT_EMAIL, "permission": "comment"},
        headers=h,
    ).json()["data"]
    ids = {
        "doc": doc["id"],
        "file": file["id"],
        "share": share["id"],
        "req": req["id"],
        "folder": folder["id"],
        "grant": grant["id"],
        "number": 1,
        "key": "business_plan",
    }
    return heads, ids


# (method, path template, json body, multipart upload?)
ROUTES = [
    ("GET", "/document-templates", None, False),
    ("GET", "/document-templates/{key}", None, False),
    ("GET", "/documents", None, False),
    ("POST", "/documents", {"title": "x"}, False),
    ("GET", "/documents/{doc}", None, False),
    ("PUT", "/documents/{doc}", {"title": "y", "sections": [], "version": 1}, False),
    ("GET", "/documents/files", None, False),
    ("POST", "/documents/files", None, True),
    ("GET", "/documents/files/{file}", None, False),
    ("GET", "/documents/files/{file}/download", None, False),
    ("GET", "/documents/shares", None, False),
    ("GET", "/documents/{doc}/shares", None, False),
    ("POST", "/documents/{doc}/shares", {"email": "z@example.com", "access_level": "view"}, False),
    ("GET", "/documents/signature-requests", None, False),
    ("GET", "/documents/signature-requests/{req}", None, False),
    ("POST", "/documents/signature-requests/{req}/remind", None, False),
    (
        "POST",
        "/documents/files/{file}/signature-requests",
        {"signers": [{"email": "n@example.com"}]},
        False,
    ),
    ("POST", "/documents/signature-requests/{req}/cancel", None, False),
    ("DELETE", "/documents/{doc}/shares/{share}", None, False),
    ("GET", "/documents/files/{file}/content", None, False),
    ("PATCH", "/documents/files/{file}", {"tags": ["a"]}, False),
    ("POST", "/documents/files/{file}/restore", None, False),
    ("DELETE", "/documents/files/{file}/permanent", None, False),
    ("GET", "/documents/folders", None, False),
    ("POST", "/documents/folders", {"name": "Fin"}, False),
    ("POST", "/documents/folders/standard", None, False),
    ("PATCH", "/documents/folders/{folder}", {"name": "Contracts"}, False),
    ("GET", "/documents/trash", None, False),
    ("GET", "/documents/grants", None, False),
    ("GET", "/documents/{doc}/grants", None, False),
    ("POST", "/documents/{doc}/grants", {"email": "q@example.com", "permission": "view"}, False),
    ("PATCH", "/documents/{doc}/grants/{grant}", {"permission": "view"}, False),
    ("GET", "/documents/{doc}/comments", None, False),
    ("POST", "/documents/{doc}/comments", {"body": "note"}, False),
    ("GET", "/documents/{doc}/versions", None, False),
    ("GET", "/documents/{doc}/versions/{number}", None, False),
    ("POST", "/documents/{doc}/versions/{number}/restore", None, False),
    ("POST", "/documents/templates/{key}/instantiate", {}, False),
    ("POST", "/documents/{doc}/export", {"format": "pdf"}, False),
    ("POST", "/documents/{doc}/ai-draft", {}, False),
    ("PATCH", "/documents/{doc}", {"title": "renamed"}, False),
    ("POST", "/documents/{doc}/restore", None, False),
    ("DELETE", "/documents/{doc}/grants/{grant}", None, False),
    ("DELETE", "/documents/folders/{folder}", None, False),
    ("DELETE", "/documents/files/{file}", None, False),
    ("DELETE", "/documents/{doc}/permanent", None, False),
    ("DELETE", "/documents/{doc}", None, False),
]

# Open to any verified account by design: access comes from a live per-person grant, never from
# a workspace role (tests/api/test_documents_grants.py covers these).
GRANTEE_ROUTES = {
    ("GET", "/documents/shared-with-me"),
    ("GET", "/documents/shared-with-me/{doc}"),
    ("GET", "/documents/shared-with-me/{doc}/comments"),
    ("POST", "/documents/shared-with-me/{doc}/comments"),
}


def _call(client, route, ids, headers):
    method, tpl, body, upload = route
    url = "/api/v1" + tpl.format(**ids)
    if upload:
        return client.post(
            url,
            files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            headers=headers,
        )
    return client.request(method, url, json=body, headers=headers)


@pytest.mark.parametrize("role", DENIED, ids=lambda r: r.value)
def test_denied_roles_get_403_on_every_documents_route(client, world, role):
    heads, ids = world
    for route in ROUTES:
        r = _call(client, route, ids, heads[role])
        assert r.status_code == 403, (role.value, route[0], route[1], r.text)
        assert r.json()["error"]["code"] == "FORBIDDEN", (route[0], route[1])
        assert SIGNER_EMAIL not in r.text and SHARE_EMAIL not in r.text, (route[0], route[1])


@pytest.mark.parametrize("role", ALLOWED, ids=lambda r: r.value)
def test_founder_and_team_member_reach_every_documents_route(client, world, role):
    heads, ids = world
    for route in ROUTES:  # ordered so destructive calls come last
        r = _call(client, route, ids, heads[role])
        # Past the role gate: anything but 401/403/5xx (deleting the signed file is a 409).
        assert r.status_code not in (401, 403) and r.status_code < 500, (route, r.text)


def test_matrix_covers_every_documents_route():
    public = {  # the token (or signed link) is the credential
        "/api/v1/shared/{token}",
        "/api/v1/sign/{token}",
        "/api/v1/sign/{token}/file",
        "/api/v1/documents/files/download",
        "/api/v1/documents/files/content",
    }
    path_names = {  # the matrix spells path params with its own names
        "{document_id}": "{doc}",
        "{file_id}": "{file}",
        "{share_id}": "{share}",
        "{request_id}": "{req}",
        "{folder_id}": "{folder}",
        "{grant_id}": "{grant}",
    }
    live = set()
    for path, ops in app.openapi()["paths"].items():
        if ("/documents" not in path and "/document-templates" not in path) or path in public:
            continue
        for old, new in path_names.items():
            path = path.replace(old, new)
        live |= {(m.upper(), path.removeprefix("/api/v1")) for m in ops}
    assert live - GRANTEE_ROUTES == {(m, p) for m, p, *_ in ROUTES}
    assert GRANTEE_ROUTES <= live


def test_public_token_routes_stay_open(client, world):
    """/shared/{token} and /sign/{token} are credentialed by the token, not by a role."""
    assert client.get("/api/v1/shared/not-a-token").status_code == 404
    assert client.get("/api/v1/sign/not-a-token").status_code == 404


def test_unauthenticated_is_401(client):
    assert client.get("/api/v1/documents").status_code == 401
    assert client.get(f"/api/v1/documents/signature-requests/{uuid.uuid4()}").status_code == 401

"""Documents BE-C, export: a rich document becomes a stored PDF/DOCX file (gap-list row 12)."""

import io
import uuid

import pytest
from docx import Document as DocxDocument
from reportlab import rl_config

from app.core.config import settings
from app.db.models.document import DocumentFile
from app.db.models.enums import DOCUMENT_ROLES, MembershipRole, ScanStatus
from tests.documents_helpers import headers, new_doc, team

DISCLAIMER = "is not legal advice"


@pytest.fixture(autouse=True)
def _storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(rl_config, "pageCompression", 0)  # lets the tests read the page text


@pytest.fixture
def ws(db):
    startup, users = team(db)
    return startup, users, {r: headers(u, startup) for r, u in users.items()}


def _export(client, h, doc_id, fmt="pdf"):
    return client.post(f"/api/v1/documents/{doc_id}/export", json={"format": fmt}, headers=h)


def _rich(client, h, **extra):
    doc = new_doc(client, h, title="Q4 Plan", **extra)
    sections = [
        {
            "id": "s1",
            "heading": "Summary",
            "body": "We sell <b>shovels</b> & rakes.\n\n- one\n- two",
        },
        {"id": "s2", "heading": "Team", "body": "Two founders\nand a dog"},
    ]
    r = client.put(
        f"/api/v1/documents/{doc['id']}",
        json={
            "title": "Q4 Plan",
            "sections": sections,
            "version": 1,
            "status": "draft",
            "folder": extra.get("folder"),
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _bytes(client, h, file_id):
    link = client.get(f"/api/v1/documents/files/{file_id}/download", headers=h).json()["data"]
    r = client.get("/api/v1" + link["url"].split("/api/v1", 1)[1])
    assert r.status_code == 200
    return r.content


@pytest.mark.parametrize("role", sorted(DOCUMENT_ROLES, key=lambda r: r.value))
def test_pdf_export_creates_a_clean_private_file(client, ws, role):
    _s, _u, heads = ws
    doc = _rich(client, heads[MembershipRole.founder])
    r = _export(client, heads[role], doc["id"])
    assert r.status_code == 201, r.text
    f = r.json()["data"]
    assert f["filename"] == "Q4 Plan.pdf"
    assert f["content_type"] == "application/pdf" and f["mime"] == "application/pdf"
    assert f["scan_status"] == "clean"
    assert f["origin"] == "export" and f["type"] == "file"
    assert f["source_document_id"] == doc["id"] and f["source_version"] == doc["version"]
    body = _bytes(client, heads[role], f["id"])
    assert body.startswith(b"%PDF")
    assert b"Q4 Plan" in body and b"shovels" in body
    assert b"(We sell <)" in body  # the tag is printed literally, never parsed as markup
    assert DISCLAIMER not in body.decode("latin-1")  # no disclaimer on a non-legal document


def test_export_appears_in_the_files_list_with_its_origin(client, ws):
    _s, _u, heads = ws
    h = heads[MembershipRole.founder]
    doc = _rich(client, h)
    fid = _export(client, h, doc["id"]).json()["data"]["id"]
    rows = client.get("/api/v1/documents/files", headers=h).json()["data"]["files"]
    row = next(x for x in rows if x["id"] == fid)
    assert row["origin"] == "export" and row["source_document_id"] == doc["id"]
    up = client.post(
        "/api/v1/documents/files",
        files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
        headers=h,
    ).json()["data"]
    assert up["origin"] == "upload" and up["source_document_id"] is None


def test_docx_export_round_trips_text(client, ws):
    _s, _u, heads = ws
    h = heads[MembershipRole.team_member]
    doc = _rich(client, heads[MembershipRole.founder])
    f = _export(client, h, doc["id"], "docx").json()["data"]
    assert f["filename"] == "Q4 Plan.docx"
    assert f["content_type"].endswith("wordprocessingml.document")
    parsed = DocxDocument(io.BytesIO(_bytes(client, h, f["id"])))
    text = "\n".join(p.text for p in parsed.paragraphs)
    assert "Q4 Plan" in text and "Summary" in text and "We sell <b>shovels</b> & rakes." in text
    assert "Two founders" in text and "and a dog" in text
    assert parsed.core_properties.title == "Q4 Plan"


def test_same_version_and_format_reuses_the_file_new_version_makes_a_new_one(client, ws):
    _s, _u, heads = ws
    h = heads[MembershipRole.founder]
    doc = _rich(client, h)
    first = _export(client, h, doc["id"])
    again = _export(client, h, doc["id"])
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["data"]["id"] == first.json()["data"]["id"]
    other = _export(client, h, doc["id"], "docx")
    assert other.status_code == 201 and other.json()["data"]["id"] != first.json()["data"]["id"]
    client.put(
        f"/api/v1/documents/{doc['id']}",
        json={"title": "Q4 Plan", "sections": doc["sections"], "version": doc["version"]},
        headers=h,
    )
    newer = _export(client, h, doc["id"])
    assert newer.status_code == 201
    assert newer.json()["data"]["source_version"] == doc["version"] + 1
    assert newer.json()["data"]["id"] != first.json()["data"]["id"]


def test_a_trashed_export_is_not_reused(client, ws):
    _s, _u, heads = ws
    h = heads[MembershipRole.founder]
    doc = _rich(client, h)
    first = _export(client, h, doc["id"]).json()["data"]
    assert client.delete(f"/api/v1/documents/files/{first['id']}", headers=h).status_code == 200
    second = _export(client, h, doc["id"])
    assert second.status_code == 201 and second.json()["data"]["id"] != first["id"]


def test_every_non_editor_role_is_refused(client, ws, db):
    _s, _u, heads = ws
    doc = _rich(client, heads[MembershipRole.founder])
    for role in set(MembershipRole) - DOCUMENT_ROLES:
        assert _export(client, heads[role], doc["id"]).status_code == 403, role
    assert db.query(DocumentFile).filter_by(source_document_id=uuid.UUID(doc["id"])).count() == 0


def test_trashed_and_unknown_documents_are_404(client, ws):
    _s, _u, heads = ws
    h = heads[MembershipRole.founder]
    doc = _rich(client, h)
    assert client.delete(f"/api/v1/documents/{doc['id']}", headers=h).status_code == 200
    assert _export(client, h, doc["id"]).status_code == 404
    assert _export(client, h, str(uuid.uuid4())).status_code == 404


def test_another_workspaces_document_is_404(client, ws, db):
    _s, _u, heads = ws
    other, ousers = team(db, name="Other")
    theirs = _rich(client, headers(ousers[MembershipRole.founder], other))
    assert _export(client, heads[MembershipRole.founder], theirs["id"]).status_code == 404


def test_bad_format_is_422(client, ws):
    _s, _u, heads = ws
    h = heads[MembershipRole.founder]
    doc = _rich(client, h)
    r = _export(client, h, doc["id"], "rtf")
    assert r.status_code == 422


def test_oversized_document_is_refused_not_stored(client, ws, db, monkeypatch):
    _s, _u, heads = ws
    h = heads[MembershipRole.founder]
    doc = _rich(client, h)
    monkeypatch.setattr("app.services.documents.export.MAX_EXPORT_BYTES", 100)
    r = _export(client, h, doc["id"])
    assert r.status_code == 422 and "too large" in r.json()["error"]["message"]
    assert db.query(DocumentFile).filter_by(source_document_id=uuid.UUID(doc["id"])).count() == 0


def test_blocked_scan_verdict_is_stored_blocked_and_unlinkable(client, ws, monkeypatch):
    _s, _u, heads = ws
    h = heads[MembershipRole.founder]
    doc = _rich(client, h)

    class Blocker:
        def scan(self, content, filename):
            return ScanStatus.blocked

    monkeypatch.setattr("app.services.documents.scanner.get_scanner", lambda: Blocker())
    f = _export(client, h, doc["id"]).json()["data"]
    assert f["scan_status"] == "blocked"
    r = client.get(f"/api/v1/documents/files/{f['id']}/download", headers=h)
    assert r.status_code == 409


def test_exported_file_can_be_sent_for_signature(client, ws):
    _s, _u, heads = ws
    h = heads[MembershipRole.founder]
    doc = _rich(client, h)
    f = _export(client, h, doc["id"]).json()["data"]
    r = client.post(
        f"/api/v1/documents/files/{f['id']}/signature-requests",
        json={"signers": [{"email": "signer@example.com", "name": "S"}]},
        headers=h,
    )
    assert r.status_code == 201, r.text


def test_export_survives_permanent_delete_of_its_source(client, ws):
    _s, _u, heads = ws
    h = heads[MembershipRole.founder]
    doc = _rich(client, h)
    f = _export(client, h, doc["id"]).json()["data"]
    client.delete(f"/api/v1/documents/{doc['id']}", headers=h)
    assert client.delete(f"/api/v1/documents/{doc['id']}/permanent", headers=h).status_code == 200
    row = client.get(f"/api/v1/documents/files/{f['id']}", headers=h).json()["data"]
    assert row["source_document_id"] is None and row["origin"] == "export"


def test_export_inherits_the_documents_folder(client, ws):
    _s, _u, heads = ws
    h = heads[MembershipRole.founder]
    doc = _rich(client, h, folder="Legal")
    f = _export(client, h, doc["id"]).json()["data"]
    assert f["folder"] == "Legal"


def test_xml_forbidden_characters_do_not_break_a_docx_export(client, ws):
    _s, _u, heads = ws
    h = heads[MembershipRole.founder]
    doc = new_doc(client, h, title="Plan￾")
    client.put(
        f"/api/v1/documents/{doc['id']}",
        json={
            "title": "Plan￾",
            "version": 1,
            "sections": [{"id": "a", "heading": "H￿", "body": "x￾y"}],
        },
        headers=h,
    )
    assert _export(client, h, doc["id"], "docx").status_code == 201
    assert _export(client, h, doc["id"], "pdf").status_code == 201


def test_too_many_lines_is_refused_before_rendering(client, ws, monkeypatch):
    _s, _u, heads = ws
    h = heads[MembershipRole.founder]
    doc = _rich(client, h)
    monkeypatch.setattr("app.services.documents.export.MAX_LINES", 3)
    r = _export(client, h, doc["id"])
    assert r.status_code == 422 and "too large" in r.json()["error"]["message"]


def test_rename_makes_a_new_export_not_the_stale_one(client, ws):
    _s, _u, heads = ws
    h = heads[MembershipRole.founder]
    doc = _rich(client, h)
    first = _export(client, h, doc["id"]).json()["data"]
    client.patch(f"/api/v1/documents/{doc['id']}", json={"title": "Renamed"}, headers=h)
    second = _export(client, h, doc["id"])
    assert second.status_code == 201
    assert second.json()["data"]["filename"] == "Renamed.pdf"
    assert second.json()["data"]["id"] != first["id"]


def test_non_ascii_title_downloads_with_an_rfc5987_filename(client, ws):
    _s, _u, heads = ws
    h = heads[MembershipRole.founder]
    doc = new_doc(client, h, title="Adé — NDA ọlá")
    f = _export(client, h, doc["id"]).json()["data"]
    link = client.get(f"/api/v1/documents/files/{f['id']}/download", headers=h).json()["data"]
    r = client.get("/api/v1" + link["url"].split("/api/v1", 1)[1])
    assert r.status_code == 200
    assert "filename*=UTF-8''" in r.headers["content-disposition"]

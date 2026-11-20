from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.document import Document, DocumentFile
from app.db.models.enums import MembershipRole, MembershipStatus, ScanStatus
from app.db.models.membership import Membership
from app.db.models.notification import Notification
from app.services.notifications import registry
from tests.api.test_legal import BASE, FORBIDDEN_ROLES, WRITER_ROLES, _member

REQUESTS = f"{BASE}/advisor-requests"


def _doc(db, startup, title="Supplier Agreement"):
    row = Document(startup_id=startup.id, title=title)
    db.add(row)
    db.flush()
    return row


def _file(db, startup, *, scan=ScanStatus.clean, filename="supplier-agreement.pdf"):
    row = DocumentFile(
        startup_id=startup.id,
        filename=filename,
        content_type="application/pdf",
        size_bytes=2048,
        storage_key=f"documents/{startup.id}/{filename}",
        scan_status=scan,
    )
    db.add(row)
    db.flush()
    return row


def _workspace(db):
    """A founder, a legal advisor and a team member in one workspace."""
    founder, startup, fh = _member(db)
    advisor, _s, ah = _member(db, role=MembershipRole.legal_advisor, startup=startup)
    member, _s2, mh = _member(db, role=MembershipRole.team_member, startup=startup)
    return {
        "startup": startup,
        "founder": founder,
        "fh": fh,
        "advisor": advisor,
        "ah": ah,
        "member": member,
        "mh": mh,
    }


def _body(w, **kw):
    body = {"subject": "Please check clause 7", "advisor_user_id": str(w["advisor"].id)}
    body.update(kw)
    return body


# --- create ------------------------------------------------------------------------------------


def test_create_with_a_document(client, db):
    w = _workspace(db)
    doc = _doc(db, w["startup"])
    resp = client.post(REQUESTS, json=_body(w, document_id=str(doc.id)), headers=w["fh"])
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "requested"
    assert data["attachment"] == {
        "kind": "document",
        "id": str(doc.id),
        "title": "Supplier Agreement",
    }
    assert data["advisor"]["id"] == str(w["advisor"].id)
    # Nothing has come back yet, and it is not cancelled.
    assert data["returned_file"] is None
    assert data["returned_at"] is None and data["cancelled_at"] is None


def test_create_with_an_uploaded_file(client, db):
    w = _workspace(db)
    file = _file(db, w["startup"])
    resp = client.post(
        REQUESTS, json=_body(w, file_id=str(file.id), note="Clause 7 worries me."), headers=w["fh"]
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # The PDF case, which DocumentGrant and DocumentVersion cannot serve at all (D-LEG-16).
    assert data["attachment"]["kind"] == "file"
    assert data["attachment"]["title"] == "supplier-agreement.pdf"
    assert data["note"] == "Clause 7 worries me."


def test_both_targets_is_422(client, db):
    w = _workspace(db)
    body = _body(
        w, document_id=str(_doc(db, w["startup"]).id), file_id=str(_file(db, w["startup"]).id)
    )
    resp = client.post(REQUESTS, json=body, headers=w["fh"])
    # Refused by the schema, so the database CHECK never has to surface as a 500.
    assert resp.status_code == 422, resp.text


def test_neither_target_is_422(client, db):
    w = _workspace(db)
    resp = client.post(REQUESTS, json=_body(w), headers=w["fh"])
    assert resp.status_code == 422, resp.text


def test_another_workspaces_document_is_404(client, db):
    w = _workspace(db)
    _other_u, other_s, _oh = _member(db)
    theirs = _doc(db, other_s)
    resp = client.post(REQUESTS, json=_body(w, document_id=str(theirs.id)), headers=w["fh"])
    # 404, never 403: a 403 would confirm the id exists.
    assert resp.status_code == 404, resp.text


def test_a_trashed_document_is_404(client, db):
    w = _workspace(db)
    doc = _doc(db, w["startup"])
    doc.trashed_at = datetime.now(UTC)
    db.flush()
    resp = client.post(REQUESTS, json=_body(w, document_id=str(doc.id)), headers=w["fh"])
    assert resp.status_code == 404, resp.text


def test_a_file_that_has_not_passed_the_scan_is_409(client, db):
    w = _workspace(db)
    pending = _file(db, w["startup"], scan=ScanStatus.pending)
    resp = client.post(REQUESTS, json=_body(w, file_id=str(pending.id)), headers=w["fh"])
    # D-LEG-18, using the house code and status the data room already uses for this.
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "FILE_NOT_CLEAN"


def test_naming_a_team_member_as_the_advisor_is_422(client, db):
    w = _workspace(db)
    doc = _doc(db, w["startup"])
    body = _body(w, document_id=str(doc.id))
    body["advisor_user_id"] = str(w["member"].id)
    resp = client.post(REQUESTS, json=body, headers=w["fh"])
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["field_errors"][0]["field"] == "advisor_user_id"


def test_naming_an_advisor_from_another_workspace_is_422(client, db):
    w = _workspace(db)
    _ou, other_s, _oh = _member(db)
    outsider, _s, _h = _member(db, role=MembershipRole.legal_advisor, startup=other_s)
    body = _body(w, document_id=str(_doc(db, w["startup"]).id))
    body["advisor_user_id"] = str(outsider.id)
    resp = client.post(REQUESTS, json=body, headers=w["fh"])
    assert resp.status_code == 422, resp.text


def test_naming_a_removed_advisor_is_422(client, db):
    w = _workspace(db)
    row = db.query(Membership).filter_by(user_id=w["advisor"].id, startup_id=w["startup"].id).one()
    row.status = MembershipStatus.removed
    db.flush()
    resp = client.post(
        REQUESTS, json=_body(w, document_id=str(_doc(db, w["startup"]).id)), headers=w["fh"]
    )
    assert resp.status_code == 422, resp.text


def test_a_refused_request_leaves_no_row(client, db):
    from app.db.models.legal import AdvisorRequest

    w = _workspace(db)
    pending = _file(db, w["startup"], scan=ScanStatus.pending)
    resp = client.post(REQUESTS, json=_body(w, file_id=str(pending.id)), headers=w["fh"])
    assert resp.status_code == 409, resp.text
    # Everything is validated before the insert, so a refusal cannot leave a half-made request.
    assert db.query(AdvisorRequest).filter_by(startup_id=w["startup"].id).count() == 0


# --- list and read -----------------------------------------------------------------------------


def test_list_is_newest_first_and_filters_by_status(client, db):
    w = _workspace(db)
    first = client.post(
        REQUESTS,
        json=_body(w, document_id=str(_doc(db, w["startup"], title="One").id)),
        headers=w["fh"],
    )
    assert first.status_code == 200, first.text
    second = client.post(
        REQUESTS,
        json=_body(w, document_id=str(_doc(db, w["startup"], title="Two").id)),
        headers=w["fh"],
    )
    assert second.status_code == 200, second.text

    # Postgres' now() is the TRANSACTION timestamp, and this whole test runs in one transaction,
    # so both rows were stamped identically and `desc` had nothing to sort by. Each API call is
    # its own transaction in production, so instead of relying on insertion luck, age the SECOND
    # one: a correct ordering then puts "One" first, which insertion order would not.
    from app.db.models.legal import AdvisorRequest

    older = db.query(AdvisorRequest).filter_by(id=second.json()["data"]["id"]).one()
    older.created_at = datetime.now(UTC) - timedelta(days=1)
    db.flush()

    listed = client.get(REQUESTS, headers=w["fh"])
    assert listed.status_code == 200, listed.text
    rows = listed.json()["data"]["advisor_requests"]
    assert [r["attachment"]["title"] for r in rows] == ["One", "Two"]

    filtered = client.get(f"{REQUESTS}?status=returned", headers=w["fh"])
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["data"]["advisor_requests"] == []


def test_read_one_and_a_foreign_id_is_404(client, db):
    w = _workspace(db)
    made = client.post(
        REQUESTS, json=_body(w, document_id=str(_doc(db, w["startup"]).id)), headers=w["fh"]
    )
    assert made.status_code == 200, made.text
    rid = made.json()["data"]["id"]

    got = client.get(f"{REQUESTS}/{rid}", headers=w["fh"])
    assert got.status_code == 200, got.text
    assert got.json()["data"]["id"] == rid

    _ou, _os, other_h = _member(db)
    cross = client.get(f"{REQUESTS}/{rid}", headers=other_h)
    assert cross.status_code == 404, cross.text


# --- access ------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", WRITER_ROLES)
def test_writers_can_create(client, db, role):
    w = _workspace(db)
    _u, _s, h = _member(db, role=role, startup=w["startup"])
    resp = client.post(
        REQUESTS, json=_body(w, document_id=str(_doc(db, w["startup"]).id)), headers=h
    )
    assert resp.status_code == 200, resp.text


def test_a_legal_advisor_cannot_create_a_request(client, db):
    w = _workspace(db)
    resp = client.post(
        REQUESTS, json=_body(w, document_id=str(_doc(db, w["startup"]).id)), headers=w["ah"]
    )
    # An advisor answers requests; they do not raise them (D-LEG-9).
    assert resp.status_code == 403, resp.text


def test_a_legal_advisor_can_read_the_list(client, db):
    w = _workspace(db)
    resp = client.get(REQUESTS, headers=w["ah"])
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_forbidden_roles_cannot_read_or_create(client, db, role):
    w = _workspace(db)
    _u, _s, h = _member(db, role=role, startup=w["startup"])
    read = client.get(REQUESTS, headers=h)
    assert read.status_code == 403, read.text
    write = client.post(
        REQUESTS, json=_body(w, document_id=str(_doc(db, w["startup"]).id)), headers=h
    )
    assert write.status_code == 403, write.text


def test_the_request_requires_authentication(client):
    resp = client.get(REQUESTS)
    assert resp.status_code in (401, 403)


# --- D-LEG-15: no addresses anywhere -----------------------------------------------------------


def test_no_response_carries_an_email_address(client, db):
    w = _workspace(db)
    made = client.post(
        REQUESTS, json=_body(w, document_id=str(_doc(db, w["startup"]).id)), headers=w["fh"]
    )
    assert made.status_code == 200, made.text
    listed = client.get(REQUESTS, headers=w["ah"])
    assert listed.status_code == 200, listed.text
    # People come from `owner_map`, whose contract is "never any part of their e-mail". Asserted
    # on the raw body so a future field cannot smuggle one in unnoticed.
    assert "@" not in made.text
    assert "@" not in listed.text


# --- the notification (row 47) -----------------------------------------------------------------


def test_creating_a_request_notifies_only_the_named_advisor(client, db):
    registry.register()
    w = _workspace(db)
    made = client.post(
        REQUESTS, json=_body(w, document_id=str(_doc(db, w["startup"]).id)), headers=w["fh"]
    )
    assert made.status_code == 200, made.text

    notes = db.query(Notification).filter_by(type="legal.advisor_request.created").all()
    assert {n.user_id for n in notes} == {w["advisor"].id}
    assert notes[0].title.endswith("requested your review of Supplier Agreement")
    assert "@" not in notes[0].title

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


def test_an_advisor_sees_their_own_requests_and_not_another_advisors(client, db):
    w = _workspace(db)
    second, _s, second_h = _member(db, role=MembershipRole.legal_advisor, startup=w["startup"])

    mine = client.post(
        REQUESTS,
        json=_body(w, document_id=str(_doc(db, w["startup"], title="For advisor one").id)),
        headers=w["fh"],
    )
    assert mine.status_code == 200, mine.text
    theirs_body = _body(w, document_id=str(_doc(db, w["startup"], title="For advisor two").id))
    theirs_body["advisor_user_id"] = str(second.id)
    theirs = client.post(REQUESTS, json=theirs_body, headers=w["fh"])
    assert theirs.status_code == 200, theirs.text

    # The founder sees both.
    all_of_them = client.get(REQUESTS, headers=w["fh"])
    assert all_of_them.status_code == 200, all_of_them.text
    assert len(all_of_them.json()["data"]["advisor_requests"]) == 2

    # Each advisor sees only the one naming them (gap row 45, D-LEG-11).
    first_view = client.get(REQUESTS, headers=w["ah"])
    assert first_view.status_code == 200, first_view.text
    titles = [r["attachment"]["title"] for r in first_view.json()["data"]["advisor_requests"]]
    assert titles == ["For advisor one"]

    second_view = client.get(REQUESTS, headers=second_h)
    assert second_view.status_code == 200, second_view.text
    titles2 = [r["attachment"]["title"] for r in second_view.json()["data"]["advisor_requests"]]
    assert titles2 == ["For advisor two"]


def test_an_advisor_gets_404_on_another_advisors_request(client, db):
    w = _workspace(db)
    second, _s, second_h = _member(db, role=MembershipRole.legal_advisor, startup=w["startup"])
    made = client.post(
        REQUESTS, json=_body(w, document_id=str(_doc(db, w["startup"]).id)), headers=w["fh"]
    )
    assert made.status_code == 200, made.text
    rid = made.json()["data"]["id"]

    # The named advisor can open it.
    ok = client.get(f"{REQUESTS}/{rid}", headers=w["ah"])
    assert ok.status_code == 200, ok.text

    # The other advisor gets the SAME 404 as a foreign id: a 403 would confirm it exists.
    nope = client.get(f"{REQUESTS}/{rid}", headers=second_h)
    assert nope.status_code == 404, nope.text


def test_the_advisors_shape_carries_no_user_ids(client, db):
    w = _workspace(db)
    made = client.post(
        REQUESTS, json=_body(w, document_id=str(_doc(db, w["startup"]).id)), headers=w["fh"]
    )
    assert made.status_code == 200, made.text

    advisor_view = client.get(REQUESTS, headers=w["ah"])
    assert advisor_view.status_code == 200, advisor_view.text
    row = advisor_view.json()["data"]["advisor_requests"][0]

    # No USER ids: `advisor` is dropped (it is always the viewer) and `created_by` becomes a
    # name-only `requested_by`, mirroring documents._external_comment.
    assert "advisor" not in row
    assert "created_by" not in row
    assert row["requested_by"] == {"name": row["requested_by"]["name"]}
    assert set(row["requested_by"]) == {"name"}
    # Neither the founder's nor the advisor's id appears anywhere in the body.
    assert str(w["founder"].id) not in advisor_view.text
    assert str(w["advisor"].id) not in advisor_view.text

    # Resource ids DO stay: the advisor needs them to fetch the document.
    assert row["attachment"]["id"]

    # And the founder's own view still carries the fuller shape.
    founder_view = client.get(REQUESTS, headers=w["fh"])
    assert founder_view.status_code == 200, founder_view.text
    assert founder_view.json()["data"]["advisor_requests"][0]["advisor"]["id"] == str(
        w["advisor"].id
    )


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


# --- the attached-document read (task 4, D-LEG-16) ---------------------------------------------


@pytest.fixture()
def _local_storage(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))


def _stored_file(db, startup, *, body=b"%PDF-1.4 contract bytes"):
    from app.platform.storage import get_storage

    row = _file(db, startup)
    get_storage().save_private(row.storage_key, body, row.content_type)
    return row


def _make(client, w, **kw):
    resp = client.post(REQUESTS, json=_body(w, **kw), headers=w["fh"])
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def test_the_named_advisor_can_read_an_attached_file(client, db, _local_storage):
    w = _workspace(db)
    file = _stored_file(db, w["startup"])
    rid = _make(client, w, file_id=str(file.id))

    resp = client.get(f"{REQUESTS}/{rid}/document", headers=w["ah"])
    assert resp.status_code == 200, resp.text
    assert resp.content == b"%PDF-1.4 contract bytes"
    assert resp.headers["content-type"].startswith("application/pdf")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["cache-control"] == "private, no-store"
    assert "supplier-agreement.pdf" in resp.headers["content-disposition"]


def test_a_writer_can_read_it_too(client, db, _local_storage):
    w = _workspace(db)
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))
    for headers in (w["fh"], w["mh"]):
        resp = client.get(f"{REQUESTS}/{rid}/document", headers=headers)
        assert resp.status_code == 200, resp.text


def test_a_rich_document_comes_back_as_a_pdf(client, db, _local_storage):
    w = _workspace(db)
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))
    resp = client.get(f"{REQUESTS}/{rid}/document", headers=w["ah"])
    # A rich document has no bytes, so it is rendered exactly as the data room renders one.
    assert resp.status_code == 200, resp.text
    assert resp.content.startswith(b"%PDF")
    assert resp.headers["content-type"].startswith("application/pdf")


def test_another_advisor_cannot_read_the_document(client, db, _local_storage):
    w = _workspace(db)
    _second, _s, second_h = _member(db, role=MembershipRole.legal_advisor, startup=w["startup"])
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))
    resp = client.get(f"{REQUESTS}/{rid}/document", headers=second_h)
    assert resp.status_code == 404, resp.text


def test_another_workspace_cannot_read_the_document(client, db, _local_storage):
    w = _workspace(db)
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))
    _ou, _os, other_h = _member(db)
    resp = client.get(f"{REQUESTS}/{rid}/document", headers=other_h)
    assert resp.status_code == 404, resp.text


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_forbidden_roles_cannot_read_the_document(client, db, role, _local_storage):
    w = _workspace(db)
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))
    _u, _s, h = _member(db, role=role, startup=w["startup"])
    resp = client.get(f"{REQUESTS}/{rid}/document", headers=h)
    assert resp.status_code == 403, resp.text


def test_a_cancelled_request_stops_serving_the_document(client, db, _local_storage):
    from app.db.models.enums import AdvisorRequestStatus
    from app.db.models.legal import AdvisorRequest

    w = _workspace(db)
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))
    before = client.get(f"{REQUESTS}/{rid}/document", headers=w["ah"])
    assert before.status_code == 200, before.text

    row = db.query(AdvisorRequest).filter_by(id=rid).one()
    row.status = AdvisorRequestStatus.cancelled
    db.flush()

    # The lead's condition: access ends with the request, and there is no grant row to revoke.
    after = client.get(f"{REQUESTS}/{rid}/document", headers=w["ah"])
    assert after.status_code == 404, after.text


def test_a_file_that_stops_being_clean_stops_serving(client, db, _local_storage):
    from app.db.models.document import DocumentFile

    w = _workspace(db)
    file = _stored_file(db, w["startup"])
    rid = _make(client, w, file_id=str(file.id))

    row = db.query(DocumentFile).filter_by(id=file.id).one()
    row.scan_status = ScanStatus.blocked
    db.flush()

    # Re-checked on every call (D-LEG-18), because a file can be blocked after the request went
    # out, exactly as signatures.py already handles.
    resp = client.get(f"{REQUESTS}/{rid}/document", headers=w["ah"])
    assert resp.status_code == 404, resp.text


def test_a_missing_stored_object_is_404_not_a_500(client, db, _local_storage):
    w = _workspace(db)
    file = _file(db, w["startup"])  # row exists, nothing written to storage
    rid = _make(client, w, file_id=str(file.id))
    resp = client.get(f"{REQUESTS}/{rid}/document", headers=w["ah"])
    assert resp.status_code == 404, resp.text


# --- the comment thread + the in_review transition (task 5, D-LEG-13 / D-LEG-17) ---------------


def _comments(client, rid, headers):
    return client.get(f"{REQUESTS}/{rid}/comments", headers=headers)


def _say(client, rid, headers, body="Clause 7 is unenforceable as drafted."):
    return client.post(f"{REQUESTS}/{rid}/comments", json={"body": body}, headers=headers)


def test_the_thread_is_one_per_request_and_oldest_first(client, db):
    w = _workspace(db)
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))

    first = _say(client, rid, w["fh"], "Please look at clause 7.")
    assert first.status_code == 200, first.text
    second = _say(client, rid, w["ah"], "Clause 7 is unenforceable as drafted.")
    assert second.status_code == 200, second.text

    # Same trap as the request list: now() is the TRANSACTION timestamp, so both comments were
    # stamped identically and the `id` tiebreaker is a random UUID. Each comment is its own
    # transaction in production, so give them real, distinct times instead of trusting insertion
    # order, which is what this test is supposed to be checking.
    from app.db.models.comment import Comment

    asked = db.query(Comment).filter_by(entity_id=rid, body="Please look at clause 7.").one()
    asked.created_at = datetime.now(UTC) - timedelta(hours=2)
    db.flush()

    listed = _comments(client, rid, w["fh"])
    assert listed.status_code == 200, listed.text
    bodies = [c["body"] for c in listed.json()["data"]["comments"]]
    assert bodies == ["Please look at clause 7.", "Clause 7 is unenforceable as drafted."]
    # On the shared comments table, keyed to this request (D-LEG-13): no new table.
    assert listed.json()["data"]["comments"][0]["entity_type"] == "advisor_request"
    assert listed.json()["data"]["comments"][0]["entity_id"] == rid


def test_the_advisors_first_comment_moves_it_to_in_review(client, db):
    w = _workspace(db)
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))

    before = client.get(f"{REQUESTS}/{rid}", headers=w["fh"])
    assert before.status_code == 200, before.text
    assert before.json()["data"]["status"] == "requested"

    said = _say(client, rid, w["ah"])
    assert said.status_code == 200, said.text

    after = client.get(f"{REQUESTS}/{rid}", headers=w["fh"])
    assert after.status_code == 200, after.text
    assert after.json()["data"]["status"] == "in_review"


def test_the_founders_own_comment_does_not_move_the_status(client, db):
    w = _workspace(db)
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))
    said = _say(client, rid, w["fh"], "Bumping this.")
    assert said.status_code == 200, said.text
    got = client.get(f"{REQUESTS}/{rid}", headers=w["fh"])
    assert got.status_code == 200, got.text
    # Only the advisor engaging means anything: a founder talking to themselves does not.
    assert got.json()["data"]["status"] == "requested"


def test_a_returned_request_is_not_dragged_back_to_in_review(client, db):
    from app.db.models.enums import AdvisorRequestStatus
    from app.db.models.legal import AdvisorRequest

    w = _workspace(db)
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))
    row = db.query(AdvisorRequest).filter_by(id=rid).one()
    row.status = AdvisorRequestStatus.returned
    db.flush()

    said = _say(client, rid, w["ah"], "One more thought.")
    assert said.status_code == 200, said.text
    got = client.get(f"{REQUESTS}/{rid}", headers=w["fh"])
    assert got.status_code == 200, got.text
    # The transition is `requested -> in_review` only; it never walks backwards.
    assert got.json()["data"]["status"] == "returned"


def test_the_advisors_copy_of_the_thread_has_no_user_ids(client, db):
    w = _workspace(db)
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))
    said = _say(client, rid, w["fh"], "Please look at clause 7.")
    assert said.status_code == 200, said.text

    advisor_view = _comments(client, rid, w["ah"])
    assert advisor_view.status_code == 200, advisor_view.text
    c = advisor_view.json()["data"]["comments"][0]
    # Copies documents._external_comment: the author's name, no id, no mention list.
    assert set(c["author"]) == {"name"}
    assert "mention_user_ids" not in c
    assert str(w["founder"].id) not in advisor_view.text

    founder_view = _comments(client, rid, w["fh"])
    assert founder_view.status_code == 200, founder_view.text
    assert founder_view.json()["data"]["comments"][0]["author"]["id"] == str(w["founder"].id)


def test_another_advisor_cannot_read_or_post_on_the_thread(client, db):
    w = _workspace(db)
    _second, _s, second_h = _member(db, role=MembershipRole.legal_advisor, startup=w["startup"])
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))

    read = _comments(client, rid, second_h)
    assert read.status_code == 404, read.text
    post = _say(client, rid, second_h)
    assert post.status_code == 404, post.text


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_forbidden_roles_cannot_touch_the_thread(client, db, role):
    w = _workspace(db)
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))
    _u, _s, h = _member(db, role=role, startup=w["startup"])
    read = _comments(client, rid, h)
    assert read.status_code == 403, read.text
    post = _say(client, rid, h)
    assert post.status_code == 403, post.text


def test_a_cancelled_request_refuses_new_comments(client, db):
    from app.db.models.enums import AdvisorRequestStatus
    from app.db.models.legal import AdvisorRequest

    w = _workspace(db)
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))
    row = db.query(AdvisorRequest).filter_by(id=rid).one()
    row.status = AdvisorRequestStatus.cancelled
    db.flush()

    post = _say(client, rid, w["ah"])
    assert post.status_code == 409, post.text
    assert post.json()["error"]["code"] == "REQUEST_CANCELLED"
    # Reading the thread still works: the history of a cancelled request is not hidden.
    read = _comments(client, rid, w["fh"])
    assert read.status_code == 200, read.text


def test_a_whitespace_only_comment_is_refused(client, db):
    w = _workspace(db)
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))
    post = _say(client, rid, w["fh"], "   ")
    assert post.status_code == 422, post.text


# --- the reviewed draft (task 6, gap row 45) ---------------------------------------------------

PDF_BYTES = b"%PDF-1.4 marked up by counsel"


def _docx_bytes() -> bytes:
    """A real minimal .docx. The sniffer opens the upload as a ZIP and requires a readable
    central directory, `[Content_Types].xml` and a `word/` entry, so fake PK magic bytes are
    correctly refused and would not exercise the OOXML path at all."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<document/>")
    return buf.getvalue()


def _send_draft(client, rid, headers, *, name="reviewed.pdf", ct="application/pdf", body=PDF_BYTES):
    import io

    return client.post(
        f"{REQUESTS}/{rid}/reviewed-draft",
        files={"file": (name, io.BytesIO(body), ct)},
        headers=headers,
    )


def test_the_advisor_returns_a_draft_and_the_request_is_returned(client, db, _local_storage):
    w = _workspace(db)
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))

    resp = _send_draft(client, rid, w["ah"])
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "returned"
    assert data["returned_file"]["filename"] == "reviewed.pdf"
    assert data["returned_at"] is not None


def test_the_returned_draft_is_an_ordinary_private_library_file(client, db, _local_storage):
    from app.db.models.document import DocumentFile
    from app.db.models.enums import ScanStatus as S

    w = _workspace(db)
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))
    resp = _send_draft(client, rid, w["ah"])
    assert resp.status_code == 200, resp.text

    row = (
        db.query(DocumentFile).filter_by(startup_id=w["startup"].id, filename="reviewed.pdf").one()
    )
    # Through Documents' own upload: private from the first byte and scanned (condition 2).
    assert row.is_private is True
    assert row.url is None
    assert row.scan_status == S.clean
    assert row.uploaded_by_id == w["advisor"].id


def test_a_founder_cannot_return_a_draft(client, db, _local_storage):
    w = _workspace(db)
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))
    # A writer can see the request, but returning work is the advisor's action, not theirs.
    resp = _send_draft(client, rid, w["fh"])
    assert resp.status_code == 403, resp.text


def test_another_advisor_gets_404_not_403(client, db, _local_storage):
    w = _workspace(db)
    _second, _s, second_h = _member(db, role=MembershipRole.legal_advisor, startup=w["startup"])
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))
    resp = _send_draft(client, rid, second_h)
    assert resp.status_code == 404, resp.text


def test_a_docx_draft_is_accepted(client, db, _local_storage):
    w = _workspace(db)
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))
    resp = _send_draft(
        client,
        rid,
        w["ah"],
        name="reviewed.docx",
        ct="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        body=_docx_bytes(),
    )
    # The whole reason for using Documents' path: a lawyer marks up a Word file, and Slice 1's
    # proof sniff (PNG/JPEG/PDF only) would have refused it.
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["returned_file"]["filename"] == "reviewed.docx"


def test_a_draft_whose_bytes_contradict_its_type_is_422(client, db, _local_storage):
    w = _workspace(db)
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))
    resp = _send_draft(client, rid, w["ah"], name="reviewed.pdf", body=b"<html>nope</html>")
    assert resp.status_code == 422, resp.text


def test_an_oversized_draft_is_422(client, db, _local_storage, monkeypatch):
    from app.services.legal import advisor_requests as svc

    monkeypatch.setattr(svc, "MAX_DRAFT_BYTES", 8)
    w = _workspace(db)
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))
    resp = _send_draft(client, rid, w["ah"], body=PDF_BYTES)
    assert resp.status_code == 422, resp.text


def test_a_second_draft_moves_the_pointer_and_keeps_the_first(client, db, _local_storage):
    from app.db.models.document import DocumentFile

    w = _workspace(db)
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))
    first = _send_draft(client, rid, w["ah"], name="draft-one.pdf")
    assert first.status_code == 200, first.text
    second = _send_draft(client, rid, w["ah"], name="draft-two.pdf")
    assert second.status_code == 200, second.text

    assert second.json()["data"]["returned_file"]["filename"] == "draft-two.pdf"
    # Nothing the advisor sent is destroyed: the earlier draft stays an ordinary library file.
    kept = db.query(DocumentFile).filter_by(startup_id=w["startup"].id, filename="draft-one.pdf")
    assert kept.count() == 1


def test_a_cancelled_request_refuses_a_draft(client, db, _local_storage):
    from app.db.models.enums import AdvisorRequestStatus
    from app.db.models.legal import AdvisorRequest

    w = _workspace(db)
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))
    row = db.query(AdvisorRequest).filter_by(id=rid).one()
    row.status = AdvisorRequestStatus.cancelled
    db.flush()

    resp = _send_draft(client, rid, w["ah"])
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "REQUEST_CANCELLED"


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_forbidden_roles_cannot_return_a_draft(client, db, role, _local_storage):
    w = _workspace(db)
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))
    _u, _s, h = _member(db, role=role, startup=w["startup"])
    resp = _send_draft(client, rid, h)
    assert resp.status_code == 403, resp.text


# --- cancel (task 7, D-LEG-14) -----------------------------------------------------------------


def _cancel(client, rid, headers):
    return client.post(f"{REQUESTS}/{rid}/cancel", headers=headers)


def test_a_founder_cancels_an_open_request(client, db):
    w = _workspace(db)
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))
    resp = _cancel(client, rid, w["fh"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "cancelled"
    assert resp.json()["data"]["cancelled_at"] is not None


def test_an_in_review_request_can_still_be_cancelled(client, db):
    w = _workspace(db)
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))
    said = _say(client, rid, w["ah"])  # the advisor's first comment -> in_review
    assert said.status_code == 200, said.text
    resp = _cancel(client, rid, w["fh"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "cancelled"


def test_cancelling_twice_is_409(client, db):
    w = _workspace(db)
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))
    first = _cancel(client, rid, w["fh"])
    assert first.status_code == 200, first.text
    again = _cancel(client, rid, w["fh"])
    # Not idempotent on purpose: the founder should know it was already closed, and there is no
    # reopen to undo it with (D-LEG-14).
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "REQUEST_CANCELLED"


def test_a_completed_review_cannot_be_cancelled(client, db, _local_storage):
    w = _workspace(db)
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))
    returned = _send_draft(client, rid, w["ah"])
    assert returned.status_code == 200, returned.text
    resp = _cancel(client, rid, w["fh"])
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "REQUEST_COMPLETE"


def test_an_advisor_cannot_cancel(client, db):
    w = _workspace(db)
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))
    # An advisor answers requests; withdrawing one is the founder's decision.
    resp = _cancel(client, rid, w["ah"])
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("role", FORBIDDEN_ROLES)
def test_forbidden_roles_cannot_cancel(client, db, role):
    w = _workspace(db)
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))
    _u, _s, h = _member(db, role=role, startup=w["startup"])
    resp = _cancel(client, rid, h)
    assert resp.status_code == 403, resp.text


def test_another_workspace_cannot_cancel(client, db):
    w = _workspace(db)
    rid = _make(client, w, document_id=str(_doc(db, w["startup"]).id))
    _ou, _os, other_h = _member(db)
    resp = _cancel(client, rid, other_h)
    assert resp.status_code == 404, resp.text


def test_cancelling_ends_the_advisors_access_end_to_end(client, db, _local_storage):
    w = _workspace(db)
    rid = _make(client, w, file_id=str(_stored_file(db, w["startup"]).id))

    before = client.get(f"{REQUESTS}/{rid}/document", headers=w["ah"])
    assert before.status_code == 200, before.text

    cancelled = _cancel(client, rid, w["fh"])
    assert cancelled.status_code == 200, cancelled.text

    # The request IS the access rule, so there is no grant row to revoke: the document stops
    # being served, a new comment is refused, and the thread stays readable as history.
    after = client.get(f"{REQUESTS}/{rid}/document", headers=w["ah"])
    assert after.status_code == 404, after.text
    said = _say(client, rid, w["ah"])
    assert said.status_code == 409, said.text
    thread = _comments(client, rid, w["ah"])
    assert thread.status_code == 200, thread.text
    draft = _send_draft(client, rid, w["ah"])
    assert draft.status_code == 409, draft.text

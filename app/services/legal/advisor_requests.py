"""Legal advisor requests (PRD 13.6, gap rows 41 to 47).

A founder or team member asks a named legal advisor to review one document. The attachment is a
rich `Document` OR an uploaded `DocumentFile`, never both (D-LEG-16).

Access to the attached content is **not** a grant and **not** a share: it is the request row
itself, read through `GET /legal/advisor-requests/{id}/document`. Cancelling a request therefore
ends access with nothing to revoke.

Names, never addresses: every person on a response comes from `owner_map`, whose own contract is
"never any part of their e-mail (these names reach grantees and share recipients of every role)".
That is D-LEG-15's condition satisfied by construction rather than by review.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, FileNotClean, NotFound
from app.db.models.comment import Comment
from app.db.models.document import Document, DocumentFile
from app.db.models.enums import (
    AdvisorRequestStatus,
    MembershipRole,
    MembershipStatus,
    ScanStatus,
)
from app.db.models.legal import AdvisorRequest
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.platform.storage import get_storage
from app.schemas.legal import AdvisorRequestCreate
from app.services.documents.common import owner_map
from app.services.documents.export import render_pdf
from app.services.documents.files import upload_file
from app.services.documents.template_defs import disclaimer_for
from app.services.legal.errors import _validation
from app.services.team import service as team_svc

EVENT_CREATED = "legal.advisor_request.created"


def _target_of(
    db: Session, startup_id: uuid.UUID, *, document_id: uuid.UUID | None, file_id: uuid.UUID | None
) -> tuple[Document | None, DocumentFile | None]:
    """The attached document or file, or 404. Mirrors `funding.data_room._target_of`.

    A foreign or trashed row is a 404, never a 403: a 403 would confirm the id exists. A file
    that has not passed the virus scan is a 409 with the same code the data room uses, because
    D-LEG-18 applies to an attachment exactly as it applies to an upload.
    """
    if document_id is not None:
        doc = (
            db.query(Document)
            .filter(
                Document.id == document_id,
                Document.startup_id == startup_id,
                Document.trashed_at.is_(None),
            )
            .first()
        )
        if doc is None:
            raise NotFound()
        return doc, None
    file = (
        db.query(DocumentFile)
        .filter(
            DocumentFile.id == file_id,
            DocumentFile.startup_id == startup_id,
            DocumentFile.trashed_at.is_(None),
        )
        .first()
    )
    if file is None:
        raise NotFound()
    if file.scan_status != ScanStatus.clean:
        raise AppError(
            "FILE_NOT_CLEAN",
            "That file hasn't passed the virus scan, so it can't be sent for review.",
            409,
        )
    return None, file


def _check_advisor(db: Session, startup_id: uuid.UUID, advisor_user_id: uuid.UUID) -> None:
    """422 unless that user holds a LIVE `legal_advisor` membership in this workspace (D-LEG-10).

    A 422 rather than a 404: the founder chose from their own member list, so a bad value is a
    mistake in the form, not a hidden resource. The role is re-checked on delivery too, because
    a membership can end between the request and the notification.
    """
    live = (
        db.query(Membership.id)
        .filter(
            Membership.startup_id == startup_id,
            Membership.user_id == advisor_user_id,
            Membership.role == MembershipRole.legal_advisor,
            Membership.status == MembershipStatus.active,
        )
        .first()
    )
    if live is None:
        raise _validation("advisor_user_id", "Choose a legal advisor from this workspace.")


def _titles(db: Session, rows: list[AdvisorRequest]) -> dict[tuple[str, uuid.UUID], str]:
    """`{("document"|"file", id): title}` for every attachment and reviewed draft, in two
    queries rather than one per row."""
    doc_ids = {r.document_id for r in rows if r.document_id}
    file_ids = {r.file_id for r in rows if r.file_id}
    file_ids |= {r.returned_file_id for r in rows if r.returned_file_id}
    out: dict[tuple[str, uuid.UUID], str] = {}
    if doc_ids:
        for i, title in db.query(Document.id, Document.title).filter(Document.id.in_(doc_ids)):
            out[("document", i)] = title
    if file_ids:
        for i, name in db.query(DocumentFile.id, DocumentFile.filename).filter(
            DocumentFile.id.in_(file_ids)
        ):
            out[("file", i)] = name
    return out


def serialize_requests(db: Session, rows: list[AdvisorRequest]) -> list[dict[str, Any]]:
    """The founder's view. `serialize_for_advisor` is the deliberately narrower twin."""
    titles = _titles(db, rows)
    people = owner_map(
        db,
        {r.advisor_user_id for r in rows if r.advisor_user_id}
        | {r.created_by for r in rows if r.created_by},
    )
    return [
        {
            "id": str(r.id),
            "subject": r.subject,
            "note": r.note,
            "status": r.status.value,
            "attachment": _attachment(r, titles),
            "advisor": people.get(r.advisor_user_id) if r.advisor_user_id else None,
            "returned_file": (
                {
                    "id": str(r.returned_file_id),
                    "filename": titles.get(("file", r.returned_file_id)),
                }
                if r.returned_file_id
                else None
            ),
            "returned_at": r.returned_at.isoformat() if r.returned_at else None,
            "cancelled_at": r.cancelled_at.isoformat() if r.cancelled_at else None,
            "created_by": people.get(r.created_by) if r.created_by else None,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in rows
    ]


def _attachment(
    row: AdvisorRequest, titles: dict[tuple[str, uuid.UUID], str]
) -> dict[str, Any] | None:
    """The one attachment, named by `kind` so the FE needs no null-checking to tell them apart.
    None only when the attachment was hard-deleted under a cascade we do not expect to see."""
    if row.document_id:
        return {
            "kind": "document",
            "id": str(row.document_id),
            "title": titles.get(("document", row.document_id)),
        }
    if row.file_id:
        return {
            "kind": "file",
            "id": str(row.file_id),
            "title": titles.get(("file", row.file_id)),
        }
    return None


def serialize_request(db: Session, row: AdvisorRequest) -> dict[str, Any]:
    return serialize_requests(db, [row])[0]


def serialize_for_advisor(db: Session, rows: list[AdvisorRequest]) -> list[dict[str, Any]]:
    """What the advisor sees: the same request, deliberately narrower.

    No USER ids, mirroring `documents._external_comment` ("the author's name, but no internal
    user ids"). Resource ids stay: the advisor needs the attachment's and the reviewed draft's
    ids to fetch them. `advisor` is dropped entirely, because it is always the viewer and would
    only hand them their own id back. `created_by` becomes `requested_by` with the founder's
    NAME, since the advisor must know who asked, and `owner_map`'s own contract guarantees that
    is never any part of an address (D-LEG-15).
    """
    full = serialize_requests(db, rows)
    return [
        {
            **{k: v for k, v in r.items() if k not in ("advisor", "created_by")},
            "requested_by": {"name": r["created_by"]["name"]} if r["created_by"] else None,
        }
        for r in full
    ]


def serialize_one_for_advisor(db: Session, row: AdvisorRequest) -> dict[str, Any]:
    return serialize_for_advisor(db, [row])[0]


def is_advisor(role: MembershipRole) -> bool:
    """One place decides which shape a caller gets, so the route cannot drift from the query."""
    return role == MembershipRole.legal_advisor


def create_request(
    db: Session, *, startup_id: uuid.UUID, actor_id: uuid.UUID, data: AdvisorRequestCreate
) -> AdvisorRequest:
    """Validate everything BEFORE inserting, so a refused request leaves no row behind."""
    doc, file = _target_of(db, startup_id, document_id=data.document_id, file_id=data.file_id)
    _check_advisor(db, startup_id, data.advisor_user_id)
    row = AdvisorRequest(
        startup_id=startup_id,
        document_id=doc.id if doc else None,
        file_id=file.id if file else None,
        advisor_user_id=data.advisor_user_id,
        subject=data.subject,
        note=data.note,
        created_by=actor_id,
    )
    db.add(row)
    db.flush()
    return row


def list_requests(
    db: Session,
    *,
    startup_id: uuid.UUID,
    viewer_id: uuid.UUID,
    viewer_role: MembershipRole,
    status: AdvisorRequestStatus | None = None,
) -> list[AdvisorRequest]:
    """Newest first: a review request is correspondence, not a dated obligation.

    SCOPED BY ROLE, which is the whole point. A founder or team member sees the workspace's
    requests; a legal advisor sees only the ones naming them (D-LEG-11, gap row 45). The scoping
    lives here rather than in a separate advisor route, so there is no second path that could be
    left unscoped by mistake.
    """
    q = db.query(AdvisorRequest).filter_by(startup_id=startup_id)
    if viewer_role == MembershipRole.legal_advisor:
        q = q.filter(AdvisorRequest.advisor_user_id == viewer_id)
    if status is not None:
        q = q.filter(AdvisorRequest.status == status)
    return q.order_by(AdvisorRequest.created_at.desc()).all()


def get_request(
    db: Session,
    *,
    startup_id: uuid.UUID,
    request_id: uuid.UUID,
    viewer_id: uuid.UUID,
    viewer_role: MembershipRole,
) -> AdvisorRequest:
    """One request, or 404. An advisor who is not the named one gets the SAME 404 as a foreign
    id: a 403 would confirm that someone else's request exists."""
    row = db.query(AdvisorRequest).filter_by(id=request_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    if viewer_role == MembershipRole.legal_advisor and row.advisor_user_id != viewer_id:
        raise NotFound()
    return row


def created_payload(db: Session, row: AdvisorRequest) -> dict[str, Any]:
    """The notification payload for row 47. Carries the founder's NAME and the document's title,
    never an address and never an id the renderer would have to resolve: the advisor may be
    e-mailed this (D-LEG-15), and nothing they receive may show another member's address."""
    titles = _titles(db, [row])
    people = owner_map(db, {row.created_by} if row.created_by else set())
    founder = people.get(row.created_by) if row.created_by else None
    attachment = _attachment(row, titles)
    return {
        "startup_id": str(row.startup_id),
        "request_id": str(row.id),
        "advisor_user_id": str(row.advisor_user_id) if row.advisor_user_id else None,
        "founder_name": founder["name"] if founder else None,
        "document_title": attachment["title"] if attachment else None,
    }


def read_attachment(
    db: Session,
    *,
    startup_id: uuid.UUID,
    request_id: uuid.UUID,
    viewer_id: uuid.UUID,
    viewer_role: MembershipRole,
) -> tuple[bytes, str, str]:
    """`(content, content_type, filename)` of the request's attachment, or 404 (D-LEG-16).

    The request IS the access rule, which is why there is no grant and no share to check. The
    lead's conditions: the named advisor and the workspace's writers may read; anyone else, a
    foreign workspace and a **cancelled** request are all 404. A live `legal_advisor` membership
    is already guaranteed upstream, because `require_workspace` resolves only active memberships.

    An uploaded file is streamed as itself. A rich document has no bytes, so it is rendered to
    PDF exactly as the data room renders one, which also carries the not-legal-advice footer
    through `disclaimer_for`.

    A file that has stopped being `clean` is a 404 here rather than the 409 used on create
    (D-LEG-18): on create the founder picked the wrong file and can fix it, while on read the
    honest answer is simply that it is not available.
    """
    row = get_request(
        db,
        startup_id=startup_id,
        request_id=request_id,
        viewer_id=viewer_id,
        viewer_role=viewer_role,
    )
    if row.status == AdvisorRequestStatus.cancelled:
        raise NotFound()
    if row.file_id is not None:
        file = (
            db.query(DocumentFile)
            .filter(
                DocumentFile.id == row.file_id,
                DocumentFile.startup_id == startup_id,
                DocumentFile.trashed_at.is_(None),
            )
            .first()
        )
        if file is None or file.scan_status != ScanStatus.clean:
            raise NotFound()
        raw = get_storage().read_private(file.storage_key)
        if raw is None:
            raise NotFound()
        return raw, file.content_type, file.filename
    doc = (
        db.query(Document)
        .filter(
            Document.id == row.document_id,
            Document.startup_id == startup_id,
            Document.trashed_at.is_(None),
        )
        .first()
    )
    if doc is None:
        raise NotFound()
    pdf = render_pdf(doc, disclaimer_for(doc.template_key, doc.kind))
    return pdf, "application/pdf", f"{doc.title or 'document'}.pdf"


# --- the comment thread (gap row 45) ------------------------------------------------------------

ENTITY_TYPE = "advisor_request"


def _external_comment(c: dict[str, Any]) -> dict[str, Any]:
    """A comment as the advisor sees it, copying `documents._external_comment`: the author's
    name, but no internal user ids and no mention list."""
    author = c.get("author")
    return {
        **{k: v for k, v in c.items() if k not in ("mention_user_ids", "author")},
        "author": {"name": author.get("name")} if author else None,
    }


def list_comments(
    db: Session, *, row: AdvisorRequest, viewer_id: uuid.UUID, viewer_role: MembershipRole
) -> list[dict[str, Any]]:
    """The thread, oldest first. One thread per request (D-LEG-13), on the shared `comments`
    table: no new table, and its spare `anchor` column leaves room for per-version threads later
    without a migration."""
    rows = (
        db.query(Comment)
        .filter_by(startup_id=row.startup_id, entity_type=ENTITY_TYPE, entity_id=row.id)
        .order_by(Comment.created_at.asc(), Comment.id.asc())
        .all()
    )
    names = team_svc._names(db, {c.author_id for c in rows if c.author_id})
    out = [team_svc.serialize_comment(c, names, viewer_id) for c in rows]
    return [_external_comment(c) for c in out] if is_advisor(viewer_role) else out


def add_comment(
    db: Session,
    *,
    row: AdvisorRequest,
    author_id: uuid.UUID,
    viewer_role: MembershipRole,
    body: str,
) -> dict[str, Any]:
    """Add to the thread, and move a `requested` request to `in_review` on the ADVISOR's first
    comment (D-LEG-17).

    Not on their first read: a stray click must not tell the founder their lawyer is working on
    it, and the status cannot be moved back. A founder's own comment never changes the status,
    and a request that is already in review, returned or cancelled is left alone.
    """
    if row.status == AdvisorRequestStatus.cancelled:
        raise AppError(
            "REQUEST_CANCELLED",
            "This request was cancelled, so it can't take new comments.",
            409,
        )
    c = Comment(
        startup_id=row.startup_id,
        entity_type=ENTITY_TYPE,
        entity_id=row.id,
        author_id=author_id,
        body=body.strip(),
        mention_user_ids=[],
    )
    db.add(c)
    if is_advisor(viewer_role) and row.status == AdvisorRequestStatus.requested:
        row.status = AdvisorRequestStatus.in_review
    db.flush()
    names = team_svc._names(db, {author_id})
    out = team_svc.serialize_comment(c, names, author_id)
    return _external_comment(out) if is_advisor(viewer_role) else out


# --- the reviewed draft (gap row 45) ------------------------------------------------------------

# Matches the cap Documents puts on its own uploads (`_MAX_FILE_BYTES` in its endpoints module).
# Duplicated rather than imported, because that constant is private to another endpoint module,
# exactly as `_CHUNK_BYTES` is duplicated across four of them today.
MAX_DRAFT_BYTES = 15 * 1024 * 1024


def return_reviewed_draft(
    db: Session,
    *,
    row: AdvisorRequest,
    actor_id: uuid.UUID,
    filename: str,
    content_type: str,
    content: bytes,
) -> AdvisorRequest:
    """Store the advisor's marked-up draft and mark the request `returned` (gap row 45).

    It goes through Documents' own upload (`upload_file`): private from the first byte, scanned,
    and an ordinary library file afterwards, per the lead's condition 2. It is a NEW file, never
    a `DocumentVersion`, because `ShareAccess` has no edit level and every document write route
    is founder + team member, so an advisor can author no version by any path. That supersedes
    the version half of D-LEG-12.

    A draft that fails the scan refuses the return (D-LEG-18) and leaves the status alone. The
    file row stays in the library marked `blocked`, which is how Documents treats one anyway.

    A second upload moves the pointer; earlier drafts remain ordinary library files, so nothing
    the advisor sent is ever destroyed.
    """
    if row.status == AdvisorRequestStatus.cancelled:
        raise AppError(
            "REQUEST_CANCELLED",
            "This request was cancelled, so a reviewed draft can't be returned to it.",
            409,
        )
    startup = db.query(Startup).filter_by(id=row.startup_id).first()
    if startup is None:
        raise NotFound()
    file = upload_file(
        db,
        startup,
        uploaded_by_id=actor_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        content=content,
        folder=None,
    )
    if file.scan_status != ScanStatus.clean:
        raise FileNotClean()
    row.returned_file_id = file.id
    row.status = AdvisorRequestStatus.returned
    row.returned_at = datetime.now(UTC)
    db.flush()
    return row


# --- cancel (gap row 44 area, D-LEG-14) ---------------------------------------------------------


def cancel_request(db: Session, *, row: AdvisorRequest) -> AdvisorRequest:
    """Close a request that is still open. There is deliberately no reopen (D-LEG-14).

    409 on a terminal state, following `documents.signatures.cancel_request`, which refuses
    anything that is not still awaiting. Two different messages, because the two cases need
    different things from the reader: one is already done, the other was already cancelled.

    Cancelling ends the advisor's access to the attachment with nothing to revoke, because the
    request IS the access rule: `read_attachment` 404s on a cancelled request, and the thread
    stops taking new comments while staying readable.
    """
    if row.status == AdvisorRequestStatus.cancelled:
        raise AppError("REQUEST_CANCELLED", "This request was already cancelled.", 409)
    if row.status == AdvisorRequestStatus.returned:
        raise AppError(
            "REQUEST_COMPLETE",
            "This review is already complete, so it can't be cancelled.",
            409,
        )
    row.status = AdvisorRequestStatus.cancelled
    row.cancelled_at = datetime.now(UTC)
    db.flush()
    return row

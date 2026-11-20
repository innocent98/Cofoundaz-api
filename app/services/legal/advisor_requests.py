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
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.document import Document, DocumentFile
from app.db.models.enums import (
    AdvisorRequestStatus,
    MembershipRole,
    MembershipStatus,
    ScanStatus,
)
from app.db.models.legal import AdvisorRequest
from app.db.models.membership import Membership
from app.schemas.legal import AdvisorRequestCreate
from app.services.documents.common import owner_map
from app.services.legal.errors import _validation

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
    db: Session, *, startup_id: uuid.UUID, status: AdvisorRequestStatus | None = None
) -> list[AdvisorRequest]:
    """Newest first: a review request is a piece of correspondence, not a dated obligation."""
    q = db.query(AdvisorRequest).filter_by(startup_id=startup_id)
    if status is not None:
        q = q.filter(AdvisorRequest.status == status)
    return q.order_by(AdvisorRequest.created_at.desc()).all()


def get_request(db: Session, *, startup_id: uuid.UUID, request_id: uuid.UUID) -> AdvisorRequest:
    row = db.query(AdvisorRequest).filter_by(id=request_id, startup_id=startup_id).first()
    if row is None:
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

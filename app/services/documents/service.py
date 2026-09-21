import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, DocumentVersionConflict, NotFound
from app.db.models.document import Document
from app.db.models.enums import DocumentKind, DocumentStatus
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.platform.events import event_bus


def validate_sections(sections: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, sec in enumerate(sections):
        if not isinstance(sec, dict):
            raise AppError(
                "VALIDATION_ERROR",
                "Each section must be an object.",
                422,
                field_errors=[{"field": f"sections.{i}", "message": "Expected an object."}],
            )
        heading, body = sec.get("heading"), sec.get("body")
        if not isinstance(heading, str) or not isinstance(body, str):
            raise AppError(
                "VALIDATION_ERROR",
                "Each section needs text heading and body.",
                422,
                field_errors=[{"field": f"sections.{i}", "message": "heading/body must be text."}],
            )
        sid = sec.get("id")
        out.append({"id": str(sid) if sid else str(uuid.uuid4()), "heading": heading, "body": body})
    return out


def create_document(
    db: Session,
    startup: Startup,
    *,
    created_by_id: uuid.UUID,
    kind: DocumentKind,
    title: str,
    sections: list[Any],
    folder: str | None,
    template_key: str | None,
    ai_generated: bool = False,
) -> Document:
    doc = Document(
        startup_id=startup.id,
        created_by_id=created_by_id,
        kind=kind,
        title=title,
        sections=validate_sections(sections),
        folder=folder,
        template_key=template_key,
        ai_generated=ai_generated,
    )
    db.add(doc)
    db.flush()
    event_bus.publish(
        db,
        "document.created",
        {"startup_id": str(startup.id), "document_id": str(doc.id), "kind": doc.kind.value},
    )
    return doc


def list_documents(
    db: Session,
    startup: Startup,
    *,
    kind: DocumentKind | None,
    folder: str | None,
    status: DocumentStatus | None,
) -> list[Document]:
    q = db.query(Document).filter_by(startup_id=startup.id)
    if kind is not None:
        q = q.filter_by(kind=kind)
    if folder is not None:
        q = q.filter_by(folder=folder)
    if status is not None:
        q = q.filter_by(status=status)
    return q.order_by(Document.updated_at.desc()).all()


def get_document(db: Session, membership: Membership, document_id: Any) -> Document:
    doc = db.query(Document).filter_by(id=document_id, startup_id=membership.startup_id).first()
    if doc is None:
        raise NotFound()
    return doc


def update_document(
    db: Session,
    doc: Document,
    *,
    title: str,
    sections: list[Any],
    status: DocumentStatus,
    folder: str | None,
    expected_version: int,
) -> Document:
    if expected_version != doc.version:
        raise DocumentVersionConflict()
    doc.title = title
    doc.sections = validate_sections(sections)
    doc.status = status
    doc.folder = folder
    doc.version += 1
    db.flush()
    return doc


def delete_document(db: Session, doc: Document) -> None:
    db.delete(doc)
    db.flush()


def serialize_summary(doc: Document) -> dict[str, Any]:
    return {
        "id": str(doc.id),
        "kind": doc.kind.value,
        "title": doc.title,
        "status": doc.status.value,
        "ai_generated": doc.ai_generated,
        "folder": doc.folder,
        "template_key": doc.template_key,
        "version": doc.version,
        "updated_at": doc.updated_at.isoformat(),
    }


def serialize_document(doc: Document) -> dict[str, Any]:
    return {**serialize_summary(doc), "sections": doc.sections}

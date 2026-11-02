import copy
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.document import Document, DocumentVersion
from app.db.models.user import UserProfile
from app.platform.events import event_bus
from app.services.documents.common import display_name, search_text_of


def snapshot(
    db: Session,
    doc: Document,
    *,
    author_id: uuid.UUID | None,
    note: str | None = None,
    announce: bool = True,
) -> DocumentVersion:
    """Record `doc` as it is NOW under its current version number. The caller has already
    bumped `doc.version` and holds the row lock, so the (document, version) pair is unique.

    Publishes `document.version.created` for every version after the first (the first is the
    document being created, which has its own event)."""
    row = DocumentVersion(
        startup_id=doc.startup_id,
        document_id=doc.id,
        version=doc.version,
        title=doc.title,
        status=doc.status,
        sections=copy.deepcopy(doc.sections),
        author_id=author_id,
        note=note,
    )
    db.add(row)
    db.flush()
    if announce and doc.version > 1:
        event_bus.publish(
            db,
            "document.version.created",
            {
                "startup_id": str(doc.startup_id),
                "document_id": str(doc.id),
                "version": doc.version,
                "title": doc.title,
                "actor_id": str(author_id) if author_id else None,
                "author_name": display_name(db, author_id),
                "owner_id": str(doc.created_by_id) if doc.created_by_id else None,
                "href": f"/documents/{doc.id}",
            },
        )
    return row


def version_authors(db: Session, rows: list[DocumentVersion]) -> dict[uuid.UUID, str | None]:
    ids = {r.author_id for r in rows if r.author_id}
    if not ids:
        return {}
    return dict(
        db.query(UserProfile.user_id, UserProfile.full_name)
        .filter(UserProfile.user_id.in_(ids))
        .tuples()
        .all()
    )


def list_versions(db: Session, doc: Document) -> list[dict[str, Any]]:
    """Newest first. Summaries only (no content): open one version to read it."""
    rows = (
        db.query(DocumentVersion)
        .filter_by(document_id=doc.id)
        .order_by(DocumentVersion.version.desc())
        .all()
    )
    names = version_authors(db, rows)
    return [serialize_version(r, names, current=doc.version) for r in rows]


def get_version(db: Session, doc: Document, number: int) -> DocumentVersion:
    row = db.query(DocumentVersion).filter_by(document_id=doc.id, version=number).first()
    if row is None:
        raise NotFound()
    return row


def restore_version(
    db: Session, doc: Document, number: int, *, author_id: uuid.UUID
) -> DocumentVersion:
    """Make version `number`'s content the current content as a NEW version (history is never
    rewritten). Restoring the current version is allowed and just adds a no-change entry."""
    source = get_version(db, doc, number)
    doc.title = source.title
    doc.status = source.status
    doc.sections = copy.deepcopy(source.sections)
    doc.search_text = search_text_of(doc.sections)
    doc.version += 1
    db.flush()
    return snapshot(db, doc, author_id=author_id, note=f"Restored from version {number}")


def serialize_version(
    row: DocumentVersion,
    names: dict[uuid.UUID, str | None],
    *,
    current: int,
    with_content: bool = False,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "version": row.version,
        "title": row.title,
        "status": row.status.value,
        "note": row.note,
        "author": (
            {"id": str(row.author_id), "name": names.get(row.author_id)} if row.author_id else None
        ),
        "created_at": row.created_at.isoformat(),
        "is_current": row.version == current,
    }
    if with_content:
        out["sections"] = row.sections
    return out

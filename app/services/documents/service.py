import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Text, and_, cast, func, or_
from sqlalchemy.orm import Session

from app.core.errors import AppError, DocumentVersionConflict, NotFound
from app.db.models.comment import Comment
from app.db.models.document import Document, DocumentGrant, DocumentShare
from app.db.models.enums import DocumentKind, DocumentStatus
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.documents.common import (  # noqa: F401 - re-exported for the other modules
    DOCUMENT_MIME,
    MAX_FOLDER_DEPTH,
    MAX_FOLDER_PATH,
    MAX_FOLDER_SEGMENT,
    MAX_SEARCH_TERMS,
    MAX_TAG_LENGTH,
    MAX_TAGS,
    _invalid,
    display_name,
    normalize_folder,
    normalize_tags,
    owner_map,
    search_text_of,
)
from app.services.documents.folders import ensure_path
from app.services.documents.template_defs import disclaimer_for
from app.services.documents.versions import snapshot

TRASH_RETENTION_DAYS = 30


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
    tags: list[str] | None = None,
) -> Document:
    clean = validate_sections(sections)
    path = normalize_folder(folder)
    doc = Document(
        startup_id=startup.id,
        created_by_id=created_by_id,
        kind=kind,
        title=title,
        sections=clean,
        search_text=search_text_of(clean),
        folder=path,
        tags=normalize_tags(tags or []),
        template_key=template_key,
        ai_generated=ai_generated,
    )
    db.add(doc)
    db.flush()
    ensure_path(db, startup.id, path)
    snapshot(db, doc, author_id=created_by_id, note="Created", announce=False)
    event_bus.publish(
        db,
        "document.created",
        {"startup_id": str(startup.id), "document_id": str(doc.id), "kind": doc.kind.value},
    )
    return doc


def _search_clause(model: Any, q: str, *, with_body: bool) -> Any:
    """Every whitespace-separated term must appear (case-insensitive substring) in the name, the
    tags or, for documents, the body text. Substring rather than word match so a half-typed word
    still finds its document."""
    clauses = []
    for term in q.split()[:MAX_SEARCH_TERMS]:
        pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        title_col = model.title if with_body else model.filename
        parts = [
            title_col.ilike(pattern, escape="\\"),
            cast(model.tags, Text).ilike(pattern, escape="\\"),
        ]
        if with_body:
            parts.append(model.search_text.ilike(pattern, escape="\\"))
        clauses.append(or_(*parts))
    return and_(*clauses)


def list_documents(
    db: Session,
    startup: Startup,
    *,
    kind: DocumentKind | None,
    folder: str | None,
    status: DocumentStatus | None,
    q: str | None = None,
    tag: str | None = None,
    trashed: bool = False,
) -> list[Document]:
    query = db.query(Document).filter_by(startup_id=startup.id)
    query = query.filter(
        Document.trashed_at.isnot(None) if trashed else Document.trashed_at.is_(None)
    )
    if kind is not None:
        query = query.filter_by(kind=kind)
    if folder is not None:
        query = query.filter_by(folder=folder)
    if status is not None:
        query = query.filter_by(status=status)
    if tag:
        query = query.filter(
            func.lower(cast(Document.tags, Text)).contains(f'"{tag.lower()}"', autoescape=True)
        )
    if q and q.split():
        query = query.filter(_search_clause(Document, q, with_body=True))
    order = Document.trashed_at.desc() if trashed else Document.updated_at.desc()
    return query.order_by(order).all()


def get_document(
    db: Session,
    membership: Membership,
    document_id: Any,
    *,
    trashed: bool | None = False,
    lock: bool = False,
) -> Document:
    """The workspace's live document (or, with `trashed=True`, its trashed one) or 404. A
    trashed document is invisible to every normal route; only the trash routes ask for it."""
    q = db.query(Document).filter_by(id=document_id, startup_id=membership.startup_id)
    if trashed is not None:
        q = q.filter(Document.trashed_at.isnot(None) if trashed else Document.trashed_at.is_(None))
    doc = (q.with_for_update() if lock else q).first()
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
    author_id: uuid.UUID | None = None,
) -> Document:
    if expected_version != doc.version:
        raise DocumentVersionConflict()
    clean = validate_sections(sections)
    path = normalize_folder(folder)
    doc.title = title
    doc.sections = clean
    doc.search_text = search_text_of(clean)
    doc.status = status
    doc.folder = path
    doc.version += 1
    db.flush()
    ensure_path(db, doc.startup_id, path)
    snapshot(db, doc, author_id=author_id)
    return doc


def update_metadata(
    db: Session,
    doc: Document,
    *,
    fields: dict[str, Any],
) -> Document:
    """Rename / move / tag / set status without touching the content: no new version."""
    if "title" in fields:
        title = (fields["title"] or "").strip()
        if not title:
            raise _invalid("title", "A document needs a name.")
        if len(title) > 200:
            raise _invalid("title", "A document name can be at most 200 characters.")
        doc.title = title
    if "folder" in fields:
        doc.folder = normalize_folder(fields["folder"])
        ensure_path(db, doc.startup_id, doc.folder)
    if "tags" in fields:
        doc.tags = normalize_tags(fields["tags"] or [])
    if "status" in fields and fields["status"] is not None:
        doc.status = fields["status"]
    db.flush()
    return doc


def trash_document(db: Session, doc: Document, *, actor_id: uuid.UUID) -> Document:
    doc.trashed_at = dt.datetime.now(dt.UTC)
    doc.trashed_by_id = actor_id
    db.flush()
    event_bus.publish(
        db,
        "document.trashed",
        {"startup_id": str(doc.startup_id), "document_id": str(doc.id), "actor_id": str(actor_id)},
    )
    return doc


def restore_document(db: Session, doc: Document, *, actor_id: uuid.UUID) -> Document:
    doc.trashed_at = None
    doc.trashed_by_id = None
    ensure_path(db, doc.startup_id, doc.folder)  # the folder may have been deleted meanwhile
    db.flush()
    event_bus.publish(
        db,
        "document.restored",
        {"startup_id": str(doc.startup_id), "document_id": str(doc.id), "actor_id": str(actor_id)},
    )
    return doc


def delete_document(db: Session, doc: Document) -> None:
    """Permanent deletion (empty-trash and the purge job). Versions, grants and token links go
    with the row."""
    _delete_comments(db, [doc.id])
    db.delete(doc)
    db.flush()


def _delete_comments(db: Session, document_ids: list[uuid.UUID]) -> None:
    """Comments point at a document softly (no FK), so they go with it explicitly."""
    db.query(Comment).filter(
        Comment.entity_type == "document", Comment.entity_id.in_(document_ids)
    ).delete(synchronize_session=False)


def purge_expired_documents(db: Session, *, now: dt.datetime, limit: int = 500) -> int:
    cutoff = now - dt.timedelta(days=TRASH_RETENTION_DAYS)
    rows = (
        db.query(Document)
        .filter(Document.trashed_at.isnot(None), Document.trashed_at <= cutoff)
        .order_by(Document.trashed_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .all()
    )
    for doc in rows:
        event_bus.publish(
            db,
            "document.purged",
            {"startup_id": str(doc.startup_id), "document_id": str(doc.id)},
        )
        db.delete(doc)
    if rows:
        _delete_comments(db, [d.id for d in rows])
    db.flush()
    return len(rows)


def purges_at(trashed_at: dt.datetime | None) -> str | None:
    if trashed_at is None:
        return None
    return (trashed_at + dt.timedelta(days=TRASH_RETENTION_DAYS)).isoformat()


def _share_counts(db: Session, docs: list[Document]) -> dict[uuid.UUID, int]:
    """Active token links plus active per-person grants, per document."""
    if not docs:
        return {}
    ids = [d.id for d in docs]
    now = dt.datetime.now(dt.UTC)
    counts: dict[uuid.UUID, int] = {}
    links = (
        db.query(DocumentShare.document_id, func.count())
        .filter(
            DocumentShare.document_id.in_(ids),
            DocumentShare.revoked_at.is_(None),
            or_(DocumentShare.expires_at.is_(None), DocumentShare.expires_at > now),
        )
        .group_by(DocumentShare.document_id)
        .all()
    )
    grants = (
        db.query(DocumentGrant.document_id, func.count())
        .filter(DocumentGrant.document_id.in_(ids), DocumentGrant.revoked_at.is_(None))
        .group_by(DocumentGrant.document_id)
        .all()
    )
    for did, n in [*links, *grants]:
        counts[did] = counts.get(did, 0) + int(n)
    return counts


def serialize_summary(
    doc: Document, *, owner: dict[str, Any] | None = None, share_count: int = 0
) -> dict[str, Any]:
    return {
        "id": str(doc.id),
        "type": "document",
        "kind": doc.kind.value,
        "mime": DOCUMENT_MIME,
        "title": doc.title,
        "status": doc.status.value,
        "ai_generated": doc.ai_generated,
        "folder": doc.folder,
        "tags": list(doc.tags or []),
        "template_key": doc.template_key,
        "disclaimer": disclaimer_for(doc.template_key, doc.kind),
        "version": doc.version,
        "owner": owner,
        "share_count": share_count,
        "updated_at": doc.updated_at.isoformat(),
        "trashed_at": doc.trashed_at.isoformat() if doc.trashed_at else None,
        "purges_at": purges_at(doc.trashed_at),
    }


def serialize_summaries(db: Session, docs: list[Document]) -> list[dict[str, Any]]:
    owners = owner_map(db, {d.created_by_id for d in docs if d.created_by_id})
    counts = _share_counts(db, docs)
    return [
        serialize_summary(
            d,
            owner=owners.get(d.created_by_id) if d.created_by_id else None,
            share_count=counts.get(d.id, 0),
        )
        for d in docs
    ]


def serialize_document(doc: Document, *, db: Session | None = None) -> dict[str, Any]:
    if db is None:
        return {**serialize_summary(doc), "sections": doc.sections}
    return {**serialize_summaries(db, [doc])[0], "sections": doc.sections}


# What the holder of a public token link may see: content only, none of our bookkeeping.
PUBLIC_FIELDS = (
    "kind",
    "title",
    "status",
    "ai_generated",
    "template_key",
    "disclaimer",
    "version",
    "updated_at",
    "sections",
)


def serialize_public(doc: Document) -> dict[str, Any]:
    full = {**serialize_summary(doc), "sections": doc.sections}
    return {k: full[k] for k in PUBLIC_FIELDS}

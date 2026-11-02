import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Text, cast, func, or_
from sqlalchemy.orm import Session

from app.core.errors import AppError, FileHasSignatureRequests, FileNotClean, NotFound
from app.core.logger import log
from app.db.models.document import DocumentFile, DocumentShare, SignatureRequest
from app.db.models.enums import ScanStatus
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.platform.signed_urls import sign_key
from app.platform.storage import LocalStorage, Storage, get_storage
from app.services.documents.folders import ensure_path
from app.services.documents.scanner import scan_file
from app.services.documents.service import (
    TRASH_RETENTION_DAYS,
    _search_clause,
    normalize_folder,
    normalize_tags,
    owner_map,
    purges_at,
)
from app.services.documents.sniff import ALLOWED_TYPES, MARKDOWN

# content-type -> file extension. Also the upload allowlist; `sniff.ALLOWED_TYPES` must list
# exactly the same types (asserted below).
EXT_BY_CONTENT_TYPE: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "text/plain": ".txt",
    "text/csv": ".csv",
    MARKDOWN: ".md",
}
assert set(EXT_BY_CONTENT_TYPE) == ALLOWED_TYPES  # nosec B101 - import-time invariant


FILE_LINK_TTL_SECONDS = 300
LOCAL_DOWNLOAD_PATH = "/api/v1/documents/files/download"
LOCAL_CONTENT_PATH = "/api/v1/documents/files/content"

# How the in-app viewer should render a file, by its (sniffed) content type.
VIEWER_BY_CONTENT_TYPE: dict[str, str] = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "text/plain": "text",
    "text/csv": "csv",
    MARKDOWN: "markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}


def upload_file(
    db: Session,
    startup: Startup,
    *,
    uploaded_by_id: uuid.UUID,
    filename: str,
    content_type: str,
    size_bytes: int,
    content: bytes,
    folder: str | None,
) -> DocumentFile:
    folder = normalize_folder(folder)
    ext = EXT_BY_CONTENT_TYPE.get(content_type, "")
    storage_key = f"documents/{startup.id}/{uuid.uuid4().hex}{ext}"
    # Private from the first byte: no stable public URL ever exists, so the role gate, the scan
    # gate and request cancellation actually control access to the content.
    get_storage().save_private(storage_key, content, content_type)
    row = DocumentFile(
        startup_id=startup.id,
        uploaded_by_id=uploaded_by_id,
        folder=folder,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        storage_key=storage_key,
        url=None,
        is_private=True,
        scan_status=ScanStatus.pending,
    )
    db.add(row)
    db.flush()
    ensure_path(db, startup.id, folder)
    scan_file(db, row, content)
    event_bus.publish(
        db,
        "document.file.uploaded",
        {"startup_id": str(startup.id), "file_id": str(row.id), "content_type": content_type},
    )
    return row


def list_files(
    db: Session,
    startup: Startup,
    *,
    folder: str | None,
    q: str | None = None,
    tag: str | None = None,
    trashed: bool = False,
) -> list[DocumentFile]:
    query = db.query(DocumentFile).filter_by(startup_id=startup.id)
    query = query.filter(
        DocumentFile.trashed_at.isnot(None) if trashed else DocumentFile.trashed_at.is_(None)
    )
    if folder is not None:
        query = query.filter_by(folder=folder)
    if tag:
        query = query.filter(
            func.lower(cast(DocumentFile.tags, Text)).contains(f'"{tag.lower()}"', autoescape=True)
        )
    if q and q.split():
        query = query.filter(_search_clause(DocumentFile, q, with_body=False))
    order = DocumentFile.trashed_at.desc() if trashed else DocumentFile.created_at.desc()
    return query.order_by(order).all()


def get_file(
    db: Session,
    membership: Membership,
    file_id: Any,
    *,
    lock: bool = False,
    trashed: bool | None = False,
) -> DocumentFile:
    """The workspace's live file (or, with `trashed=True`, its trashed one) or 404. `lock=True`
    takes `SELECT ... FOR UPDATE` on the row so a concurrent delete and signature-request create
    serialise instead of racing. A trashed file is invisible to every route but the trash ones."""
    q = db.query(DocumentFile).filter_by(id=file_id, startup_id=membership.startup_id)
    if trashed is not None:
        q = q.filter(
            DocumentFile.trashed_at.isnot(None) if trashed else DocumentFile.trashed_at.is_(None)
        )
    row = (q.with_for_update() if lock else q).first()
    if row is None:
        raise NotFound()
    return row


def has_signature_records(db: Session, file_id: uuid.UUID) -> bool:
    """True when a signature request was sent from this file or produced it as its executed copy:
    either way it is part of a signed record and must outlive any delete."""
    return (
        db.query(SignatureRequest.id)
        .filter(
            or_(SignatureRequest.file_id == file_id, SignatureRequest.executed_file_id == file_id)
        )
        .first()
        is not None
    )


def assert_clean(file: DocumentFile) -> None:
    """409 unless the file has passed the scan. Gate for sending a file to signers, for issuing
    any download link, and for sharing when files become shareable."""
    if file.scan_status != ScanStatus.clean:
        raise FileNotClean()


def delete_file(db: Session, file: DocumentFile) -> tuple[str, bool]:
    """Remove the row and return `(storage_key, is_private)` for `purge_object`.

    The caller must hold the row lock (`get_file(..., lock=True)`) and call `purge_object` only
    AFTER committing: deleting the stored object first would orphan or destroy data if the commit
    then fails. Refuses (409) while any signature request references the file: the request, and
    above all a completed one, is the signed record and must outlive the upload.
    """
    if has_signature_records(db, file.id):
        raise FileHasSignatureRequests()
    target = (file.storage_key, file.is_private)
    db.delete(file)
    db.flush()
    event_bus.publish(
        db,
        "document.file.deleted",
        {"startup_id": str(file.startup_id), "file_id": str(file.id)},
    )
    return target


def trash_file(db: Session, file: DocumentFile, *, actor_id: uuid.UUID) -> DocumentFile:
    """Soft delete. A file with any signature request is refused (409) exactly like a hard
    delete: the request, a completed one above all, is the signed record and needs its file."""
    if has_signature_records(db, file.id):
        raise FileHasSignatureRequests()
    file.trashed_at = dt.datetime.now(dt.UTC)
    file.trashed_by_id = actor_id
    db.flush()
    event_bus.publish(
        db,
        "document.file.trashed",
        {"startup_id": str(file.startup_id), "file_id": str(file.id), "actor_id": str(actor_id)},
    )
    return file


def restore_file(db: Session, file: DocumentFile, *, actor_id: uuid.UUID) -> DocumentFile:
    file.trashed_at = None
    file.trashed_by_id = None
    ensure_path(db, file.startup_id, file.folder)
    db.flush()
    event_bus.publish(
        db,
        "document.file.restored",
        {"startup_id": str(file.startup_id), "file_id": str(file.id), "actor_id": str(actor_id)},
    )
    return file


def update_file_metadata(
    db: Session, file: DocumentFile, *, fields: dict[str, Any]
) -> DocumentFile:
    """Rename / move / tag. The stored object and its content type never change."""
    if "filename" in fields:
        name = (fields["filename"] or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not name:
            raise AppError("VALIDATION_ERROR", "A file needs a name.", 422)
        if len(name) > 255:
            raise AppError("VALIDATION_ERROR", "A file name can be at most 255 characters.", 422)
        file.filename = name
    if "folder" in fields:
        file.folder = normalize_folder(fields["folder"])
        ensure_path(db, file.startup_id, file.folder)
    if "tags" in fields:
        file.tags = normalize_tags(fields["tags"] or [])
    db.flush()
    return file


def purge_expired_files(
    db: Session, *, now: dt.datetime, limit: int = 500
) -> list[tuple[str, bool]]:
    """Hard-delete files that have sat in the trash for the retention period. Returns the stored
    objects for `purge_object`, to be removed only AFTER the caller commits. A file that somehow
    gained a signature request is left alone (it cannot be trashed with one)."""
    cutoff = now - dt.timedelta(days=TRASH_RETENTION_DAYS)
    rows = (
        db.query(DocumentFile)
        .filter(DocumentFile.trashed_at.isnot(None), DocumentFile.trashed_at <= cutoff)
        .order_by(DocumentFile.trashed_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .all()
    )
    targets = []
    for f in rows:
        if has_signature_records(db, f.id):
            continue
        targets.append(delete_file(db, f))
    return targets


def purge_object(target: tuple[str, bool]) -> None:
    """Delete the stored object after the row's deletion has committed. A failure leaves an
    orphaned object (logged), never a row pointing at nothing."""
    key, private = target
    storage = get_storage()
    try:
        (storage.delete_private if private else storage.delete)(key)
    except Exception:  # noqa: BLE001 - the row is already gone; an orphan is the lesser harm
        log.exception("document object {} was not removed after its row was deleted", key)


def ensure_private(file: DocumentFile) -> Storage:
    """Move a legacy public file behind authentication (idempotent) and return the storage."""
    storage = get_storage()
    if not file.is_private:
        storage.make_private(file.storage_key)
        file.is_private = True
        file.url = None
    return storage


def file_link(file: DocumentFile, *, api_public_url: str, inline: bool = False) -> dict[str, Any]:
    """A short-lived signed download link for `file` (caller has already authorised the reader).

    Refuses (409) a file that is not `clean`. A legacy public file is moved behind authentication
    first; if that fails the link is refused rather than handing out the public URL.
    """
    assert_clean(file)
    storage = ensure_private(file)
    url = storage.private_url(file.storage_key, FILE_LINK_TTL_SECONDS)
    if url is None:
        token, expires = sign_key(
            file.storage_key, startup_id=str(file.startup_id), ttl_seconds=FILE_LINK_TTL_SECONDS
        )
        path = LOCAL_CONTENT_PATH if inline else LOCAL_DOWNLOAD_PATH
        url = f"{api_public_url.rstrip('/')}{path}?token={token}"
    else:
        expires = int(dt.datetime.now(dt.UTC).timestamp()) + FILE_LINK_TTL_SECONDS
    out = {
        "url": url,
        "expires_at": dt.datetime.fromtimestamp(expires, dt.UTC).isoformat(),
        "content_type": file.content_type,
        "filename": file.filename,
    }
    if inline:
        out["viewer"] = VIEWER_BY_CONTENT_TYPE.get(file.content_type, "download")
    return out


def local_path(file: DocumentFile) -> Any:
    """The on-disk path of a locally stored file, or None (missing, or a remote backend)."""
    storage = get_storage()
    return storage.open_private(file.storage_key) if isinstance(storage, LocalStorage) else None


def file_by_key(db: Session, *, startup_id: str, key: str) -> DocumentFile | None:
    try:
        sid = uuid.UUID(startup_id)
    except ValueError:
        return None
    return (
        db.query(DocumentFile)
        .filter_by(startup_id=sid, storage_key=key)
        .filter(DocumentFile.trashed_at.is_(None))
        .first()
    )


def serialize_file(
    f: DocumentFile,
    *,
    owner: dict[str, Any] | None = None,
    share_count: int = 0,
    executed: bool = False,
) -> dict[str, Any]:
    """The file summary. There is deliberately no `url`: content is read through
    `GET /documents/files/{id}/download` or `/content` (a short-lived signed link) only."""
    return {
        "id": str(f.id),
        "filename": f.filename,
        "content_type": f.content_type,
        "mime": f.content_type,
        "type": "file",
        "origin": (
            "executed" if executed else "export" if f.source_version is not None else "upload"
        ),
        "source_document_id": str(f.source_document_id) if f.source_document_id else None,
        "source_version": f.source_version,
        "size_bytes": f.size_bytes,
        "folder": f.folder,
        "tags": list(f.tags or []),
        "owner": owner,
        "share_count": share_count,
        "scan_status": f.scan_status.value,
        "uploaded_at": f.created_at.isoformat(),
        "trashed_at": f.trashed_at.isoformat() if f.trashed_at else None,
        "purges_at": purges_at(f.trashed_at),
    }


def _file_share_counts(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Live (not revoked, not expired) shares per file."""
    if not ids:
        return {}
    now = dt.datetime.now(dt.UTC)
    rows = (
        db.query(DocumentShare.file_id, func.count())
        .filter(
            DocumentShare.file_id.in_(ids),
            DocumentShare.revoked_at.is_(None),
            or_(DocumentShare.expires_at.is_(None), DocumentShare.expires_at > now),
        )
        .group_by(DocumentShare.file_id)
        .all()
    )
    return {fid: n for fid, n in rows if fid is not None}


def serialize_files(db: Session, files: list[DocumentFile]) -> list[dict[str, Any]]:
    owners = owner_map(db, {f.uploaded_by_id for f in files if f.uploaded_by_id})
    ids = [f.id for f in files]
    counts = _file_share_counts(db, ids)
    executed = (
        {
            eid
            for (eid,) in db.query(SignatureRequest.executed_file_id)
            .filter(SignatureRequest.executed_file_id.in_(ids))
            .all()
        }
        if ids
        else set()
    )
    return [
        serialize_file(
            f,
            owner=owners.get(f.uploaded_by_id) if f.uploaded_by_id else None,
            share_count=counts.get(f.id, 0),
            executed=f.id in executed,
        )
        for f in files
    ]

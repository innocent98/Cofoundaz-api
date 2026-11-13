"""Marketplace uploads: allowlist by purpose, magic-byte sniff, size cap, private storage.

Reuses the Documents sniffing (`documents/sniff.py`) and scanner hook rather than a second copy of
either. The client's `Content-Type` is a claim; the bytes decide. Nothing is ever public: owners
read their own files and staff read credentials through audited routes."""

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import MarketplaceFilePurpose, ScanStatus
from app.db.models.marketplace import (
    MarketplaceBooking,
    MarketplaceFile,
    MarketplaceProvider,
    MarketplaceProviderPortfolio,
    MarketplaceThread,
)
from app.platform.storage import get_storage
from app.services.documents.files import EXT_BY_CONTENT_TYPE
from app.services.documents.scanner import get_scanner
from app.services.documents.sniff import (
    CSV,
    DOCX,
    JPEG,
    PDF,
    PNG,
    PPTX,
    TEXT,
    XLSX,
    declared_type,
    sniff_content_type,
)
from app.services.marketplace import identity
from app.services.marketplace.constants import (
    MAX_AVATAR_BYTES,
    MAX_BRIEF_FILE_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES_PER_PURPOSE,
    MAX_UNATTACHED_FILES,
)

ALLOWED_BY_PURPOSE: dict[MarketplaceFilePurpose, frozenset[str]] = {
    MarketplaceFilePurpose.credential: frozenset({PDF, PNG, JPEG}),
    MarketplaceFilePurpose.portfolio: frozenset({PDF, PNG, JPEG, PPTX}),
    MarketplaceFilePurpose.avatar: frozenset({PNG, JPEG}),
    # D-MP-L10: what a founder may send with a brief or a message; deliveries may also carry
    # slides and plain data files.
    MarketplaceFilePurpose.brief: frozenset({PDF, DOCX, XLSX, PNG, JPEG}),
    MarketplaceFilePurpose.message: frozenset({PDF, DOCX, XLSX, PNG, JPEG}),
    MarketplaceFilePurpose.delivery: frozenset({PDF, DOCX, XLSX, PPTX, PNG, JPEG, TEXT, CSV}),
}
# Purposes that travel between a founder and an expert once attached to a booking / thread.
SHARED_PURPOSES = frozenset(
    {MarketplaceFilePurpose.brief, MarketplaceFilePurpose.delivery, MarketplaceFilePurpose.message}
)
FOUNDER_UPLOADS = frozenset({MarketplaceFilePurpose.brief, MarketplaceFilePurpose.message})
PROVIDER_UPLOADS = frozenset({MarketplaceFilePurpose.delivery, MarketplaceFilePurpose.message})
_HINT = {
    MarketplaceFilePurpose.avatar: "Upload a PNG or JPEG image.",
    MarketplaceFilePurpose.brief: "Upload a PDF, Word, Excel, PNG or JPEG file.",
    MarketplaceFilePurpose.message: "Upload a PDF, Word, Excel, PNG or JPEG file.",
    MarketplaceFilePurpose.delivery: "Upload a PDF, Office, image, text or CSV file.",
}
_UNSAFE_NAME = re.compile(r"[^\w .()\-]+")


def _size_limit(purpose: MarketplaceFilePurpose) -> int:
    if purpose == MarketplaceFilePurpose.avatar:
        return MAX_AVATAR_BYTES
    if purpose in (MarketplaceFilePurpose.brief, MarketplaceFilePurpose.message):
        return MAX_BRIEF_FILE_BYTES
    return MAX_FILE_BYTES


def size_limit(purpose: MarketplaceFilePurpose) -> int:
    return _size_limit(purpose)


def _err(code: str, message: str, status: int = 422) -> AppError:
    return AppError(code=code, message=message, http_status=status)


def safe_filename(raw: str | None) -> str:
    base = re.split(r"[\\/]", raw or "")[-1]
    cleaned = _UNSAFE_NAME.sub("_", base).strip(" .")[:200]
    return cleaned or "file"


def _live_count(db: Session, user_id: uuid.UUID, purpose: MarketplaceFilePurpose) -> int:
    return (
        db.query(func.count(MarketplaceFile.id))
        .filter(
            MarketplaceFile.owner_user_id == user_id,
            MarketplaceFile.purpose == purpose,
            MarketplaceFile.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )


def _unattached_count(db: Session, user_id: uuid.UUID, purpose: MarketplaceFilePurpose) -> int:
    return (
        db.query(func.count(MarketplaceFile.id))
        .filter(
            MarketplaceFile.owner_user_id == user_id,
            MarketplaceFile.purpose == purpose,
            MarketplaceFile.deleted_at.is_(None),
            MarketplaceFile.booking_id.is_(None),
            MarketplaceFile.thread_id.is_(None),
        )
        .scalar()
        or 0
    )


def store_file(
    db: Session,
    *,
    owner_user_id: uuid.UUID,
    purpose: MarketplaceFilePurpose,
    filename: str | None,
    content_type: str | None,
    content: bytes,
) -> MarketplaceFile:
    allowed = ALLOWED_BY_PURPOSE.get(purpose)
    if allowed is None:
        raise _err("VALIDATION_ERROR", "That kind of upload isn't available here.")
    name = safe_filename(filename)
    declared = declared_type(content_type, name)
    if declared is None or declared not in allowed:
        hint = _HINT.get(purpose, "Upload a PDF, PNG or JPEG.")
        raise _err("UNSUPPORTED_FILE_TYPE", f"That file type isn't allowed here. {hint}")
    limit = _size_limit(purpose)
    if len(content) > limit:
        raise _err("FILE_TOO_LARGE", f"File must be {limit // (1024 * 1024)} MB or smaller.")
    sniffed = sniff_content_type(content, declared)
    if sniffed is None:
        raise _err(
            "FILE_CONTENT_MISMATCH",
            "That file's contents don't match its type, or the file is empty.",
        )
    cap = MAX_FILES_PER_PURPOSE.get(purpose.value)
    if (
        purpose != MarketplaceFilePurpose.avatar
        and cap
        and (_live_count(db, owner_user_id, purpose) >= cap)
    ):
        raise _err("FILE_LIMIT_REACHED", f"You can keep up to {cap} {purpose.value} files.", 409)
    if (
        purpose in SHARED_PURPOSES
        and _unattached_count(db, owner_user_id, purpose) >= MAX_UNATTACHED_FILES
    ):
        raise _err(
            "FILE_LIMIT_REACHED",
            f"You have {MAX_UNATTACHED_FILES} uploads waiting to be sent. Send or remove some "
            "first.",
            409,
        )
    verdict = get_scanner().scan(content, name)
    if verdict == ScanStatus.blocked:
        raise _err("FILE_BLOCKED", "That file didn't pass our safety scan.")
    key = f"marketplace/{owner_user_id}/{uuid.uuid4().hex}{EXT_BY_CONTENT_TYPE.get(sniffed, '')}"
    get_storage().save_private(key, content, sniffed)
    if purpose == MarketplaceFilePurpose.avatar:
        retire_files(db, owner_user_id, MarketplaceFilePurpose.avatar)
    row = MarketplaceFile(
        owner_user_id=owner_user_id,
        purpose=purpose,
        filename=name,
        mime=sniffed,
        size_bytes=len(content),
        storage_key=key,
        scan_status=verdict,
        scanned_at=None if verdict == ScanStatus.pending else datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def erase(db: Session, row: MarketplaceFile) -> None:
    """Remove the bytes and mark the row deleted. Anything that pointed at the file is cleared."""
    if row.deleted_at is not None:
        return
    get_storage().delete_private(row.storage_key)
    row.deleted_at = datetime.now(UTC)
    db.query(MarketplaceProviderPortfolio).filter(
        MarketplaceProviderPortfolio.file_id == row.id
    ).update({"file_id": None}, synchronize_session=False)
    db.query(MarketplaceProvider).filter(MarketplaceProvider.avatar_file_id == row.id).update(
        {"avatar_file_id": None}, synchronize_session=False
    )
    db.flush()


def retire_files(
    db: Session, owner_user_id: uuid.UUID, purpose: MarketplaceFilePurpose | None = None
) -> int:
    q = db.query(MarketplaceFile).filter(
        MarketplaceFile.owner_user_id == owner_user_id, MarketplaceFile.deleted_at.is_(None)
    )
    if purpose is not None:
        q = q.filter(MarketplaceFile.purpose == purpose)
    rows = q.all()
    for r in rows:
        erase(db, r)
    return len(rows)


def get_owned(db: Session, owner_user_id: uuid.UUID, file_id: uuid.UUID) -> MarketplaceFile:
    row = (
        db.query(MarketplaceFile)
        .filter(
            MarketplaceFile.id == file_id,
            MarketplaceFile.owner_user_id == owner_user_id,
            MarketplaceFile.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise NotFound()
    return row


def read_bytes(row: MarketplaceFile) -> bytes:
    data = get_storage().read_private(row.storage_key)
    if data is None:
        raise NotFound()
    return data


def serialize_file(row: MarketplaceFile) -> dict[str, object]:
    return {
        "id": str(row.id),
        "purpose": row.purpose.value,
        "filename": row.filename,
        "mime": row.mime,
        "size_bytes": row.size_bytes,
        "scan_status": row.scan_status.value,
        "created_at": row.created_at.isoformat(),
    }


# ------------------------------------------------------------------ sharing between the two sides


def _bad_attachment() -> AppError:
    return _err("INVALID_FILE", "One of those files can't be attached. Upload it again.")


def attach(
    db: Session,
    *,
    owner_user_id: uuid.UUID,
    file_ids: list[uuid.UUID],
    purposes: frozenset[MarketplaceFilePurpose],
    limit: int,
    booking_id: uuid.UUID | None = None,
    thread_id: uuid.UUID | None = None,
    dry_run: bool = False,
) -> list[MarketplaceFile]:
    """Link uploads to a booking or a thread, so the other side can read them. Every id must be a
    live file of `owner_user_id`, of an allowed purpose, scanned not-blocked and not yet attached
    anywhere. The rows are locked in id order so two requests can't both attach one file. One
    uniform error for every way an id can be wrong: nothing tells a caller whether somebody else's
    file exists. `dry_run` only checks (no lock, no change), so a request can fail early, before it
    has created anything."""
    if len(file_ids) != len(set(file_ids)) or len(file_ids) > limit:
        raise _err("INVALID_FILE", f"Attach up to {limit} different files.")
    if not file_ids:
        return []
    q = (
        db.query(MarketplaceFile)
        .filter(MarketplaceFile.id.in_(file_ids))
        .order_by(MarketplaceFile.id)
    )
    rows = (q if dry_run else q.with_for_update()).all()
    if len(rows) != len(file_ids):
        raise _bad_attachment()
    for r in rows:
        if (
            r.owner_user_id != owner_user_id
            or r.deleted_at is not None
            or r.purpose not in purposes
            or r.booking_id is not None
            or r.thread_id is not None
            or r.scan_status == ScanStatus.blocked
        ):
            raise _bad_attachment()
    if dry_run:
        return rows
    for r in rows:
        r.booking_id, r.thread_id = booking_id, thread_id
    db.flush()
    by_id = {r.id: r for r in rows}
    return [by_id[i] for i in file_ids]


def _scope(db: Session, row: MarketplaceFile) -> tuple[uuid.UUID, uuid.UUID, bool] | None:
    """(startup_id, provider_id, provider_may_see_it) for an attached file, else None. A booking
    still waiting for payment is invisible to the expert, and so are its brief files."""
    if row.booking_id is not None:
        b = db.get(MarketplaceBooking, row.booking_id)
        if b is None:
            return None
        return b.startup_id, b.provider_id, identity.was_paid(db, b)
    if row.thread_id is not None:
        t = db.get(MarketplaceThread, row.thread_id)
        return (t.startup_id, t.provider_id, True) if t is not None else None
    return None


def _live(db: Session, file_id: uuid.UUID) -> MarketplaceFile:
    row = (
        db.query(MarketplaceFile)
        .filter(MarketplaceFile.id == file_id, MarketplaceFile.deleted_at.is_(None))
        .first()
    )
    if row is None:
        raise NotFound()
    return row


def readable_by_founder_side(
    db: Session, user_id: uuid.UUID, startup_id: uuid.UUID, file_id: uuid.UUID
) -> MarketplaceFile:
    """The owner, or any founder-side member of the workspace the file was sent in. Anyone else,
    including another workspace's founder, gets the same 404 as a missing file."""
    row = _live(db, file_id)
    if row.owner_user_id == user_id:
        return row
    scope = _scope(db, row) if row.purpose in SHARED_PURPOSES else None
    if scope is None or scope[0] != startup_id:
        raise NotFound()
    return row


def readable_by_provider(
    db: Session,
    user_id: uuid.UUID,
    provider_id: uuid.UUID,
    file_id: uuid.UUID,
    *,
    approved: bool,
) -> MarketplaceFile:
    """The owner (always), or, for an APPROVED expert only, the expert of the booking / thread the
    file was sent in. A suspended expert keeps their own files but reads no client's."""
    row = _live(db, file_id)
    if row.owner_user_id == user_id:
        return row
    if not approved:
        raise NotFound()
    scope = _scope(db, row) if row.purpose in SHARED_PURPOSES else None
    if scope is None or scope[1] != provider_id or not scope[2]:
        raise NotFound()
    return row


def serialize_many(db: Session, file_ids: list[str], *, base: str) -> list[dict[str, object]]:
    """The files a message / delivery / brief carries, with the path that downloads each (`base`
    differs per side: `/api/v1/marketplace/files` or `/api/v1/provider/files`)."""
    if not file_ids:
        return []
    ids = [uuid.UUID(str(i)) for i in file_ids]
    rows = {r.id: r for r in db.query(MarketplaceFile).filter(MarketplaceFile.id.in_(ids)).all()}
    out: list[dict[str, object]] = []
    for i in ids:
        r = rows.get(i)
        if r is None or r.deleted_at is not None:
            continue
        out.append({**serialize_file(r), "url": f"{base}/{r.id}/content"})
    return out


def download_headers(filename: str) -> dict[str, str]:
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "") or "file"
    return {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}",
    }

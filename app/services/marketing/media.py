"""Marketing media: images attached to calendar posts.

Deliberately separate from Documents (`document_files`). Documents are readable by every role,
so an unpublished campaign asset stored there would be visible to mentors, investors and
consultants. These rows are reachable only through `MARKETING_ROLES` routes, and the storage key
carries a random name so a URL is not guessable.
"""

import uuid
from pathlib import Path

from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import NotFound
from app.db.models.marketing import MarketingMedia
from app.platform.storage import get_storage
from app.schemas.marketing import MediaResponse
from app.services.marketing.errors import _validation

MAX_MEDIA_BYTES = 5 * 1024 * 1024
MAX_MEDIA_PER_WORKSPACE = 200

# content-type -> (extension, magic-number check). The declared type must match the bytes, so a
# file cannot be passed off as an image.
_TYPES = {
    "image/png": (".png", lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/jpeg": (".jpg", lambda b: b.startswith(b"\xff\xd8\xff")),
    "image/gif": (".gif", lambda b: b[:6] in (b"GIF87a", b"GIF89a")),
    "image/webp": (".webp", lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP"),
}
ALLOWED_CONTENT_TYPES = frozenset(_TYPES)


def upload_media(
    db: Session,
    *,
    startup_id: uuid.UUID,
    uploaded_by: uuid.UUID,
    filename: str,
    content_type: str,
    content: bytes,
) -> MarketingMedia:
    spec = _TYPES.get(content_type)
    if spec is None:
        raise _validation("file", "Upload a PNG, JPEG, GIF or WebP image.")
    ext, matches = spec
    if not content or not matches(content):
        raise _validation("file", "That file is not a valid image of the type it claims to be.")
    if len(content) > MAX_MEDIA_BYTES:
        raise _validation("file", "Image must be 5 MB or smaller.")
    if db.query(MarketingMedia).filter_by(startup_id=startup_id).count() >= MAX_MEDIA_PER_WORKSPACE:
        raise _validation("file", "Media limit reached. Delete an image you no longer use.")
    key = f"marketing/{startup_id}/{uuid.uuid4().hex}{ext}"
    url = get_storage().save(key, content, content_type)
    row = MarketingMedia(
        startup_id=startup_id,
        uploaded_by=uploaded_by,
        filename=(filename or "image")[:255],
        content_type=content_type,
        size_bytes=len(content),
        storage_key=key,
        url=url,
    )
    db.add(row)
    db.flush()
    return row


def list_media(db: Session, *, startup_id: uuid.UUID) -> list[MarketingMedia]:
    return (
        db.query(MarketingMedia)
        .filter_by(startup_id=startup_id)
        .order_by(MarketingMedia.created_at.desc())
        .all()
    )


def delete_media(db: Session, *, startup_id: uuid.UUID, media_id: uuid.UUID) -> None:
    row = db.query(MarketingMedia).filter_by(id=media_id, startup_id=startup_id).one_or_none()
    if row is None:
        raise NotFound()
    get_storage().delete(row.storage_key)
    db.delete(row)  # posts that used it fall back to no media (FK SET NULL)
    db.flush()


def serialize_media(m: MarketingMedia) -> MediaResponse:
    return MediaResponse.model_validate(m, from_attributes=True)


def media_file_response(db: Session, *, startup_id: uuid.UUID, media_id: uuid.UUID) -> Response:
    """Serve one image of this workspace. Local storage streams the file (the path is checked to
    sit under the storage root); a hosted backend (Cloudinary) already has a public URL with a
    random key, so the caller is redirected to it."""
    row = db.query(MarketingMedia).filter_by(id=media_id, startup_id=startup_id).one_or_none()
    if row is None:
        raise NotFound()
    if row.url.startswith("https://"):
        return RedirectResponse(row.url, status_code=302)
    root = Path(settings.LOCAL_STORAGE_DIR).resolve()
    path = (root / row.storage_key).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise NotFound()
    return FileResponse(
        path,
        media_type=row.content_type,
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )

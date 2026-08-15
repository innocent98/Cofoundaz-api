import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError
from app.db.models.user import User
from app.db.session import get_db
from app.platform.storage import get_storage
from app.services.onboarding.workspace import resolve_or_create_workspace

router = APIRouter()
_ALLOWED = {"image/png": "png", "image/jpeg": "jpg", "image/svg+xml": "svg", "image/webp": "webp"}
_MAX_BYTES = 2 * 1024 * 1024


@router.post("/logo")
async def upload_logo(
    file: UploadFile = File(...),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    if file.content_type not in _ALLOWED:
        raise AppError("VALIDATION_ERROR", "Logo must be a PNG, JPG, SVG, or WebP image.", 422)
    content = await file.read()
    if len(content) > _MAX_BYTES:
        raise AppError("VALIDATION_ERROR", "Logo must be 2 MB or smaller.", 422)
    startup = resolve_or_create_workspace(db, user)
    ext = _ALLOWED[file.content_type]
    key = f"logos/{startup.id}/{uuid.uuid4().hex}.{ext}"
    startup.logo_url = get_storage().save(key, content, file.content_type)
    db.commit()
    return success_response({"logo_url": startup.logo_url})

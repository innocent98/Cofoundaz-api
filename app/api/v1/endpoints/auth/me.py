import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_core import PydanticCustomError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.platform.audit import write_audit
from app.platform.storage import get_storage
from app.services.onboarding.workspace import is_untouched_draft

router = APIRouter()

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class MePatch(BaseModel):
    """What a member may change about themselves. Unknown fields are rejected (422) rather than
    ignored, so a typo can't look like a save. Send only the fields to change: `country` and
    `phone` accept `null` to clear them, `full_name` and `role_title` can't be blank."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = None
    role_title: str | None = None
    country: str | None = None
    phone: str | None = None

    @field_validator("full_name")
    @classmethod
    def _clean_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        # PydanticCustomError, not ValueError: pydantic prefixes ValueError text with "Value error, ".
        if _CONTROL.search(v):
            raise PydanticCustomError("name_chars", "Use letters, spaces and punctuation only.")
        v = " ".join(v.split())
        if not v:
            raise PydanticCustomError("name_required", "Tell us your name.")
        if len(v) > 120:
            raise PydanticCustomError("name_too_long", "Keep it to 120 characters.")
        return v

    @field_validator("role_title")
    @classmethod
    def _clean_role(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if _CONTROL.search(v):
            raise PydanticCustomError("role_chars", "Use letters, spaces and punctuation only.")
        v = " ".join(v.split())
        if not v:
            raise PydanticCustomError("role_required", "Tell us your role.")
        if len(v) > 80:
            raise PydanticCustomError("role_too_long", "Keep it to 80 characters.")
        return v

    @field_validator("country")
    @classmethod
    def _clean_country(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", v):
            raise PydanticCustomError("country_code", "Use a two-letter country code, like NG.")
        return v

    @field_validator("phone")
    @classmethod
    def _clean_phone(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not re.fullmatch(r"\+?[0-9][0-9 ()-]{5,30}", v):
            raise PydanticCustomError("phone_format", "Enter a valid phone number.")
        return v


@router.get("/me")
def me(
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(_me_payload(db, user))


@router.patch("/me")
def update_me(
    body: MePatch,
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    sent = body.model_fields_set
    if not sent:
        raise AppError("VALIDATION_ERROR", "Nothing to update.", 422)
    if "full_name" in sent and body.full_name is None:
        raise AppError("VALIDATION_ERROR", "Tell us your name.", 422)
    if "role_title" in sent and body.role_title is None:
        raise AppError("VALIDATION_ERROR", "Tell us your role.", 422)
    for field in sent:
        setattr(user.profile, field, getattr(body, field))
    write_audit(db, "auth.profile.updated", actor_user_id=user.id)
    db.commit()
    return success_response(_me_payload(db, user))


def _me_payload(db: Session, user: User) -> dict[str, Any]:
    rows = (
        db.query(Membership, Startup)
        .join(Startup, Startup.id == Membership.startup_id)
        .filter(Membership.user_id == user.id, Membership.status == MembershipStatus.active)
        # Deterministic order: earliest-created membership first, with `id` as a stable
        # tiebreaker for rows created in the same instant (e.g. same transaction, where
        # Postgres `now()` is transaction-scoped and can tie). Without this, `.all()` has
        # no defined order and `active_workspace_id` below could vary between calls.
        .order_by(Membership.created_at, Membership.id)
        .all()
    )
    # Hide an untouched onboarding draft when the user belongs to another workspace: it was
    # created for an invited member by mistake (before the 2026-10-19 fix) or abandoned.
    pairs = [(m, s) for m, s in rows]
    if len(pairs) > 1:
        pairs = [(m, s) for m, s in pairs if not is_untouched_draft(s)] or pairs
    memberships = [
        {"startup_id": str(s.id), "name": s.name, "role": m.role.value} for m, s in pairs
    ]
    profile = user.profile
    return {
        "user": {"id": str(user.id), "email": user.email, "status": user.status.value},
        "profile": (
            None
            if profile is None
            else {
                "full_name": profile.full_name,
                "role_title": profile.role_title,
                "country": profile.country,
                "avatar_url": profile.avatar_url,
                "phone": profile.phone,
            }
        ),
        "memberships": memberships,
        "active_workspace_id": memberships[0]["startup_id"] if memberships else None,
    }


_AVATAR_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_AVATAR_MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
}
_AVATAR_MAX = 2 * 1024 * 1024


@router.post("/me/avatar")
async def upload_avatar(
    file: UploadFile = File(...),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    if file.content_type not in _AVATAR_TYPES:
        raise AppError("VALIDATION_ERROR", "Use a PNG, JPG or WebP image.", 422)
    content = b""
    while chunk := await file.read(64 * 1024):
        content += chunk
        if len(content) > _AVATAR_MAX:
            raise AppError("VALIDATION_ERROR", "Photo must be 2 MB or smaller.", 422)
    # Trust the bytes, not the declared type: a renamed HTML/SVG file must not be served as an image.
    if not content.startswith(_AVATAR_MAGIC[file.content_type]):
        raise AppError("VALIDATION_ERROR", "That file isn't a valid image.", 422)
    key = f"avatars/{user.id}/{uuid.uuid4().hex}.{_AVATAR_TYPES[file.content_type]}"
    user.profile.avatar_url = get_storage().save(key, content, file.content_type)
    write_audit(db, "auth.avatar.updated", actor_user_id=user.id)
    db.commit()
    return success_response(_me_payload(db, user))


@router.delete("/me/avatar")
def remove_avatar(
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    user.profile.avatar_url = None
    write_audit(db, "auth.avatar.removed", actor_user_id=user.id)
    db.commit()
    return success_response(_me_payload(db, user))

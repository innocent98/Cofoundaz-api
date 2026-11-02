"""Settings → Workspace: the workspace's own details, outside the onboarding wizard."""

import re
import uuid
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError
from sqlalchemy.orm import Session

from app.core.envelope import success_response
from app.core.errors import AppError, NotFound
from app.db.models.enums import BusinessModel, MembershipRole, StartupStage
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.platform.audit import write_audit
from app.platform.storage import get_storage

router = APIRouter()

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LOGO_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_LOGO_MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
}
_LOGO_MAX = 2 * 1024 * 1024


class WorkspacePatch(BaseModel):
    """Founder-only edits. Unknown fields are rejected; `website`, `description`, `industry` and
    `country` accept `null` to clear; `name` can't be blank."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None)
    description: str | None = None
    website: str | None = None
    industry: str | None = None
    business_model: BusinessModel | None = None
    stage: StartupStage | None = None
    country: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if _CONTROL.search(v):
            raise PydanticCustomError("name_chars", "Use letters, spaces and punctuation only.")
        v = " ".join(v.split())
        if not v:
            raise PydanticCustomError("name_required", "Name your startup.")
        if len(v) > 120:
            raise PydanticCustomError("name_too_long", "Keep it to 120 characters.")
        return v

    @field_validator("description")
    @classmethod
    def _description(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 1000:
            raise PydanticCustomError("description_too_long", "Keep it to 1,000 characters.")
        return v

    @field_validator("website")
    @classmethod
    def _website(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or len(v) > 300:
            raise PydanticCustomError("website_url", "Enter a web address starting with https://.")
        return v

    @field_validator("industry")
    @classmethod
    def _industry(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 80:
            raise PydanticCustomError("industry_too_long", "Keep it to 80 characters.")
        return v

    @field_validator("country")
    @classmethod
    def _country(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", v):
            raise PydanticCustomError("country_code", "Use a two-letter country code, like NG.")
        return v


def _serialize(s: Startup, role: MembershipRole) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "name": s.name,
        "description": s.description,
        "website": s.website,
        "logo_url": s.logo_url,
        "industry": s.industry,
        "business_model": s.business_model.value if s.business_model else None,
        "stage": s.stage.value if s.stage else None,
        "country": s.country,
        "can_edit": role == MembershipRole.founder,
    }


def _startup(db: Session, membership: Membership) -> Startup:
    s = (
        db.query(Startup)
        .filter(Startup.id == membership.startup_id, Startup.deleted_at.is_(None))
        .first()
    )
    if s is None:
        raise NotFound()
    return s


@router.get("")
def get_workspace(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(_serialize(_startup(db, membership), membership.role))


@router.patch("")
def patch_workspace(
    body: WorkspacePatch,
    membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    sent = body.model_fields_set
    if not sent:
        raise AppError("VALIDATION_ERROR", "Nothing to update.", 422)
    if "name" in sent and body.name is None:
        raise AppError("VALIDATION_ERROR", "Name your startup.", 422)
    s = _startup(db, membership)
    for field in sent:
        setattr(s, field, getattr(body, field))
    write_audit(
        db,
        "workspace.updated",
        actor_user_id=membership.user_id,
        startup_id=s.id,
        entity_type="startup",
        entity_id=s.id,
    )
    db.commit()
    return success_response(_serialize(s, membership.role))


@router.post("/logo")
async def upload_workspace_logo(
    file: UploadFile = File(...),  # noqa: B008
    membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    if file.content_type not in _LOGO_TYPES:
        raise AppError("VALIDATION_ERROR", "Logo must be a PNG, JPG or WebP image.", 422)
    content = b""
    while chunk := await file.read(64 * 1024):
        content += chunk
        if len(content) > _LOGO_MAX:
            raise AppError("VALIDATION_ERROR", "Logo must be 2 MB or smaller.", 422)
    if not content.startswith(_LOGO_MAGIC[file.content_type]):
        raise AppError("VALIDATION_ERROR", "That file isn't a valid image.", 422)
    s = _startup(db, membership)
    key = f"logos/{s.id}/{uuid.uuid4().hex}.{_LOGO_TYPES[file.content_type]}"
    s.logo_url = get_storage().save(key, content, file.content_type)
    write_audit(
        db,
        "workspace.logo.updated",
        actor_user_id=membership.user_id,
        startup_id=s.id,
        entity_type="startup",
        entity_id=s.id,
    )
    db.commit()
    return success_response(_serialize(s, membership.role))

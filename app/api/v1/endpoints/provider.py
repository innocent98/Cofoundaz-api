"""The expert portal API (`/provider/*`, design §3.8, D-MP-2).

An expert is a normal account plus a `marketplace_providers` row. These routes are for that row's
owner only (`require_provider`); the three onboarding entry points (`/status`, `/invites/*`,
`/apply`) take any signed-in, verified account because the caller isn't an expert yet."""

import uuid
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.api.provider_deps import ProviderContext, require_provider
from app.core.envelope import success_response
from app.core.errors import AppError, FileNotClean
from app.core.rate_limit import limiter
from app.db.models.enums import MarketplaceFilePurpose, ProviderStatus, ScanStatus
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.marketplace import (
    AcceptInviteRequest,
    ApplyRequest,
    PortfolioCreate,
    PortfolioPatch,
    ProfilePatch,
)
from app.services.marketplace import emails, files, providers

router = APIRouter()

_Db = Annotated[Session, Depends(get_db)]
_User = Annotated[User, Depends(get_verified_user)]
_Provider = Annotated[ProviderContext, Depends(require_provider())]
_CHUNK = 1024 * 1024


def _client(request: Request) -> tuple[str | None, str | None]:
    return (request.client.host if request.client else None), request.headers.get("user-agent")


@router.get("/status")
def provider_status(user: _User, db: _Db) -> dict[str, Any]:
    """Anyone signed in: is this account an expert, and can it apply? Never 403, so the app can
    decide whether to show the portal link."""
    row = providers.provider_for_user(db, user.id)
    return success_response(
        {
            "is_provider": row is not None,
            "status": row.status.value if row else None,
            "applications_open": providers.applications_open(),
        }
    )


@router.get("/invites/{token}")
# One bucket per caller across every token, so tokens can't be guessed one URL at a time.
@limiter.shared_limit("30/minute", scope="provider-invite")
def preview_invite(request: Request, token: str, db: _Db) -> dict[str, Any]:
    """Public: what an invitation is for. 404 for an unknown, expired or already-used token."""
    return success_response(providers.preview_invite(db, token))


@router.post("/invites/accept")
def accept_invite(
    payload: AcceptInviteRequest, request: Request, user: _User, db: _Db
) -> dict[str, Any]:
    ip, ua = _client(request)
    row = providers.accept_invite(db, user, payload.token, ip=ip, user_agent=ua)
    db.commit()
    return success_response(providers.provider_view(db, row))


@router.post("/apply", status_code=status.HTTP_201_CREATED)
def apply(payload: ApplyRequest, request: Request, user: _User, db: _Db) -> dict[str, Any]:
    ip, ua = _client(request)
    row = providers.apply_as_provider(db, user, category=payload.category, ip=ip, user_agent=ua)
    db.commit()
    return success_response(providers.provider_view(db, row))


@router.get("/me")
def me(ctx: _Provider, db: _Db) -> dict[str, Any]:
    return success_response(providers.provider_view(db, ctx.provider))


@router.patch("/me")
def update_me(payload: ProfilePatch, request: Request, ctx: _Provider, db: _Db) -> dict[str, Any]:
    sent = payload.model_fields_set
    if not sent:
        raise AppError("VALIDATION_ERROR", "Nothing to update.", 422)
    changes: dict[str, Any] = {}
    for field in sent:
        value = getattr(payload, field)
        if value is None and field in ("categories", "languages"):
            value = []
        if value is None and field == "timezone":
            raise AppError("VALIDATION_ERROR", "Choose a timezone.", 422)
        changes[field] = value
    ip, ua = _client(request)
    row = providers.update_profile(db, ctx.user, ctx.provider.id, changes, ip=ip, user_agent=ua)
    db.commit()
    return success_response(providers.provider_view(db, row))


@router.post("/submit")
def submit(request: Request, ctx: _Provider, db: _Db) -> dict[str, Any]:
    ip, ua = _client(request)
    row, outbox = providers.submit(db, ctx.user, ctx.provider.id, ip=ip, user_agent=ua)
    db.commit()
    emails.deliver(outbox)
    return success_response(providers.provider_view(db, row))


# ------------------------------------------------------------------ files


@router.post("/files", status_code=status.HTTP_201_CREATED)
async def upload(
    ctx: _Provider,
    db: _Db,
    purpose: Annotated[MarketplaceFilePurpose, Form()],
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    if purpose == MarketplaceFilePurpose.brief:
        raise AppError("VALIDATION_ERROR", "Only a client can upload a brief.", 422)
    if purpose in files.PROVIDER_UPLOADS:
        # Work for a client and replies are an approved expert's; a suspended or pending
        # account can't send anything to a workspace.
        if ctx.provider.status != ProviderStatus.approved:
            raise AppError("PROVIDER_NOT_APPROVED", "Your expert profile isn't approved yet.", 403)
    elif ctx.provider.status not in providers.UPLOADABLE:
        providers.check_editable(ctx.provider)
    limit = files.size_limit(purpose)
    content = b""
    while chunk := await file.read(_CHUNK):
        content += chunk
        if len(content) > limit:
            raise AppError(
                "FILE_TOO_LARGE", f"File must be {limit // (1024 * 1024)} MB or smaller.", 422
            )
    row = files.store_file(
        db,
        owner_user_id=ctx.user.id,
        purpose=purpose,
        filename=file.filename,
        content_type=file.content_type,
        content=content,
    )
    db.commit()
    return success_response(files.serialize_file(row))


@router.get("/files/{file_id}/content")
def download(file_id: uuid.UUID, ctx: _Provider, db: _Db) -> Response:
    row = files.readable_by_provider(
        db,
        ctx.user.id,
        ctx.provider.id,
        file_id,
        approved=ctx.provider.status == ProviderStatus.approved,
    )
    if row.scan_status != ScanStatus.clean:
        raise FileNotClean()
    return Response(
        content=files.read_bytes(row),
        media_type=row.mime,
        headers=file_headers(row.filename),
    )


@router.delete("/files/{file_id}")
def delete_file(file_id: uuid.UUID, ctx: _Provider, db: _Db) -> dict[str, Any]:
    providers.delete_own_file(db, ctx.user, ctx.provider, file_id)
    db.commit()
    return success_response({"deleted": True})


def file_headers(filename: str) -> dict[str, str]:
    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "") or "file"
    return {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}",
    }


# ------------------------------------------------------------------ portfolio


@router.post("/portfolio", status_code=status.HTTP_201_CREATED)
def add_portfolio(payload: PortfolioCreate, ctx: _Provider, db: _Db) -> dict[str, Any]:
    item = providers.add_portfolio(db, ctx.user, ctx.provider.id, payload.model_dump())
    db.commit()
    return success_response(providers.serialize_portfolio(item))


@router.patch("/portfolio/{item_id}")
def update_portfolio(
    item_id: uuid.UUID, payload: PortfolioPatch, ctx: _Provider, db: _Db
) -> dict[str, Any]:
    changes = {f: getattr(payload, f) for f in payload.model_fields_set}
    if not changes:
        raise AppError("VALIDATION_ERROR", "Nothing to update.", 422)
    if changes.get("title", "x") is None or changes.get("position", 0) is None:
        raise AppError("VALIDATION_ERROR", "That field can't be empty.", 422)
    item = providers.update_portfolio(db, ctx.user, ctx.provider.id, item_id, changes)
    db.commit()
    return success_response(providers.serialize_portfolio(item))


@router.delete("/portfolio/{item_id}")
def delete_portfolio(item_id: uuid.UUID, ctx: _Provider, db: _Db) -> dict[str, Any]:
    providers.delete_portfolio(db, ctx.provider.id, item_id)
    db.commit()
    return success_response({"deleted": True})

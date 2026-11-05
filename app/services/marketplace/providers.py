"""Marketplace providers: invite / apply, profile, portfolio, submit (the expert's side).

Status machine (design §2): `invited | applied` (drafts) -> `in_review` -> `approved`, with
`changes_requested` (back to a draft), `rejected` (terminal) and `suspended` (reversible). Every
mutation row-locks the provider first so two requests can't both pass a status check. Staff-side
transitions live in `vetting.py`."""

import hashlib
import secrets
import uuid
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.staff_deps import StaffContext
from app.core.config import settings
from app.core.errors import AppError, InviteEmailMismatch, NotFound, TokenInvalid
from app.db.models.audit import AuditLog
from app.db.models.enums import MarketplaceFilePurpose, ProviderStatus, ScanStatus
from app.db.models.marketplace import (
    MarketplaceFile,
    MarketplaceProvider,
    MarketplaceProviderPortfolio,
)
from app.db.models.user import User
from app.platform.audit import write_audit
from app.platform.email import EmailMessage
from app.services.marketplace import emails
from app.services.marketplace.constants import (
    CATEGORIES,
    CHECK_LABELS,
    DAILY_INVITE_CAP,
    INVITE_TTL,
    MAX_PORTFOLIO_ITEMS,
    REGULATED,
    TERMS_VERSION,
)
from app.services.marketplace.files import erase, get_owned, serialize_file
from app.services.marketplace.prescreen import SCANNED_FIELDS, prescreen

DRAFT = frozenset(
    {ProviderStatus.invited, ProviderStatus.applied, ProviderStatus.changes_requested}
)
PROFILE_EDITABLE = DRAFT | {ProviderStatus.approved}
UPLOADABLE = PROFILE_EDITABLE
CREDENTIALS_DELETABLE = DRAFT


def err(code: str, message: str, status: int = 409) -> AppError:
    return AppError(code=code, message=message, http_status=status)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def lock_provider(db: Session, provider_id: uuid.UUID) -> MarketplaceProvider:
    row = (
        db.query(MarketplaceProvider)
        .filter(MarketplaceProvider.id == provider_id)
        .with_for_update()
        .first()
    )
    if row is None:
        raise err("PROVIDER_NOT_FOUND", "No such expert.", 404)
    return row


def provider_for_user(db: Session, user_id: uuid.UUID) -> MarketplaceProvider | None:
    return db.query(MarketplaceProvider).filter(MarketplaceProvider.user_id == user_id).first()


def applications_open() -> bool:
    return bool(settings.MARKETPLACE_OPEN_APPLICATIONS)


# ------------------------------------------------------------------ serialisation


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value else None


def _ticked_labels(row: MarketplaceProvider) -> list[str]:
    checks = (row.vetting_json or {}).get("checks", {})
    labels = []
    for key in ("id", "licence", "references", "portfolio"):
        entry = checks.get(key)
        if not entry or not entry.get("done"):
            continue
        if key == "references":
            n = int(entry.get("count") or 0)
            labels.append(f"{n} client reference{'s' if n != 1 else ''} checked")
        else:
            labels.append(CHECK_LABELS[key])
    return labels


def badge(row: MarketplaceProvider) -> dict[str, Any]:
    """The 'Vetted' tooltip, generated from the checks actually recorded (design D-MP-6): we never
    claim more than staff ticked. Nothing is claimed until the expert is approved, so a half-done
    checklist never reaches anyone as a badge."""
    vetted = row.status == ProviderStatus.approved and row.vetted_at is not None
    return {"vetted": vetted, "checks": _ticked_labels(row) if vetted else []}


def badge_preview(row: MarketplaceProvider) -> list[str]:
    """What the tooltip would say if approved now. Staff-only."""
    return _ticked_labels(row)


def portfolio_rows(db: Session, provider_id: uuid.UUID) -> list[MarketplaceProviderPortfolio]:
    return (
        db.query(MarketplaceProviderPortfolio)
        .filter(MarketplaceProviderPortfolio.provider_id == provider_id)
        .order_by(MarketplaceProviderPortfolio.position, MarketplaceProviderPortfolio.created_at)
        .all()
    )


def serialize_portfolio(item: MarketplaceProviderPortfolio) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "title": item.title,
        "description": item.description,
        "url": item.url,
        "file_id": str(item.file_id) if item.file_id else None,
        "position": item.position,
    }


def live_files(db: Session, user_id: uuid.UUID | None) -> list[MarketplaceFile]:
    if user_id is None:
        return []
    return (
        db.query(MarketplaceFile)
        .filter(MarketplaceFile.owner_user_id == user_id, MarketplaceFile.deleted_at.is_(None))
        .order_by(MarketplaceFile.created_at)
        .all()
    )


def submit_blockers(db: Session, row: MarketplaceProvider) -> list[dict[str, str]]:
    """What is still missing before the expert can submit, as `{field, message}` pairs."""
    missing: list[dict[str, str]] = []

    def need(field: str, message: str) -> None:
        missing.append({"field": field, "message": message})

    if row.terms_accepted_at is None:
        need("terms", "Accept the expert terms.")
    if not row.display_name:
        need("display_name", "Add your name.")
    if not row.headline:
        need("headline", "Add a one-line headline.")
    if not row.bio or len(row.bio.strip()) < 50:
        need("bio", "Write a bio of at least 50 characters.")
    if not row.category:
        need("category", "Pick your main category.")
    if not row.languages:
        need("languages", "Add at least one language.")
    clean_credentials = [
        f
        for f in live_files(db, row.user_id)
        if f.purpose == MarketplaceFilePurpose.credential and f.scan_status == ScanStatus.clean
    ]
    if not clean_credentials:
        need("credentials", "Upload at least one credential document (ID or licence).")
    if row.category and row.category not in REGULATED and not portfolio_rows(db, row.id):
        need("portfolio", "Add at least one portfolio item.")
    return missing


def provider_view(db: Session, row: MarketplaceProvider) -> dict[str, Any]:
    """The expert's own view of their record. No staff notes, ticks' authors or pre-screen."""
    return {
        "id": str(row.id),
        "email": row.email,
        "status": row.status.value,
        "source": row.source,
        "display_name": row.display_name,
        "headline": row.headline,
        "bio": row.bio,
        "category": row.category,
        "categories": list(row.categories),
        "languages": list(row.languages),
        "timezone": row.timezone,
        "location": row.location,
        "avatar_file_id": str(row.avatar_file_id) if row.avatar_file_id else None,
        "terms_accepted_at": _iso(row.terms_accepted_at),
        "submitted_at": _iso(row.submitted_at),
        "review_message": (
            row.review_message
            if row.status
            in (ProviderStatus.changes_requested, ProviderStatus.rejected, ProviderStatus.suspended)
            else None
        ),
        "can_edit_profile": row.status in PROFILE_EDITABLE,
        "can_submit": row.status in DRAFT,
        "submit_blockers": submit_blockers(db, row) if row.status in DRAFT else [],
        "badge": badge(row),
        "portfolio": [serialize_portfolio(p) for p in portfolio_rows(db, row.id)],
        "files": [serialize_file(f) for f in live_files(db, row.user_id)],
    }


# ------------------------------------------------------------------ invite / apply


def _new_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def _prescreen_fields(row: MarketplaceProvider) -> dict[str, str | None]:
    return {name: getattr(row, name) for name in SCANNED_FIELDS}


def refresh_prescreen(row: MarketplaceProvider) -> dict[str, Any]:
    result = prescreen(_prescreen_fields(row))
    row.vetting_json = {**(row.vetting_json or {}), "prescreen": result}
    return result


def invite_provider(
    db: Session,
    ctx: StaffContext,
    *,
    email: str,
    category: str | None,
    display_name: str | None,
    ip: str | None,
    user_agent: str | None,
) -> tuple[MarketplaceProvider, list[EmailMessage]]:
    if email.lower() == ctx.user.email.lower():
        raise err("CANNOT_ACT_ON_SELF", "You can't invite your own account.", 422)
    since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    # Count audit rows, not provider rows: a revoked invite deletes its row, and revoke + re-invite
    # must not dodge the daily cap.
    sent_today = (
        db.query(func.count(AuditLog.id))
        .filter(
            AuditLog.action == "marketplace.provider.invited",
            AuditLog.actor_user_id == ctx.user.id,
            AuditLog.created_at >= since,
        )
        .scalar()
        or 0
    )
    if sent_today >= DAILY_INVITE_CAP:
        raise err("INVITE_LIMIT_REACHED", "That's a lot of invitations for one day.", 429)
    existing = db.query(MarketplaceProvider).filter(MarketplaceProvider.email == email).first()
    if existing is not None:
        if existing.user_id is None and existing.status == ProviderStatus.invited:
            raise err(
                "ALREADY_INVITED", "That person already has a pending invitation. Re-send it."
            )
        raise err("ALREADY_PROVIDER", "That person is already an expert or has applied.")
    if category is not None and category not in CATEGORIES:
        raise err("VALIDATION_ERROR", "Unknown category.", 422)
    raw, token_hash = _new_token()
    row = MarketplaceProvider(
        email=email,
        status=ProviderStatus.invited,
        source="invite",
        category=category,
        categories=[],
        languages=[],
        display_name=display_name,
        invite_token_hash=token_hash,
        invite_expires_at=datetime.now(UTC) + INVITE_TTL,
        invited_by=ctx.user.id,
        vetting_json={},
    )
    db.add(row)
    db.flush()
    write_audit(
        db,
        "marketplace.provider.invited",
        actor_user_id=ctx.user.id,
        entity_type="marketplace_provider",
        entity_id=row.id,
        ip=ip,
        user_agent=user_agent,
        details={"email": email, "category": category},
    )
    return row, [emails.provider_invite_email(email, raw, name=display_name, category=category)]


def resend_invite(
    db: Session,
    ctx: StaffContext,
    provider_id: uuid.UUID,
    *,
    ip: str | None,
    user_agent: str | None,
) -> tuple[MarketplaceProvider, list[EmailMessage]]:
    row = lock_provider(db, provider_id)
    if row.user_id is not None or row.status != ProviderStatus.invited:
        raise err("INVITE_NOT_PENDING", "That invitation has already been accepted.")
    raw, row.invite_token_hash = _new_token()  # the old link stops working
    row.invite_expires_at = datetime.now(UTC) + INVITE_TTL
    write_audit(
        db,
        "marketplace.provider.invite_resent",
        actor_user_id=ctx.user.id,
        entity_type="marketplace_provider",
        entity_id=row.id,
        ip=ip,
        user_agent=user_agent,
        details={"email": row.email},
    )
    msg = emails.provider_invite_email(row.email, raw, name=row.display_name, category=row.category)
    return row, [msg]


def revoke_invite(
    db: Session,
    ctx: StaffContext,
    provider_id: uuid.UUID,
    *,
    ip: str | None,
    user_agent: str | None,
) -> None:
    row = lock_provider(db, provider_id)
    if row.user_id is not None or row.status != ProviderStatus.invited:
        raise err("INVITE_NOT_PENDING", "That invitation has already been accepted.")
    write_audit(
        db,
        "marketplace.provider.invite_revoked",
        actor_user_id=ctx.user.id,
        entity_type="marketplace_provider",
        entity_id=row.id,
        ip=ip,
        user_agent=user_agent,
        details={"email": row.email},
    )
    db.delete(row)
    db.flush()


def _invite_by_token(db: Session, raw: str, *, lock: bool) -> MarketplaceProvider | None:
    q = db.query(MarketplaceProvider).filter(
        MarketplaceProvider.invite_token_hash == hash_token(raw)
    )
    row = (q.with_for_update() if lock else q).first()
    if (
        row is None
        or row.invite_expires_at is None
        or row.invite_expires_at < datetime.now(UTC)
        or row.user_id is not None
    ):
        return None
    return row


def preview_invite(db: Session, raw: str) -> dict[str, Any]:
    row = _invite_by_token(db, raw, lock=False)
    if row is None:
        # One 404 for unknown, expired and used tokens: don't say which.
        raise NotFound()
    return {
        "email": row.email,
        "display_name": row.display_name,
        "category": row.category,
        "expires_at": _iso(row.invite_expires_at),
        "terms_version": TERMS_VERSION,
    }


def accept_invite(
    db: Session, user: User, raw: str, *, ip: str | None, user_agent: str | None
) -> MarketplaceProvider:
    row = _invite_by_token(db, raw, lock=True)
    if row is None:
        raise TokenInvalid()
    if user.email.lower() != row.email.lower():
        # Checked before the token is spent: the wrong account can't burn the invitation.
        raise InviteEmailMismatch()
    if provider_for_user(db, user.id) is not None:
        raise err("ALREADY_PROVIDER", "This account is already an expert.")
    row.user_id = user.id
    row.invite_token_hash = None  # single use: a replay finds nothing
    row.invite_expires_at = None
    row.terms_version = TERMS_VERSION
    row.terms_accepted_at = datetime.now(UTC)
    if not row.display_name and user.profile and user.profile.full_name:
        row.display_name = user.profile.full_name
    db.flush()
    write_audit(
        db,
        "marketplace.provider.invite_accepted",
        actor_user_id=user.id,
        entity_type="marketplace_provider",
        entity_id=row.id,
        ip=ip,
        user_agent=user_agent,
        details={"terms_version": TERMS_VERSION},
    )
    return row


def apply_as_provider(
    db: Session, user: User, *, category: str | None, ip: str | None, user_agent: str | None
) -> MarketplaceProvider:
    if not applications_open():
        raise err("APPLICATIONS_CLOSED", "Expert applications aren't open right now.", 403)
    if category is not None and category not in CATEGORIES:
        raise err("VALIDATION_ERROR", "Unknown category.", 422)
    if provider_for_user(db, user.id) is not None:
        raise err("ALREADY_PROVIDER", "You've already applied or are an expert.")
    pending = db.query(MarketplaceProvider).filter(MarketplaceProvider.email == user.email).first()
    if pending is not None:
        raise err("INVITE_PENDING", "You have an invitation waiting. Use the link in your email.")
    row = MarketplaceProvider(
        user_id=user.id,
        email=user.email,
        status=ProviderStatus.applied,
        source="application",
        category=category,
        categories=[],
        languages=[],
        display_name=user.profile.full_name if user.profile else None,
        terms_version=TERMS_VERSION,
        terms_accepted_at=datetime.now(UTC),
        vetting_json={},
    )
    db.add(row)
    db.flush()
    write_audit(
        db,
        "marketplace.provider.applied",
        actor_user_id=user.id,
        entity_type="marketplace_provider",
        entity_id=row.id,
        ip=ip,
        user_agent=user_agent,
        details={"category": category},
    )
    return row


# ------------------------------------------------------------------ profile


def check_editable(row: MarketplaceProvider) -> None:
    if row.status in PROFILE_EDITABLE:
        return
    if row.status == ProviderStatus.in_review:
        raise err(
            "PROFILE_LOCKED",
            "Your application is under review. You can edit it if we ask for changes.",
        )
    if row.status == ProviderStatus.suspended:
        raise err("PROVIDER_SUSPENDED", "Your profile is paused. Contact support.", 403)
    raise err("PROVIDER_REJECTED", "This application was not approved.", 403)


def flag_if_screened(
    db: Session,
    user: User,
    row: MarketplaceProvider,
    changes: dict[str, Any],
    *,
    ip: str | None,
    user_agent: str | None,
) -> None:
    """Re-run the pre-screen after an edit. D-MP-L-new: an approved expert's edit stays live, but
    if the screen trips on what they changed it lands in the Admin queue for a human to look at."""
    screen = refresh_prescreen(row)
    flagged = [f for f in screen["flags"] if f["field"] in changes]
    if row.status != ProviderStatus.approved or not flagged:
        return
    now = datetime.now(UTC).isoformat()
    prior = row.vetting_json.get("profile_flag") or {}
    # Merge, never replace: an earlier unreviewed flag must not be wiped by a later edit.
    merged = list(prior.get("flags", []))
    merged += [f for f in flagged if f not in merged]
    row.vetting_json = {
        **row.vetting_json,
        "profile_flag": {"at": prior.get("at", now), "updated_at": now, "flags": merged},
    }
    write_audit(
        db,
        "marketplace.provider.profile_edit_flagged",
        actor_user_id=user.id,
        entity_type="marketplace_provider",
        entity_id=row.id,
        ip=ip,
        user_agent=user_agent,
        details={"flags": flagged},
    )


def update_profile(
    db: Session,
    user: User,
    provider_id: uuid.UUID,
    changes: dict[str, Any],
    *,
    ip: str | None,
    user_agent: str | None,
) -> MarketplaceProvider:
    row = lock_provider(db, provider_id)
    check_editable(row)
    if "timezone" in changes:
        try:
            ZoneInfo(changes["timezone"])
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise err("VALIDATION_ERROR", "Unknown timezone.", 422) from exc
    if "category" in changes:
        cat = changes["category"]
        if cat is not None and cat not in CATEGORIES:
            raise err("VALIDATION_ERROR", "Unknown category.", 422)
        if cat != row.category and row.status == ProviderStatus.approved:
            raise err(
                "CATEGORY_LOCKED",
                "You were vetted for this category. Contact support to change it.",
            )
    category = changes.get("category", row.category)
    if "categories" in changes:
        extra = changes["categories"]
        bad = [c for c in extra if c not in CATEGORIES]
        if bad:
            raise err("VALIDATION_ERROR", "Unknown category.", 422)
        regulated_extra = [c for c in extra if c in REGULATED and c != category]
        if regulated_extra:
            raise err(
                "REGULATED_CATEGORY",
                "Legal and Accounting experts must be vetted for it as their main category.",
                422,
            )
    if "avatar_file_id" in changes and changes["avatar_file_id"] is not None:
        f = get_owned(db, user.id, changes["avatar_file_id"])
        if f.purpose != MarketplaceFilePurpose.avatar:
            raise err("VALIDATION_ERROR", "That file isn't an avatar image.", 422)
    if (
        row.status == ProviderStatus.approved
        and "display_name" in changes
        and changes["display_name"] != row.display_name
    ):
        raise err(
            "NAME_LOCKED",
            "Your name was verified, so it can't be changed here. Contact support to change it.",
            422,
        )
    for field, value in changes.items():
        setattr(row, field, list(value) if isinstance(value, list) else value)
    flag_if_screened(db, user, row, changes, ip=ip, user_agent=user_agent)
    db.flush()
    write_audit(
        db,
        "marketplace.provider.profile_updated",
        actor_user_id=user.id,
        entity_type="marketplace_provider",
        entity_id=row.id,
        ip=ip,
        user_agent=user_agent,
        details={"fields": sorted(changes)},
    )
    return row


# ------------------------------------------------------------------ portfolio


def _portfolio_file(db: Session, user: User, file_id: uuid.UUID | None) -> None:
    if file_id is None:
        return
    f = get_owned(db, user.id, file_id)
    if f.purpose != MarketplaceFilePurpose.portfolio:
        raise err("VALIDATION_ERROR", "That file isn't a portfolio upload.", 422)


def add_portfolio(
    db: Session, user: User, provider_id: uuid.UUID, data: dict[str, Any]
) -> MarketplaceProviderPortfolio:
    row = lock_provider(db, provider_id)
    check_editable(row)
    items = portfolio_rows(db, row.id)
    if len(items) >= MAX_PORTFOLIO_ITEMS:
        raise err("PORTFOLIO_FULL", f"You can add up to {MAX_PORTFOLIO_ITEMS} portfolio items.")
    if not data.get("url") and not data.get("file_id"):
        raise err("VALIDATION_ERROR", "Add a link or upload a file for this item.", 422)
    _portfolio_file(db, user, data.get("file_id"))
    item = MarketplaceProviderPortfolio(
        provider_id=row.id, position=max((i.position for i in items), default=-1) + 1, **data
    )
    db.add(item)
    db.flush()
    return item


def _portfolio_item(
    db: Session, provider_id: uuid.UUID, item_id: uuid.UUID
) -> MarketplaceProviderPortfolio:
    item = (
        db.query(MarketplaceProviderPortfolio)
        .filter(
            MarketplaceProviderPortfolio.id == item_id,
            MarketplaceProviderPortfolio.provider_id == provider_id,
        )
        .first()
    )
    if item is None:
        raise NotFound()
    return item


def update_portfolio(
    db: Session, user: User, provider_id: uuid.UUID, item_id: uuid.UUID, changes: dict[str, Any]
) -> MarketplaceProviderPortfolio:
    row = lock_provider(db, provider_id)
    check_editable(row)
    item = _portfolio_item(db, row.id, item_id)
    if "file_id" in changes:
        _portfolio_file(db, user, changes["file_id"])
    for field, value in changes.items():
        setattr(item, field, value)
    if not item.url and not item.file_id:
        raise err("VALIDATION_ERROR", "Add a link or upload a file for this item.", 422)
    db.flush()
    return item


def delete_portfolio(db: Session, provider_id: uuid.UUID, item_id: uuid.UUID) -> None:
    row = lock_provider(db, provider_id)
    check_editable(row)
    db.delete(_portfolio_item(db, row.id, item_id))
    db.flush()


# ------------------------------------------------------------------ submit


def submit(
    db: Session, user: User, provider_id: uuid.UUID, *, ip: str | None, user_agent: str | None
) -> tuple[MarketplaceProvider, list[EmailMessage]]:
    row = lock_provider(db, provider_id)
    if row.status not in DRAFT:
        raise err("INVALID_STATE", "This application can't be submitted right now.")
    missing = submit_blockers(db, row)
    if missing:
        raise AppError(
            code="SUBMISSION_INCOMPLETE",
            message="A few things are still needed before you can submit.",
            http_status=422,
            field_errors=missing,
        )
    refresh_prescreen(row)
    if row.status == ProviderStatus.changes_requested:
        # Ticks were made against the evidence as it stood. The expert may have swapped a
        # credential or changed their name or category since, so staff re-verify from scratch.
        row.vetting_json = {**(row.vetting_json or {}), "checks": {}}
        row.licence_expires_on = None
    row.status = ProviderStatus.in_review
    row.submitted_at = datetime.now(UTC)
    row.review_message = None
    db.flush()
    write_audit(
        db,
        "marketplace.provider.submitted",
        actor_user_id=user.id,
        entity_type="marketplace_provider",
        entity_id=row.id,
        ip=ip,
        user_agent=user_agent,
        details={"category": row.category},
    )
    return row, [emails.application_received_email(row.email, name=row.display_name)]


def delete_own_file(db: Session, user: User, row: MarketplaceProvider, file_id: uuid.UUID) -> None:
    f = get_owned(db, user.id, file_id)
    if f.purpose == MarketplaceFilePurpose.credential and row.status not in CREDENTIALS_DELETABLE:
        raise err(
            "CREDENTIALS_LOCKED",
            "Credential documents can't be removed while your application is under review "
            "or after approval.",
        )
    if row.status not in PROFILE_EDITABLE and f.purpose != MarketplaceFilePurpose.credential:
        check_editable(row)
    if row.avatar_file_id == f.id:
        row.avatar_file_id = None
    erase(db, f)
    write_audit(
        db,
        "marketplace.file.deleted",
        actor_user_id=user.id,
        entity_type="marketplace_file",
        entity_id=f.id,
        details={"purpose": f.purpose.value},
    )

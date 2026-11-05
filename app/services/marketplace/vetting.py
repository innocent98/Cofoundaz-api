"""Staff side of Marketplace vetting (design §4, D-MP-6): queue, checklist, decisions, audited
credential reads. Callers have already passed `require_staff("moderation")`, a reason, and (for the
decisions that change trust) a fresh authenticator code.

The checklist drives the public 'Vetted' badge: what staff tick is what the tooltip says, no more.
Approval is refused until every check the category's rubric needs is ticked."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.staff_deps import StaffContext
from app.core import pagination as core
from app.core.errors import NotFound
from app.db.models.audit import AuditLog
from app.db.models.enums import MarketplaceFilePurpose, ProviderStatus
from app.db.models.marketplace import MarketplaceFile, MarketplaceProvider
from app.db.models.user import User
from app.platform.audit import write_audit
from app.platform.email import EmailMessage
from app.platform.events import event_bus
from app.services.marketplace import emails
from app.services.marketplace.constants import (
    CHECK_KEYS,
    LICENCE_EXPIRY_WARNING_DAYS,
    REFERENCES_REQUIRED,
    REGULATED,
    required_checks,
)
from app.services.marketplace.files import read_bytes, retire_files, serialize_file
from app.services.marketplace.providers import (
    badge,
    badge_preview,
    err,
    live_files,
    lock_provider,
    portfolio_rows,
    serialize_portfolio,
)
from app.services.staff.like import contains_pattern

VETTABLE = frozenset(
    {
        ProviderStatus.in_review,
        ProviderStatus.changes_requested,
        ProviderStatus.approved,
        ProviderStatus.suspended,
    }
)
REJECT_TEMPLATES: dict[str, str] = {
    "incomplete_documents": "Your documents were incomplete or unreadable",
    "licence_not_verified": "We couldn't verify your professional licence",
    "references_not_verified": "We couldn't verify your references",
    "outside_scope": "Your services are outside what the Marketplace offers",
    "other": "Your application did not meet our requirements",
}


def _client(ctx: StaffContext, ip: str | None, ua: str | None) -> dict[str, Any]:
    return {"actor_user_id": ctx.user.id, "ip": ip, "user_agent": ua}


# ------------------------------------------------------------------ views


def licence_expiring(row: MarketplaceProvider) -> bool:
    if row.licence_expires_on is None or row.status not in (
        ProviderStatus.approved,
        ProviderStatus.suspended,
    ):
        return False
    return row.licence_expires_on <= date.today() + timedelta(days=LICENCE_EXPIRY_WARNING_DAYS)


def missing_checks(row: MarketplaceProvider) -> list[str]:
    """Rubric checks not yet satisfied for this provider's category."""
    checks = (row.vetting_json or {}).get("checks", {})
    regulated = row.category in REGULATED
    missing: list[str] = []
    for key in required_checks(row.category):
        entry = checks.get(key) or {}
        if not entry.get("done"):
            missing.append(key)
        elif key == "references" and int(entry.get("count") or 0) < REFERENCES_REQUIRED[regulated]:
            missing.append(key)
        elif key == "licence":
            expires = entry.get("expires_on")
            if not expires or date.fromisoformat(expires) <= date.today():
                missing.append(key)
    return missing


def summary(row: MarketplaceProvider) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "user_id": str(row.user_id) if row.user_id else None,
        "email": row.email,
        "display_name": row.display_name,
        "headline": row.headline,
        "category": row.category,
        "status": row.status.value,
        "source": row.source,
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        "created_at": row.created_at.isoformat(),
        "invite_pending": row.user_id is None and row.invite_token_hash is not None,
        "invite_expires_at": row.invite_expires_at.isoformat() if row.invite_expires_at else None,
        "licence_expires_on": (
            row.licence_expires_on.isoformat() if row.licence_expires_on else None
        ),
        "licence_expiring": licence_expiring(row),
        "prescreen_flags": ((row.vetting_json or {}).get("prescreen") or {}).get("flags", []),
        "vetted_at": row.vetted_at.isoformat() if row.vetted_at else None,
    }


def detail(db: Session, provider_id: uuid.UUID) -> dict[str, Any]:
    row = db.query(MarketplaceProvider).filter(MarketplaceProvider.id == provider_id).first()
    if row is None:
        raise err("PROVIDER_NOT_FOUND", "No such expert.", 404)
    history = (
        db.query(AuditLog, User.email)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .filter(AuditLog.entity_type == "marketplace_provider", AuditLog.entity_id == row.id)
        .order_by(AuditLog.created_at.desc())
        .limit(50)
        .all()
    )
    return {
        **summary(row),
        "bio": row.bio,
        "categories": list(row.categories),
        "languages": list(row.languages),
        "timezone": row.timezone,
        "location": row.location,
        "terms_version": row.terms_version,
        "terms_accepted_at": row.terms_accepted_at.isoformat() if row.terms_accepted_at else None,
        "review_message": row.review_message,
        "suspended_reason": row.suspended_reason,
        "commission_bps": row.commission_bps,
        "required_checks": list(required_checks(row.category)),
        "references_required": (
            REFERENCES_REQUIRED[row.category in REGULATED] if row.category else None
        ),
        "missing_checks": missing_checks(row),
        "checks": (row.vetting_json or {}).get("checks", {}),
        "badge": badge(row),
        "badge_preview": badge_preview(row),
        "portfolio": [serialize_portfolio(p) for p in portfolio_rows(db, row.id)],
        "files": [serialize_file(f) for f in live_files(db, row.user_id)],
        "history": [
            {
                "action": a.action,
                "at": a.created_at.isoformat(),
                "actor_email": email,
                "reason": a.reason,
            }
            for a, email in history
        ],
    }


def counts(db: Session) -> dict[str, Any]:
    by_status: dict[ProviderStatus, int] = {
        status: int(n)
        for status, n in db.query(MarketplaceProvider.status, func.count(MarketplaceProvider.id))
        .group_by(MarketplaceProvider.status)
        .all()
    }
    expiring = (
        db.query(func.count(MarketplaceProvider.id))
        .filter(
            MarketplaceProvider.status.in_([ProviderStatus.approved, ProviderStatus.suspended]),
            MarketplaceProvider.licence_expires_on
            <= date.today() + timedelta(days=LICENCE_EXPIRY_WARNING_DAYS),
        )
        .scalar()
        or 0
    )
    return {
        "by_status": {s.value: by_status.get(s, 0) for s in ProviderStatus},
        "licence_expiring": int(expiring),
    }


def list_providers(
    db: Session,
    *,
    status: ProviderStatus | None,
    category: str | None,
    query: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    q = db.query(MarketplaceProvider)
    if status:
        q = q.filter(MarketplaceProvider.status == status)
    if category:
        q = q.filter(MarketplaceProvider.category == category)
    if query and query.strip():
        pat = contains_pattern(query.strip())
        q = q.filter(
            or_(
                MarketplaceProvider.email.ilike(pat, escape="\\"),
                MarketplaceProvider.display_name.ilike(pat, escape="\\"),
            )
        )
    if cursor:
        ts, rid = core.decode_cursor(cursor)
        q = q.filter(
            or_(
                MarketplaceProvider.created_at > ts,
                (MarketplaceProvider.created_at == ts) & (MarketplaceProvider.id > rid),
            )
        )
    rows = (
        q.order_by(MarketplaceProvider.created_at.asc(), MarketplaceProvider.id.asc())
        .limit(limit + 1)
        .all()
    )
    nxt = (
        core.encode_cursor(rows[limit - 1].created_at, rows[limit - 1].id)
        if len(rows) > limit
        else None
    )
    return [summary(r) for r in rows[:limit]], nxt


# ------------------------------------------------------------------ checklist


def set_vetting(
    db: Session,
    ctx: StaffContext,
    provider_id: uuid.UUID,
    checks: dict[str, dict[str, Any]],
    *,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    row = lock_provider(db, provider_id)
    if row.status not in VETTABLE:
        raise err("INVALID_STATE", "This application isn't ready for vetting yet.")
    if not row.category:
        raise err("CATEGORY_MISSING", "The expert hasn't chosen a category yet.", 422)
    allowed = set(required_checks(row.category))
    stored: dict[str, Any] = dict((row.vetting_json or {}).get("checks", {}))
    now = datetime.now(UTC).isoformat()
    for key, payload in checks.items():
        if key not in CHECK_KEYS or key not in allowed:
            raise err(
                "VALIDATION_ERROR", f"The '{key}' check doesn't apply to {row.category}.", 422
            )
        if not payload["done"]:
            stored.pop(key, None)
            continue
        entry: dict[str, Any] = {
            "done": True,
            "note": payload.get("note"),
            "by": str(ctx.user.id),
            "at": now,
        }
        if key == "licence":
            expires = payload.get("expires_on")
            if expires is None or expires <= date.today():
                raise err("VALIDATION_ERROR", "Enter a licence expiry date in the future.", 422)
            entry["expires_on"] = expires.isoformat()
        if key == "references":
            entry["count"] = int(payload.get("count") or 0)
            if entry["count"] < 1:
                raise err("VALIDATION_ERROR", "Enter how many references were checked.", 422)
        prior = stored.get(key)
        if prior and prior.get("done"):
            entry["by"], entry["at"] = prior.get("by", entry["by"]), prior.get("at", entry["at"])
            entry["updated_by"], entry["updated_at"] = str(ctx.user.id), now
        stored[key] = entry
    row.vetting_json = {**(row.vetting_json or {}), "checks": stored}
    lic = stored.get("licence")
    row.licence_expires_on = date.fromisoformat(lic["expires_on"]) if lic else None
    db.flush()
    write_audit(
        db,
        "marketplace.provider.vetting_updated",
        entity_type="marketplace_provider",
        entity_id=row.id,
        details={"ticked": sorted(k for k, v in stored.items() if v.get("done"))},
        **_client(ctx, ip, ua),
    )
    return detail(db, row.id)


# ------------------------------------------------------------------ decisions


def _decide(
    db: Session,
    ctx: StaffContext,
    provider_id: uuid.UUID,
    *,
    allowed: set[ProviderStatus],
    target: ProviderStatus,
    action: str,
    reason: str,
    ip: str | None,
    ua: str | None,
    details: dict[str, Any] | None = None,
) -> MarketplaceProvider:
    row = lock_provider(db, provider_id)
    if row.status not in allowed:
        raise err(
            "INVALID_STATE",
            f"Can't do that while the expert is '{row.status.value}'.",
        )
    before = row.status
    row.status = target
    write_audit(
        db,
        action,
        entity_type="marketplace_provider",
        entity_id=row.id,
        reason=reason,
        details={"from": before.value, "to": target.value, **(details or {})},
        **_client(ctx, ip, ua),
    )
    return row


def approve(
    db: Session,
    ctx: StaffContext,
    provider_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> tuple[MarketplaceProvider, list[EmailMessage]]:
    row = lock_provider(db, provider_id)
    gaps = missing_checks(row) if row.status == ProviderStatus.in_review else []
    if row.status == ProviderStatus.in_review and gaps:
        raise err(
            "VETTING_INCOMPLETE",
            "Tick every check this category needs before approving: " + ", ".join(gaps) + ".",
            422,
        )
    row = _decide(
        db,
        ctx,
        provider_id,
        allowed={ProviderStatus.in_review},
        target=ProviderStatus.approved,
        action="marketplace.provider.approved",
        reason=reason,
        ip=ip,
        ua=ua,
        details={"checks": sorted((row.vetting_json or {}).get("checks", {}))},
    )
    row.vetted_at, row.vetted_by, row.review_message = datetime.now(UTC), ctx.user.id, None
    event_bus.publish(db, "marketplace.provider.approved", {"provider_id": str(row.id)})
    return row, [emails.approved_email(row.email, name=row.display_name)]


def reject(
    db: Session,
    ctx: StaffContext,
    provider_id: uuid.UUID,
    *,
    template: str,
    message: str | None,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> tuple[MarketplaceProvider, list[EmailMessage]]:
    row = _decide(
        db,
        ctx,
        provider_id,
        allowed={ProviderStatus.in_review, ProviderStatus.changes_requested},
        target=ProviderStatus.rejected,
        action="marketplace.provider.rejected",
        reason=reason,
        ip=ip,
        ua=ua,
        details={"template": template},
    )
    row.review_message = message or REJECT_TEMPLATES[template]
    erased = retire_files(db, row.user_id) if row.user_id else 0  # NDPA: delete on rejection
    write_audit(
        db,
        "marketplace.provider.files_erased",
        entity_type="marketplace_provider",
        entity_id=row.id,
        details={"files": erased},
        **_client(ctx, ip, ua),
    )
    event_bus.publish(db, "marketplace.provider.rejected", {"provider_id": str(row.id)})
    mail = emails.rejected_email(
        row.email, name=row.display_name, label=REJECT_TEMPLATES[template], message=message
    )
    return row, [mail]


def request_changes(
    db: Session,
    ctx: StaffContext,
    provider_id: uuid.UUID,
    *,
    message: str,
    ip: str | None,
    ua: str | None,
) -> tuple[MarketplaceProvider, list[EmailMessage]]:
    row = _decide(
        db,
        ctx,
        provider_id,
        allowed={ProviderStatus.in_review},
        target=ProviderStatus.changes_requested,
        action="marketplace.provider.changes_requested",
        reason=message,
        ip=ip,
        ua=ua,
    )
    row.review_message = message
    return row, [emails.changes_requested_email(row.email, name=row.display_name, message=message)]


def suspend(
    db: Session,
    ctx: StaffContext,
    provider_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> tuple[MarketplaceProvider, list[EmailMessage]]:
    row = _decide(
        db,
        ctx,
        provider_id,
        allowed={ProviderStatus.approved},
        target=ProviderStatus.suspended,
        action="marketplace.provider.suspended",
        reason=reason,
        ip=ip,
        ua=ua,
    )
    row.suspended_reason = reason
    return row, [emails.suspended_email(row.email, name=row.display_name)]


def unsuspend(
    db: Session,
    ctx: StaffContext,
    provider_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> MarketplaceProvider:
    row = lock_provider(db, provider_id)
    if row.status == ProviderStatus.suspended and missing_checks(row):
        raise err("VETTING_INCOMPLETE", "Re-tick the expired or missing checks first.", 422)
    row = _decide(
        db,
        ctx,
        provider_id,
        allowed={ProviderStatus.suspended},
        target=ProviderStatus.approved,
        action="marketplace.provider.unsuspended",
        reason=reason,
        ip=ip,
        ua=ua,
    )
    row.suspended_reason = None
    return row


# ------------------------------------------------------------------ credential reads


def open_file(
    db: Session,
    ctx: StaffContext,
    provider_id: uuid.UUID,
    file_id: uuid.UUID,
    *,
    ip: str | None,
    ua: str | None,
) -> tuple[MarketplaceFile, bytes]:
    """Staff read an expert's file. Every read is audited, including credential scans (design §4).
    Only files that belong to that provider's account are reachable through that provider."""
    row = db.query(MarketplaceProvider).filter(MarketplaceProvider.id == provider_id).first()
    if row is None or row.user_id is None:
        raise NotFound()
    f = (
        db.query(MarketplaceFile)
        .filter(
            MarketplaceFile.id == file_id,
            MarketplaceFile.owner_user_id == row.user_id,
            MarketplaceFile.deleted_at.is_(None),
        )
        .first()
    )
    if f is None:
        raise NotFound()
    data = read_bytes(f)
    write_audit(
        db,
        "marketplace.file.viewed",
        entity_type="marketplace_file",
        entity_id=f.id,
        details={
            "provider_id": str(row.id),
            "purpose": f.purpose.value,
            "filename": f.filename,
            "credential": f.purpose == MarketplaceFilePurpose.credential,
        },
        **_client(ctx, ip, ua),
    )
    return f, data

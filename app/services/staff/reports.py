"""Staff moderation console: the reported-issues queue, one report, and the four actions.

D-ADM-9: the only text a route here returns is the snippet copied into the report when it was
filed. Nothing follows `item_id` back to the chat, thread or document; the one time the item
itself is touched is to flip its `removed_by_moderation_at` flag. Every action locks the report
row, writes one audit row (ids and reasons, never the snippet) and commits it with the change.
"""

import uuid
from typing import Any

from sqlalchemy import ColumnElement, and_, or_
from sqlalchemy.orm import Session

from app.api.staff_deps import StaffContext
from app.core.errors import NotFound
from app.db.models.billing import BillingSubscription
from app.db.models.content_report import ContentReport
from app.db.models.enums import ReportItemType, ReportReason, ReportStatus
from app.db.models.startup import Startup
from app.db.models.user import User, UserProfile
from app.platform.audit import write_audit
from app.services.reports import core
from app.services.staff.like import contains_pattern

TABS: dict[str, tuple[ReportStatus, ...]] = {
    "open": (ReportStatus.open,),
    "escalated": (ReportStatus.escalated,),
    "closed": (ReportStatus.dismissed, ReportStatus.removed, ReportStatus.restored),
}
LIST_SNIPPET = 200


def _people(db: Session, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, dict[str, Any]]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    rows = (
        db.query(User.id, User.email, UserProfile.full_name)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .filter(User.id.in_(wanted))
        .all()
    )
    return {
        uid: {"id": str(uid), "name": name or email, "email": email} for uid, email, name in rows
    }


def _row(
    r: ContentReport,
    ws_name: str | None,
    plan: str | None,
    people: dict[uuid.UUID, dict[str, Any]],
    *,
    full: bool,
) -> dict[str, Any]:
    author = people.get(r.item_author_id) if r.item_author_id else None
    out: dict[str, Any] = {
        "id": str(r.id),
        "item_type": r.item_type.value,
        "reason": r.reason.value,
        "note": r.note,
        "status": r.status.value,
        "snippet": r.snippet if full else r.snippet[:LIST_SNIPPET],
        "snippet_truncated": (not full) and len(r.snippet) > LIST_SNIPPET,
        "reporter": people.get(r.reporter_id) if r.reporter_id else None,
        # The author's name only: staff have no business with the author's email (R5). An AI
        # reply has no human author.
        "author": {"id": author["id"], "name": author["name"]} if author else None,
        "workspace": {"id": str(r.startup_id), "name": ws_name, "plan_key": plan or "starter"},
        "created_at": core.iso(r.created_at),
        "escalated_at": core.iso(r.escalated_at),
        "decided_at": core.iso(r.decided_at),
    }
    if full:
        out["escalation_reason"] = r.escalation_reason
        out["decision_reason"] = r.decision_reason
        out["escalated_by"] = people.get(r.escalated_by) if r.escalated_by else None
        out["decided_by"] = people.get(r.decided_by) if r.decided_by else None
    return out


def _base(db: Session):  # type: ignore[no-untyped-def]
    return (
        db.query(ContentReport, Startup.name, BillingSubscription.plan_key)
        .join(Startup, Startup.id == ContentReport.startup_id)
        .outerjoin(BillingSubscription, BillingSubscription.startup_id == Startup.id)
    )


def list_reports(
    db: Session,
    *,
    tab: str,
    item_type: ReportItemType | None,
    reason: ReportReason | None,
    query: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    q = _base(db).filter(ContentReport.status.in_(TABS[tab]))
    if item_type:
        q = q.filter(ContentReport.item_type == item_type)
    if reason:
        q = q.filter(ContentReport.reason == reason)
    if query and query.strip():
        pat = contains_pattern(query.strip())
        reporter = (
            db.query(User.id)
            .outerjoin(UserProfile, UserProfile.user_id == User.id)
            .filter(
                or_(
                    User.email.ilike(pat, escape="\\"),
                    UserProfile.full_name.ilike(pat, escape="\\"),
                )
            )
        )
        conds: list[ColumnElement[bool]] = [
            ContentReport.snippet.ilike(pat, escape="\\"),
            Startup.name.ilike(pat, escape="\\"),
            ContentReport.reporter_id.in_(reporter),
        ]
        q = q.filter(or_(*conds))
    if cursor:
        ts, rid = core.decode_cursor(cursor)
        q = q.filter(
            or_(
                ContentReport.created_at < ts,
                and_(ContentReport.created_at == ts, ContentReport.id < rid),
            )
        )
    rows = (
        q.order_by(ContentReport.created_at.desc(), ContentReport.id.desc()).limit(limit + 1).all()
    )
    page = rows[:limit]
    people = _people(db, {x for r, _, _ in page for x in (r.reporter_id, r.item_author_id)})
    items = [_row(r, ws, plan, people, full=False) for r, ws, plan in page]
    nxt = core.encode_cursor(page[-1][0].created_at, page[-1][0].id) if len(rows) > limit else None
    return items, nxt


def counts(db: Session) -> dict[str, int]:
    return {
        tab: db.query(ContentReport).filter(ContentReport.status.in_(statuses)).count()
        for tab, statuses in TABS.items()
    }


def detail(db: Session, report_id: uuid.UUID) -> dict[str, Any]:
    found = _base(db).filter(ContentReport.id == report_id).first()
    if found is None:
        raise NotFound()
    r, ws, plan = found
    people = _people(db, {r.reporter_id, r.item_author_id, r.escalated_by, r.decided_by})
    out = _row(r, ws, plan, people, full=True)
    # How many people reported this same item (a count; no other report's text).
    out["reports_on_item"] = (
        db.query(ContentReport)
        .filter(ContentReport.item_type == r.item_type, ContentReport.item_id == r.item_id)
        .count()
    )
    return out


# ---------------------------------------------------------------- actions


def _load(db: Session, report_id: uuid.UUID) -> ContentReport:
    r = db.query(ContentReport).filter(ContentReport.id == report_id).with_for_update().first()
    if r is None:
        raise NotFound()
    return r


def _live_only(ctx: StaffContext, r: ContentReport) -> None:
    if r.status not in (ReportStatus.open, ReportStatus.escalated):
        raise core.err("REPORT_CLOSED", "This report is already closed.", 409)
    if r.status == ReportStatus.escalated and not ctx.is_super_admin:
        # R7: an escalation is the moderator asking a Super Admin to decide.
        raise core.err(
            "SUPER_ADMIN_ONLY", "Only a Super Admin can decide an escalated report.", 403
        )


def _super_admin_for_escalations(ctx: StaffContext, reports: list[ContentReport]) -> None:
    if not ctx.is_super_admin and any(x.escalated_at is not None for x in reports):
        raise core.err(
            "SUPER_ADMIN_ONLY", "Only a Super Admin can decide an escalated report.", 403
        )


def _audit(
    db: Session,
    ctx: StaffContext,
    action: str,
    r: ContentReport,
    *,
    reason: str,
    details: dict[str, Any] | None = None,
    ip: str | None,
    ua: str | None,
) -> None:
    write_audit(
        db,
        action,
        actor_user_id=ctx.user.id,
        startup_id=r.startup_id,
        entity_type="content_report",
        entity_id=r.id,
        ip=ip,
        user_agent=ua,
        reason=reason,
        details={"item_type": r.item_type.value, "item_id": str(r.item_id), **(details or {})},
    )


def dismiss(
    db: Session,
    ctx: StaffContext,
    report_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    r = _load(db, report_id)
    _live_only(ctx, r)
    was = r.status
    r.status = ReportStatus.dismissed
    r.decided_by, r.decided_at, r.decision_reason = ctx.user.id, core.now_utc(), reason
    _audit(db, ctx, "report.dismissed", r, reason=reason, details={"from": was.value}, ip=ip, ua=ua)
    db.commit()
    return detail(db, report_id)


def escalate(
    db: Session,
    ctx: StaffContext,
    report_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    r = _load(db, report_id)
    if r.status == ReportStatus.escalated:
        raise core.err("ALREADY_ESCALATED", "This report is already escalated.", 409)
    _live_only(ctx, r)
    r.status = ReportStatus.escalated
    r.escalated_by, r.escalated_at, r.escalation_reason = ctx.user.id, core.now_utc(), reason
    _audit(db, ctx, "report.escalated", r, reason=reason, ip=ip, ua=ua)
    db.commit()
    return detail(db, report_id)


def remove(
    db: Session,
    ctx: StaffContext,
    report_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    r = _load(db, report_id)
    _live_only(ctx, r)
    now = core.now_utc()
    # R8: everyone who reported this item is answered by the one removal.
    others = (
        db.query(ContentReport)
        .filter(
            ContentReport.item_type == r.item_type,
            ContentReport.item_id == r.item_id,
            ContentReport.status.in_((ReportStatus.open, ReportStatus.escalated)),
        )
        .with_for_update()
        .all()
    )
    # An escalated sibling is a Super Admin's call (R7): a removal must not close it for them.
    _super_admin_for_escalations(ctx, others)
    hidden = core.set_removed(db, r, True, now)
    for o in others:
        o.status = ReportStatus.removed
        o.decided_by, o.decided_at, o.decision_reason = ctx.user.id, now, reason
    _audit(
        db,
        ctx,
        "report.item_removed",
        r,
        reason=reason,
        details={"reports_closed": len(others), "item_existed": hidden},
        ip=ip,
        ua=ua,
    )
    db.commit()
    return detail(db, report_id)


def restore(
    db: Session,
    ctx: StaffContext,
    report_id: uuid.UUID,
    *,
    reason: str,
    ip: str | None,
    ua: str | None,
) -> dict[str, Any]:
    r = _load(db, report_id)
    if r.status != ReportStatus.removed:
        raise core.err("NOT_REMOVED", "Only a removed item can be restored.", 409)
    now = core.now_utc()
    others = (
        db.query(ContentReport)
        .filter(
            ContentReport.item_type == r.item_type,
            ContentReport.item_id == r.item_id,
            ContentReport.status == ReportStatus.removed,
        )
        .with_for_update()
        .all()
    )
    # Undoing a Super Admin's decision on an escalated report is theirs too (R7).
    _super_admin_for_escalations(ctx, others)
    shown = core.set_removed(db, r, False, now)
    for o in others:
        o.status = ReportStatus.restored
        o.decided_by, o.decided_at, o.decision_reason = ctx.user.id, now, reason
    _audit(
        db,
        ctx,
        "report.item_restored",
        r,
        reason=reason,
        details={"reports_reopened_as_restored": len(others), "item_existed": shown},
        ip=ip,
        ua=ua,
    )
    db.commit()
    return detail(db, report_id)

from datetime import UTC, datetime, time, timedelta
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import String, and_, cast, exists, func, literal, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import log
from app.db.models.assessment import Assessment
from app.db.models.enums import (
    AssessmentStatus,
    CampaignStatus,
    ContentStatus,
    InvoiceStatus,
    MembershipStatus,
    RoadmapStatus,
    StageKind,
)
from app.db.models.finance_connection import FinanceConnection
from app.db.models.invoice import Invoice
from app.db.models.marketing import Campaign, ContentCalendarEntry, MarketingMetric
from app.db.models.membership import Membership
from app.db.models.mission import MissionSettings
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.db.models.sales import Deal, PipelineStage, SalesSettings
from app.db.models.scheduled_run import ScheduledRun
from app.db.models.startup import Startup
from app.platform.jobs import job_dispatcher
from app.platform.outreach import imap_configured, outreach_configured
from app.services.finance.reminders import MAX_AUTO_REMINDERS, REMINDER_INTERVAL_DAYS
from app.services.notifications import digest

SCHED_MISSION = "scheduled.mission.generate"
SCHED_OVERDUE = "scheduled.roadmap.overdue"
SCHED_DUE_SOON = "scheduled.roadmap.due_soon"
DUE_SOON_DAYS = 2
SCHED_QUARTERLY = "scheduled.assessment.quarterly"
SCHED_KPI_SNAPSHOT = "scheduled.kpi.snapshot"
SCHED_HEALTH_NIGHTLY = "scheduled.health.recompute"
SCHED_MARKETING_PUBLISH = "scheduled.marketing.publish"
SCHED_MARKETING_READOUT = "scheduled.marketing.readout"
SCHED_PAYMENTS_SYNC = "scheduled.finance.payments_sync"
SCHED_SALES_STALL = "scheduled.sales.stall"
SCHED_SALES_SEND = "scheduled.sales.outreach_send"
SCHED_SALES_BOUNCES = "scheduled.sales.outreach_bounces"
OUTREACH_BUCKET_SECONDS = 300  # sequence sends and the bounce poll run every 5 minutes
SCHED_INVOICE_OVERDUE = "scheduled.finance.invoice_overdue"
SCHED_DOCUMENT_TRASH_PURGE = "scheduled.documents.trash_purge"
SCHED_NOTIF_DIGEST = "scheduled.notifications.digest"
INVOICE_OVERDUE_BATCH = 500
DEFAULT_STALL_DAYS = 14
SALES_STALL_BATCH = 500
READOUT_HOUR = 8  # local time; the first tick of the ISO week at or after this hour
MARKETING_PUBLISH_BATCH = 500


class Due(NamedTuple):
    task_key: str
    scope_key: str
    period_key: str
    job_type: str
    startup_id: Any
    payload: dict


def _claim(db: Session, task_key: str, scope_key: str, period_key: str) -> bool:
    """Claim (task, scope, period) exactly once. True if newly claimed, False if already taken.

    Inserts inside a SAVEPOINT so an IntegrityError (someone else claimed it) rolls back only
    this insert and leaves the caller's transaction usable.
    """
    try:
        with db.begin_nested():
            db.add(ScheduledRun(task_key=task_key, scope_key=scope_key, period_key=period_key))
            db.flush()
        return True
    except IntegrityError:
        return False


def _local(now: datetime) -> datetime:
    return now.astimezone(ZoneInfo(settings.SCHEDULER_TIMEZONE))


def _quarter_key(d: datetime) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def _active_startup_ids(db: Session) -> list[Any]:
    rows = (
        db.query(Startup.id)
        .join(Membership, Membership.startup_id == Startup.id)
        .filter(Membership.status == MembershipStatus.active, Startup.deleted_at.is_(None))
        .distinct()
        .all()
    )
    return [r[0] for r in rows]


def _due_missions(db: Session, now: datetime) -> list[Due]:
    """Once per workspace per local day, at that workspace's own `delivery_time` (PRD 04.5;
    `MISSION_GEN_HOUR`:00 when it has no settings row)."""
    local = _local(now)
    period = local.date().isoformat()
    claimed = {
        r[0]
        for r in db.query(ScheduledRun.scope_key)
        .filter(ScheduledRun.task_key == "mission.generate", ScheduledRun.period_key == period)
        .all()
    }
    delivery = dict(
        db.query(MissionSettings.startup_id, MissionSettings.delivery_time).tuples().all()
    )
    default = time(settings.MISSION_GEN_HOUR, 0)
    now_t = local.time()
    return [
        Due("mission.generate", str(sid), period, SCHED_MISSION, sid, {"startup_id": str(sid)})
        for sid in _active_startup_ids(db)
        if str(sid) not in claimed and now_t >= delivery.get(sid, default)
    ]


def _due_notification_digests(db: Session, now: datetime) -> list[Due]:
    """Digest emails (PRD 20.3): once per recipient and workspace per local day at their delivery
    time (weekly: on their chosen weekday). The job re-reads preferences before sending."""
    out: list[Due] = []
    for row, period in digest.due_rows(db, now):
        scope = row.user_id.hex + row.startup_id.hex  # 64 chars (scope_key limit)
        claimed = (
            db.query(ScheduledRun.id)
            .filter_by(task_key="notifications.digest", scope_key=scope, period_key=period)
            .first()
        )
        if claimed is None:
            payload = {"user_id": str(row.user_id), "startup_id": str(row.startup_id)}
            out.append(
                Due(
                    "notifications.digest",
                    scope,
                    period,
                    SCHED_NOTIF_DIGEST,
                    row.startup_id,
                    payload,
                )
            )
    return out


def _due_kpi_snapshots(db: Session, now: datetime) -> list[Due]:
    """Once per workspace per local day, from KPI_SNAPSHOT_HOUR on (PRD 02.1 nightly 02:00)."""
    local = _local(now)
    if local.hour < settings.KPI_SNAPSHOT_HOUR:
        return []
    period = local.date().isoformat()
    claimed = {
        r[0]
        for r in db.query(ScheduledRun.scope_key)
        .filter(ScheduledRun.task_key == "kpi.snapshot", ScheduledRun.period_key == period)
        .all()
    }
    return [
        Due("kpi.snapshot", str(sid), period, SCHED_KPI_SNAPSHOT, sid, {"startup_id": str(sid)})
        for sid in _active_startup_ids(db)
        if str(sid) not in claimed
    ]


def _due_health_nightly(db: Session, now: datetime) -> list[Due]:
    """Once per workspace per local day from HEALTH_NIGHTLY_HOUR: the PRD's nightly full pass,
    so live signals with no event (validation notes, interviews, finance drift) stay current."""
    local = _local(now)
    if local.hour < settings.HEALTH_NIGHTLY_HOUR:
        return []
    period = local.date().isoformat()
    claimed = {
        r[0]
        for r in db.query(ScheduledRun.scope_key)
        .filter(ScheduledRun.task_key == "health.nightly", ScheduledRun.period_key == period)
        .all()
    }
    return [
        Due("health.nightly", str(sid), period, SCHED_HEALTH_NIGHTLY, sid, {"startup_id": str(sid)})
        for sid in _active_startup_ids(db)
        if str(sid) not in claimed
    ]


def _due_marketing_posts(db: Session, now: datetime) -> list[Due]:
    """Marketing calendar posts whose `scheduled_at` has passed (PRD 10 `marketing.post.published`).
    One claim per (post, scheduled time): moving a post to a new time makes it claimable again,
    while a re-scan of the same time never double-fires. Capped per tick; the rest follow on the
    next one. The job only flips the status (D-MKT-8)."""
    rows = (
        db.query(
            ContentCalendarEntry.id,
            ContentCalendarEntry.startup_id,
            ContentCalendarEntry.scheduled_at,
        )
        .join(Startup, Startup.id == ContentCalendarEntry.startup_id)
        .filter(
            ContentCalendarEntry.status == ContentStatus.scheduled,
            ContentCalendarEntry.scheduled_at.isnot(None),
            ContentCalendarEntry.scheduled_at <= now,
            Startup.deleted_at.is_(None),
        )
        .order_by(ContentCalendarEntry.scheduled_at)
        .limit(MARKETING_PUBLISH_BATCH)
        .all()
    )
    if not rows:
        return []
    claimed = {
        (r[0], r[1])
        for r in db.query(ScheduledRun.scope_key, ScheduledRun.period_key)
        .filter(
            ScheduledRun.task_key == "marketing.publish",
            ScheduledRun.scope_key.in_([str(r[0]) for r in rows]),
        )
        .all()
    }
    due = []
    for eid, sid, at in rows:
        period = at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%f")
        if (str(eid), period) not in claimed:
            due.append(
                Due(
                    "marketing.publish",
                    str(eid),
                    period,
                    SCHED_MARKETING_PUBLISH,
                    sid,
                    {"startup_id": str(sid), "entry_id": str(eid)},
                )
            )
    return due


def _due_sales_stalls(db: Session, now: datetime) -> list[Due]:
    """Open deals quiet for the workspace's `stall_days` (default 14) that were not notified yet.
    The claim period is the deal's `last_activity_at` stamp, so one stall episode fires once even
    across repeated or concurrent scans, and new activity (a new stamp) opens a fresh episode."""
    stall_days = func.coalesce(SalesSettings.stall_days, DEFAULT_STALL_DAYS)
    rows = (
        db.query(Deal.id, Deal.startup_id, Deal.last_activity_at)
        .join(PipelineStage, PipelineStage.id == Deal.stage_id)
        .join(Startup, Startup.id == Deal.startup_id)
        .outerjoin(SalesSettings, SalesSettings.startup_id == Deal.startup_id)
        .filter(
            PipelineStage.kind == StageKind.open,
            PipelineStage.archived_at.is_(None),
            Startup.deleted_at.is_(None),
            Deal.stalled_notified_at.is_(None),
            Deal.last_activity_at <= literal(now) - func.make_interval(0, 0, 0, stall_days),
        )
        .order_by(Deal.last_activity_at)
        .limit(SALES_STALL_BATCH)
        .all()
    )
    return [
        Due(
            "sales.stall",
            str(did),
            at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%f"),
            SCHED_SALES_STALL,
            sid,
            {"startup_id": str(sid), "deal_id": str(did)},
        )
        for did, sid, at in rows
    ]


def _due_campaign_readouts(db: Session, now: datetime) -> list[Due]:
    """Once per ISO week per active campaign that recorded any numbers in the last 14 days (a
    campaign with no data gets no readout and costs no tokens)."""
    local = _local(now)
    if local.hour < READOUT_HOUR:
        return []
    iso = local.isocalendar()
    period = f"{iso.year}-W{iso.week:02d}"
    since = local.date() - timedelta(days=14)
    has_data = exists().where(
        MarketingMetric.campaign_id == Campaign.id, MarketingMetric.ts >= since
    )
    rows = (
        db.query(Campaign.id, Campaign.startup_id)
        .join(Startup, Startup.id == Campaign.startup_id)
        .filter(Campaign.status == CampaignStatus.active, Startup.deleted_at.is_(None), has_data)
        .all()
    )
    if not rows:
        return []
    claimed = {
        r[0]
        for r in db.query(ScheduledRun.scope_key).filter(
            ScheduledRun.task_key == "marketing.readout", ScheduledRun.period_key == period
        )
    }
    week_start = (local.date() - timedelta(days=local.weekday())).isoformat()
    return [
        Due(
            "marketing.readout",
            str(cid),
            period,
            SCHED_MARKETING_READOUT,
            sid,
            {"startup_id": str(sid), "campaign_id": str(cid), "week_start": week_start},
        )
        for cid, sid in rows
        if str(cid) not in claimed
    ]


def _due_payment_syncs(db: Session, now: datetime) -> list[Due]:
    """One sync per `active` payment connection per local hour (disconnected and
    needs_attention connections are never synced)."""
    local = _local(now)
    period = f"{local.date().isoformat()}T{local.hour:02d}"
    claimed = {
        r[0]
        for r in db.query(ScheduledRun.scope_key).filter(
            ScheduledRun.task_key == "payments.sync", ScheduledRun.period_key == period
        )
    }
    rows = (
        db.query(FinanceConnection.id, FinanceConnection.startup_id)
        .join(Startup, Startup.id == FinanceConnection.startup_id)
        .filter(FinanceConnection.status == "active", Startup.deleted_at.is_(None))
        .all()
    )
    return [
        Due(
            "payments.sync",
            str(cid),
            period,
            SCHED_PAYMENTS_SYNC,
            sid,
            {"connection_id": str(cid), "startup_id": str(sid)},
        )
        for cid, sid in rows
        if str(cid) not in claimed
    ]


def _due_invoice_overdue(db: Session, now: datetime) -> list[Due]:
    """Sent invoices past their due date that still need work today: the one-shot overdue
    event has not fired, or auto-reminders are on, under the cap, and the last one is 7+ days
    old. One claim per invoice per local day; the handler's row-locked re-check is the
    cross-worker guarantee (the claim only stops repeated ticks flooding the queue)."""
    local = _local(now)
    today = local.date()
    period = today.isoformat()
    spacing_cutoff = now - timedelta(days=REMINDER_INTERVAL_DAYS)
    rows = (
        db.query(Invoice.id, Invoice.startup_id)
        .join(Startup, Startup.id == Invoice.startup_id)
        .filter(
            Invoice.status == InvoiceStatus.sent,
            Invoice.due_on.isnot(None),
            Invoice.due_on < today,
            Startup.deleted_at.is_(None),
            or_(
                Invoice.overdue_notified_at.is_(None),
                and_(
                    Invoice.auto_remind.is_(True),
                    Invoice.reminder_count < MAX_AUTO_REMINDERS,
                    or_(
                        Invoice.last_reminded_at.is_(None),
                        Invoice.last_reminded_at <= spacing_cutoff,
                    ),
                ),
            ),
        )
        .order_by(Invoice.due_on, Invoice.id)
        .limit(INVOICE_OVERDUE_BATCH)
        .all()
    )
    if not rows:
        return []
    claimed = {
        r[0]
        for r in db.query(ScheduledRun.scope_key).filter(
            ScheduledRun.task_key == "finance.invoice_overdue",
            ScheduledRun.period_key == period,
            ScheduledRun.scope_key.in_([str(r[0]) for r in rows]),
        )
    }
    return [
        Due(
            "finance.invoice_overdue",
            str(iid),
            period,
            SCHED_INVOICE_OVERDUE,
            sid,
            {"startup_id": str(sid), "invoice_id": str(iid)},
        )
        for iid, sid in rows
        if str(iid) not in claimed
    ]


def _due_trash_purge(db: Session, now: datetime) -> list[Due]:  # noqa: ARG001
    """One purge sweep per local day, for every workspace at once (so no startup scope): the
    30-day Documents trash retention is a day-granular promise."""
    return [
        Due(
            "documents.trash_purge",
            "all",
            _local(now).date().isoformat(),
            SCHED_DOCUMENT_TRASH_PURGE,
            None,
            {},
        )
    ]


def _due_overdue_milestones(db: Session, now: datetime) -> list[Due]:
    today = _local(now).date()
    already_claimed = exists().where(
        ScheduledRun.task_key == "roadmap.overdue",
        ScheduledRun.period_key == "once",
        ScheduledRun.scope_key == cast(RoadmapMilestone.id, String),
    )
    rows = (
        db.query(RoadmapMilestone.id, Roadmap.startup_id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .join(Startup, Roadmap.startup_id == Startup.id)
        .filter(
            RoadmapMilestone.due_on < today,
            RoadmapMilestone.status != RoadmapStatus.done,
            Startup.deleted_at.is_(None),
            ~already_claimed,
        )
        .all()
    )
    return [
        Due(
            "roadmap.overdue",
            str(mid),
            "once",
            SCHED_OVERDUE,
            sid,
            {"startup_id": str(sid), "milestone_id": str(mid)},
        )
        for mid, sid in rows
    ]


def _due_milestones_soon(db: Session, now: datetime) -> list[Due]:
    """PRD 05 "Milestone due soon": once per milestone, when it is due within DUE_SOON_DAYS."""
    today = _local(now).date()
    already_claimed = exists().where(
        ScheduledRun.task_key == "roadmap.due_soon",
        ScheduledRun.period_key == "once",
        ScheduledRun.scope_key == cast(RoadmapMilestone.id, String),
    )
    rows = (
        db.query(RoadmapMilestone.id, Roadmap.startup_id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .join(Startup, Roadmap.startup_id == Startup.id)
        .filter(
            RoadmapMilestone.due_on >= today,
            RoadmapMilestone.due_on <= today + timedelta(days=DUE_SOON_DAYS),
            RoadmapMilestone.status != RoadmapStatus.done,
            Startup.deleted_at.is_(None),
            ~already_claimed,
        )
        .all()
    )
    return [
        Due(
            "roadmap.due_soon",
            str(mid),
            "once",
            SCHED_DUE_SOON,
            sid,
            {"startup_id": str(sid), "milestone_id": str(mid)},
        )
        for mid, sid in rows
    ]


def _due_quarterly(db: Session, now: datetime) -> list[Due]:
    period = _quarter_key(_local(now))
    cutoff = now - timedelta(days=settings.QUARTERLY_REASSESS_DAYS)
    latest = (
        db.query(Assessment.startup_id, func.max(Assessment.completed_at).label("last"))
        .filter(Assessment.status == AssessmentStatus.completed)
        .group_by(Assessment.startup_id)
        .subquery()
    )
    due_ids = [
        r[0]
        for r in db.query(latest.c.startup_id)
        .join(Startup, Startup.id == latest.c.startup_id)
        .filter(latest.c.last <= cutoff, Startup.deleted_at.is_(None))
        .all()
    ]
    in_progress = {
        r[0]
        for r in db.query(Assessment.startup_id)
        .filter(Assessment.status == AssessmentStatus.in_progress)
        .all()
    }
    claimed = {
        r[0]
        for r in db.query(ScheduledRun.scope_key)
        .filter(ScheduledRun.task_key == "assessment.quarterly", ScheduledRun.period_key == period)
        .all()
    }
    return [
        Due(
            "assessment.quarterly", str(sid), period, SCHED_QUARTERLY, sid, {"startup_id": str(sid)}
        )
        for sid in due_ids
        if sid not in in_progress and str(sid) not in claimed
    ]


def _due_sales_outreach(db: Session, now: datetime) -> list[Due]:  # noqa: ARG001
    """One send job and one bounce-poll job per 5-minute bucket, for the ONE shared mailbox (so
    no startup scope). Nothing is enqueued while the mailbox env is unset: an unconfigured
    deployment pays no cost and logs no noise."""
    bucket = str(int(now.timestamp()) // OUTREACH_BUCKET_SECONDS)
    due: list[Due] = []
    if outreach_configured():
        due.append(Due("sales.outreach.send", "mailbox", bucket, SCHED_SALES_SEND, None, {}))
    if imap_configured():
        due.append(Due("sales.outreach.bounces", "mailbox", bucket, SCHED_SALES_BOUNCES, None, {}))
    return due


def scheduler_tick(db: Session, *, now: datetime) -> int:
    """Claim + enqueue every due scheduled task. Returns the count enqueued.

    Each (task, scope) is isolated: a lost claim (already fired this period) is a normal skip;
    any other per-item error is logged and the rest proceed. Commits once at the end.
    """
    due = [
        *_due_missions(db, now),
        *_due_overdue_milestones(db, now),
        *_due_milestones_soon(db, now),
        *_due_quarterly(db, now),
        *_due_kpi_snapshots(db, now),
        *_due_health_nightly(db, now),
        *_due_marketing_posts(db, now),
        *_due_campaign_readouts(db, now),
        *_due_payment_syncs(db, now),
        *_due_sales_stalls(db, now),
        *_due_invoice_overdue(db, now),
        *_due_sales_outreach(db, now),
        *_due_trash_purge(db, now),
        *_due_notification_digests(db, now),
    ]
    enqueued = 0
    for item in due:
        try:
            if _claim(db, item.task_key, item.scope_key, item.period_key):
                job_dispatcher.enqueue(db, item.job_type, item.payload, item.startup_id)
                enqueued += 1
        except Exception as exc:  # noqa: BLE001 - one bad item must not stop the rest
            log.warning(f"[scheduler] {item.task_key}/{item.scope_key} failed: {exc}")
    db.commit()
    return enqueued

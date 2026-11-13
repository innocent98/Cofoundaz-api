"""Exports (slice C): a report or a set of data tables, rendered once from a frozen run into a
private file that expires after 30 days.

Who (spec section 3, Exports column): founder sees every export in the workspace, team member and
accountant see their own; mentor, business consultant and everyone else have no exports at all.
An export outside that set is a 404, the same as one in another workspace.

The security properties, in order:
  1. The file is rendered from the run AS THE REQUESTER MAY READ IT (`runs.view_snapshot` under the
     requester's role at render time), so a locked block never carries a number into the file.
  2. `hubs` records whose numbers are in the file; every download re-checks the downloader's role
     against each of them, so a role change after the export still cuts access.
  3. The file is private storage only: no URL is ever handed out; the download route streams it
     after the per-request check and writes an audit row.
"""

import logging
import re
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Query, Session

from app.core.errors import AppError, Forbidden
from app.db.models.analytics import AnalyticsExport, AnalyticsReport, AnalyticsReportRun
from app.db.models.enums import MembershipRole, MembershipStatus
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.platform.audit import write_audit
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.platform.storage import get_storage
from app.services.analytics import catalog as cat
from app.services.analytics import render as rnd
from app.services.analytics import reports as rep
from app.services.analytics import runs
from app.services.analytics import service as svc
from app.services.analytics.errors import not_found, validation

log = logging.getLogger(__name__)
RENDER_JOB = "analytics.export.render"
RETENTION_DAYS = 30
RUN_RETENTION_DAYS = 90
PURGE_BATCH = 500
MAX_EXPORT_BYTES = 15 * 1024 * 1024
MAX_TABLE_METRICS = 24
LIST_LIMIT_MAX = 100
REPORT_FORMATS = ("pdf", "xlsx")
TABLE_FORMATS = ("xlsx", "csv")
EXTENSIONS = {"pdf": ".pdf", "xlsx": ".xlsx", "csv": ".csv"}
_FILENAME_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')
_NO_ACCESS = "You no longer have access to exports in this workspace."


def _now() -> datetime:
    return datetime.now(UTC)


def is_expired(e: AnalyticsExport, now: datetime | None = None) -> bool:
    return e.purged_at is not None or e.expires_at <= (now or _now())


def safe_filename(name: str, period_from: str, period_to: str, ext: str) -> str:
    stem = unicodedata.normalize("NFKC", name)
    stem = _FILENAME_UNSAFE.sub(" ", stem).strip(" .") or "Report"
    stem = " ".join(stem.split())[:120]
    return f"{stem} {period_from} to {period_to}{ext}"


# ---- access ----
def visible_query(db: Session, m: Membership) -> Query[AnalyticsExport]:
    q = db.query(AnalyticsExport).filter(AnalyticsExport.startup_id == m.startup_id)
    if m.role == MembershipRole.founder:
        return q
    return q.filter(AnalyticsExport.created_by == m.user_id)


def get_visible(db: Session, m: Membership, export_id: uuid.UUID) -> AnalyticsExport:
    row = visible_query(db, m).filter(AnalyticsExport.id == export_id).first()
    if row is None:
        raise not_found("Export not found.")
    return row


# ---- create ----
def _new(
    db: Session,
    m: Membership,
    run: AnalyticsReportRun,
    *,
    artifact: str,
    fmt: str,
    name: str,
    report_id: uuid.UUID | None,
    options: dict[str, Any],
) -> AnalyticsExport:
    row = AnalyticsExport(
        startup_id=m.startup_id,
        run_id=run.id,
        report_id=report_id,
        artifact_type=artifact,
        format=fmt,
        name=name[:160],
        status="pending",
        hubs=[],
        options_json={
            **options,
            "period": {"from": run.period_from.isoformat(), "to": run.period_to.isoformat()},
        },
        created_by=m.user_id,
        expires_at=_now() + timedelta(days=RETENTION_DAYS),
    )
    db.add(row)
    db.flush()
    job_dispatcher.enqueue(
        db, type=RENDER_JOB, payload={"export_id": str(row.id)}, startup_id=m.startup_id
    )
    return row


def export_report(
    db: Session,
    m: Membership,
    report: AnalyticsReport,
    fmt: str,
    run_id: uuid.UUID | None = None,
) -> AnalyticsExport:
    """Export a saved report. With `run_id`, an existing finished run of this report is rendered
    (how an accountant, who cannot start runs, exports a report shared with them); without, a new
    frozen run is made first (founder and team member only)."""
    if fmt not in REPORT_FORMATS:
        raise validation("format", f"Pick one of: {', '.join(REPORT_FORMATS)}.")
    if run_id is not None:
        svc.check_rate(m.startup_id)
        run = runs.get_run(db, report, run_id)
        if run.status != "ready":
            raise AppError("RUN_NOT_READY", "Pick a run that has finished.", 409)
    else:
        if m.role not in cat.REPORT_WRITE_ROLES:
            raise AppError(
                "FORBIDDEN",
                "Only the founder or a team member can run a report. "
                "Export one of its finished runs instead.",
                403,
            )
        run, _ = runs.start_run(db, m, report, "export", enqueue=False)
    return _new(
        db,
        m,
        run,
        artifact="report",
        fmt=fmt,
        name=report.name,
        report_id=report.id,
        options={},
    )


def export_tables(
    db: Session,
    m: Membership,
    metric_keys: list[str],
    rng: dict[str, Any] | None,
    fmt: str,
    include_metadata: bool,
) -> AnalyticsExport:
    """Data tables over catalog metrics: a report-less frozen run, then XLSX (summary + one sheet
    per metric series) or CSV (the summary table). Only metrics the requester can see are allowed;
    a key they cannot see is refused with the same message as a key that does not exist, so the
    route cannot be used to probe a workspace's custom metric names."""
    if fmt not in TABLE_FORMATS:
        raise validation("format", f"Pick one of: {', '.join(TABLE_FORMATS)}.")
    keys = list(dict.fromkeys(k.strip() for k in metric_keys if k and k.strip()))
    if not keys:
        raise validation("metrics", "Pick at least one metric.")
    if len(keys) > MAX_TABLE_METRICS:
        raise validation("metrics", f"Pick at most {MAX_TABLE_METRICS} metrics.")
    metrics = svc.all_metrics(db, m.startup_id)
    for k in keys:
        entry = metrics.get(k)
        if entry is None or not cat.report_can_see(m.role, entry[0].hub):
            raise validation("metrics", "One or more metrics are not available to you.")
    svc.check_rate(m.startup_id)
    norm = rep.normalise_blocks(
        db, m.startup_id, [{"type": "metric", "metric": k, "viz": "line"} for k in keys]
    )
    frozen = rep.normalise_range(rng, "range")
    run = runs.start_table_run(db, m, norm["blocks"], frozen)
    return _new(
        db,
        m,
        run,
        artifact="data_tables",
        fmt=fmt,
        name="Data tables",
        report_id=None,
        options={"metrics": keys, "include_metadata": include_metadata},
    )


# ---- render (worker) ----
def _fail(db: Session, e: AnalyticsExport, message: str) -> None:
    e.status = "failed"
    e.error = message
    db.flush()


def _requester(db: Session, e: AnalyticsExport) -> Membership | None:
    if e.created_by is None:
        return None
    return (
        db.query(Membership)
        .filter(
            Membership.user_id == e.created_by,
            Membership.startup_id == e.startup_id,
            Membership.status == MembershipStatus.active,
        )
        .first()
    )


def render(db: Session, export_id: uuid.UUID) -> None:
    """Idempotent: an export that is no longer pending is left as it is. Any failure marks the
    export failed with a plain message (it never stays pending forever)."""
    e = (
        db.query(AnalyticsExport)
        .filter(AnalyticsExport.id == export_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if e is None or e.status != "pending":
        return
    member = _requester(db, e)
    startup = db.get(Startup, e.startup_id)
    if member is None or startup is None or member.role not in cat.EXPORT_ROLES:
        _fail(db, e, _NO_ACCESS)
        return
    run = runs.execute_run(db, e.run_id) if e.run_id else None
    if run is None:
        _fail(db, e, "The run behind this export no longer exists. Generate it again.")
        return
    if run.status != "ready":
        _fail(db, e, run.error or "The report could not be run.")
        return
    try:
        metrics = svc.all_metrics(db, e.startup_id)
        view = runs.view_snapshot(run.snapshot_json, member.role, metrics) or {}
        ctx = rnd.Context(
            workspace=startup.name or "Workspace",
            period_from=run.period_from.isoformat(),
            period_to=run.period_to.isoformat(),
            as_of=(run.as_of or run.created_at).strftime("%Y-%m-%d %H:%M UTC"),
            generated_by=rep.names(db, {member.user_id}).get(member.user_id, "Member"),
        )
        include_meta = bool(e.options_json.get("include_metadata", e.format != "csv"))
        if e.format == "pdf":
            content = rnd.render_pdf(view, ctx)
        elif e.format == "xlsx":
            content = rnd.render_xlsx(view, ctx, include_metadata=include_meta)
        else:
            content = rnd.render_csv(view, ctx, include_metadata=include_meta)
        hubs = rnd.hubs_in(rnd.rows_from(view))
    except Exception:  # noqa: BLE001 - a broken render must end as `failed`, not pending forever
        log.warning("analytics export %s render failed", e.id, exc_info=True)
        _fail(db, e, "The file could not be generated. Try again.")
        return
    if len(content) > MAX_EXPORT_BYTES:
        _fail(db, e, "The file is too large. Export fewer blocks or a shorter range.")
        return
    ext = EXTENSIONS[e.format]
    key = f"analytics/exports/{e.startup_id}/{e.id}{ext}"
    try:
        get_storage().save_private(key, content, rnd.CONTENT_TYPES[e.format])
    except Exception:  # noqa: BLE001
        log.warning("analytics export %s storage failed", e.id, exc_info=True)
        _fail(db, e, "The file could not be stored. Try again.")
        return
    e.file_key = key
    e.filename = safe_filename(e.name, ctx.period_from, ctx.period_to, ext)
    e.size_bytes = len(content)
    e.hubs = hubs
    e.status = "ready"
    e.ready_at = _now()
    db.flush()
    # PRD `report_exported` (D-ANA-L11): format and artifact ride on the domain event.
    event_bus.publish(
        db,
        "analytics.export.created",
        {
            "startup_id": str(e.startup_id),
            "export_id": str(e.id),
            "report_id": str(e.report_id) if e.report_id else None,
            "export_name": e.name,
            "format": e.format,
            "artifact": e.artifact_type,
            "user_ids": [str(e.created_by)],
        },
    )


# ---- download ----
def open_for_download(
    db: Session, m: Membership, export_id: uuid.UUID
) -> tuple[AnalyticsExport, bytes]:
    """Authorise one download, per request. 404 outside the viewer's set, 410 once expired, 409
    while pending or failed, 403 if the file holds a hub the viewer's role cannot see now."""
    e = get_visible(db, m, export_id)
    if is_expired(e):
        raise AppError("EXPORT_EXPIRED", "This export expired. Generate it again.", 410)
    if e.status == "pending":
        raise AppError("EXPORT_NOT_READY", "This export is still being generated.", 409)
    if e.status != "ready" or not e.file_key:
        raise AppError("EXPORT_FAILED", "This export failed. Generate it again.", 409)
    if any(not cat.report_can_see(m.role, h) for h in e.hubs or []):
        raise Forbidden()
    content = get_storage().read_private(e.file_key)
    if content is None:
        raise AppError("EXPORT_EXPIRED", "This export expired. Generate it again.", 410)
    return e, content


def record_download(db: Session, e: AnalyticsExport, user_id: uuid.UUID, ip: str | None) -> None:
    db.execute(
        update(AnalyticsExport)
        .where(AnalyticsExport.id == e.id)
        .values(download_count=AnalyticsExport.download_count + 1)
    )
    write_audit(
        db,
        "analytics.export.downloaded",
        actor_user_id=user_id,
        startup_id=e.startup_id,
        entity_type="analytics_export",
        entity_id=e.id,
        ip=ip,
        details={"format": e.format, "artifact": e.artifact_type, "size_bytes": e.size_bytes},
    )


# ---- list / serialise ----
def state(e: AnalyticsExport, now: datetime | None = None) -> str:
    if is_expired(e, now):
        return "expired"
    return e.status


def serialize(e: AnalyticsExport, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    st = state(e)
    return {
        "id": str(e.id),
        "name": e.name,
        "artifact_type": e.artifact_type,
        "format": e.format,
        "status": st,
        "report_id": str(e.report_id) if e.report_id else None,
        "run_id": str(e.run_id) if e.run_id else None,
        "period": e.options_json.get("period"),
        "metrics": e.options_json.get("metrics"),
        "filename": e.filename if st == "ready" else None,
        "size_bytes": e.size_bytes if st == "ready" else None,
        "download_count": e.download_count,
        "download_path": (
            f"/api/v1/analytics/exports/{e.id}/download" if st == "ready" else None
        ),
        "error": e.error if st == "failed" else None,
        "created_by": (
            {"id": str(e.created_by), "name": names.get(e.created_by, "Member")}
            if e.created_by
            else None
        ),
        "created_at": e.created_at.isoformat(),
        "ready_at": e.ready_at.isoformat() if e.ready_at else None,
        "expires_at": e.expires_at.isoformat(),
    }


def list_exports(
    db: Session, m: Membership, artifact: str | None, limit: int, offset: int
) -> dict[str, Any]:
    q = visible_query(db, m)
    if artifact:
        q = q.filter(AnalyticsExport.artifact_type == artifact)
    total = q.count()
    rows = (
        q.order_by(AnalyticsExport.created_at.desc(), AnalyticsExport.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    names = rep.names(db, {r.created_by for r in rows if r.created_by})
    return {
        "exports": [serialize(r, names) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "retention_days": RETENTION_DAYS,
    }


def detail(db: Session, e: AnalyticsExport) -> dict[str, Any]:
    names = rep.names(db, {e.created_by} if e.created_by else set())
    return serialize(e, names)


# ---- purge (daily) ----
def purge(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Delete expired export files (row kept, `purged_at` set, so history says "Expired") and
    runs older than 90 days. A storage error leaves that row for tomorrow's sweep."""
    now = now or _now()
    rows = (
        db.query(AnalyticsExport)
        .filter(AnalyticsExport.expires_at <= now, AnalyticsExport.purged_at.is_(None))
        .order_by(AnalyticsExport.expires_at.asc())
        .limit(PURGE_BATCH)
        .all()
    )
    storage = get_storage()
    files = 0
    for e in rows:
        if e.file_key:
            try:
                storage.delete_private(e.file_key)
            except Exception:  # noqa: BLE001 - one bad file must not stop the sweep
                log.warning("analytics export %s purge failed", e.id, exc_info=True)
                continue
        e.purged_at = now
        files += 1
    old = (
        db.query(AnalyticsReportRun.id)
        .filter(AnalyticsReportRun.created_at < now - timedelta(days=RUN_RETENTION_DAYS))
        .limit(PURGE_BATCH)
        .all()
    )
    ids = [r[0] for r in old]
    if ids:
        db.query(AnalyticsReportRun).filter(AnalyticsReportRun.id.in_(ids)).delete(
            synchronize_session=False
        )
    db.flush()
    return {"exports_purged": files, "runs_deleted": len(ids)}

"""Previews and frozen runs (slice B).

A run resolves every block once, under the requester's access, and stores the answer. After that
it is never recomputed or edited: editing or deleting the report, or changing the underlying data,
does not touch it. A viewer reading a run sees it through their OWN access (`view_snapshot`), so a
run the Founder made never shows Finance numbers to a mentor it was shared with.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.analytics import AnalyticsReport, AnalyticsReportRun
from app.db.models.enums import MembershipRole, MembershipStatus
from app.db.models.job import Job
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.analytics import catalog as cat
from app.services.analytics import periods as per
from app.services.analytics import reports as rep
from app.services.analytics import service as svc
from app.services.analytics.errors import not_found

log = logging.getLogger(__name__)
RUN_JOB = "analytics.report.run"
REUSE_PENDING_MINUTES = 10
RUN_LIST_LIMIT = 20
UNKNOWN = {"status": "unknown", "message": "Metric no longer available."}


def _period(rng: dict[str, Any]) -> per.Period:
    return per.resolve(
        rng.get("range") or "30d",
        rep._as_date(rng.get("from")),
        rep._as_date(rng.get("to")),
    )


def freeze_range(default_range: dict[str, Any]) -> dict[str, Any]:
    """Turn a preset ("30d") into explicit dates so the run says exactly which days it covers."""
    p = _period(default_range)
    return {
        "range": "custom",
        "from": p.start.isoformat(),
        "to": p.end.isoformat(),
        "compare": default_range.get("compare") or "previous",
    }


def build_results(
    db: Session,
    startup: Startup,
    role: MembershipRole,
    blocks: list[dict[str, Any]],
    rng: dict[str, Any],
) -> list[dict[str, Any]]:
    """Resolve each block for `role`. A locked block gets a stub, an unknown key an honest
    "no longer available"; one failing metric never fails the whole report."""
    base = _period(rng)
    out: list[dict[str, Any]] = []
    for b in blocks:
        if b["type"] == "text":
            out.append(
                {"block_id": b["id"], "type": "text", "title": b.get("title"), "text": b["text"]}
            )
            continue
        p = _period(b["range"]) if b.get("range") else base
        compare = b.get("compare") or rng.get("compare") or "previous"
        base_row = {
            "block_id": b["id"],
            "type": "metric",
            "hub": b.get("hub"),
            "viz": b["viz"],
            "title": b.get("title"),
        }
        try:
            res = svc.resolve(
                db,
                startup,
                role,
                b["metric"],
                p,
                compare,
                b["viz"] != "number",
                visible=cat.report_can_see,
            )
        except Exception:  # noqa: BLE001 - one broken source must not sink the report
            log.warning("analytics block %s failed", b["id"], exc_info=True)
            res = {
                "key": b["metric"],
                "status": "error",
                "message": "Could not compute this metric.",
            }
        if res.get("status") == "unknown":
            res = dict(UNKNOWN)
        out.append({**base_row, "result": res})
    return out


# ---- stateless preview ----
def preview(
    db: Session,
    m: Membership,
    blocks: list[dict[str, Any]],
    default_range: dict[str, Any] | None,
) -> dict[str, Any]:
    svc.check_rate(m.startup_id)
    startup = db.get(Startup, m.startup_id)
    if startup is None:
        raise not_found()
    norm = rep.normalise_blocks(db, m.startup_id, blocks)
    rng = rep.normalise_range(default_range)
    results = build_results(db, startup, m.role, norm["blocks"], rng)
    return {
        "period": _period(rng).iso(),
        "as_of": datetime.now(UTC).isoformat(),
        "results": results,
    }


# ---- runs ----
def start_run(
    db: Session, m: Membership, report: AnalyticsReport, triggered_by: str = "manual"
) -> tuple[AnalyticsReportRun, str | None]:
    svc.check_rate(m.startup_id)
    cutoff = datetime.now(UTC) - timedelta(minutes=REUSE_PENDING_MINUTES)
    existing = (
        db.query(AnalyticsReportRun)
        .filter(
            AnalyticsReportRun.report_id == report.id,
            AnalyticsReportRun.run_by == m.user_id,
            AnalyticsReportRun.status == "pending",
            AnalyticsReportRun.created_at >= cutoff,
        )
        .first()
    )
    if existing is not None:  # a double click is one run
        job = (
            db.query(Job)
            .filter(Job.type == RUN_JOB, Job.payload["run_id"].astext == str(existing.id))
            .first()
        )
        return existing, str(job.id) if job else None
    rng = freeze_range(report.default_range_json)
    p = _period(rng)
    run = AnalyticsReportRun(
        report_id=report.id,
        startup_id=m.startup_id,
        period_from=p.start,
        period_to=p.end,
        tz="UTC",
        status="pending",
        snapshot_json={
            "report": {
                "name": report.name,
                "description": report.description,
                "range": rng,
                "blocks": report.blocks_json["blocks"],
            }
        },
        access_json={"role": m.role.value},
        triggered_by=triggered_by,
        run_by=m.user_id,
    )
    db.add(run)
    db.flush()
    job = job_dispatcher.enqueue(
        db, type=RUN_JOB, payload={"run_id": str(run.id)}, startup_id=m.startup_id
    )
    return run, str(job.id)


def _fail(db: Session, run: AnalyticsReportRun, message: str) -> None:
    run.status = "failed"
    run.error = message
    run.as_of = datetime.now(UTC)
    db.flush()


def execute(db: Session, job: Job) -> None:
    """Worker side. Idempotent: a run that is no longer pending is left exactly as it is."""
    run = db.get(AnalyticsReportRun, uuid.UUID(str(job.payload["run_id"])))
    if run is None or run.status != "pending":
        return
    member = (
        db.query(Membership)
        .filter(
            Membership.user_id == run.run_by,
            Membership.startup_id == run.startup_id,
            Membership.status == MembershipStatus.active,
        )
        .first()
    )
    startup = db.get(Startup, run.startup_id)
    if member is None or startup is None or member.role not in cat.REPORT_ROLES:
        return _fail(db, run, "You no longer have access to analytics in this workspace.")
    snap = dict(run.snapshot_json or {})
    definition = snap["report"]
    results = build_results(db, startup, member.role, definition["blocks"], definition["range"])
    metrics = svc.all_metrics(db, run.startup_id)
    seen = [r["result"].get("key") for r in results if r["type"] == "metric"]
    visible = [
        k for k in seen if k in metrics and cat.report_can_see(member.role, metrics[k][0].hub)
    ]
    run.snapshot_json = {**snap, "results": results}
    run.access_json = {
        "role": member.role.value,
        "visible_keys": sorted(set(visible)),
        "locked_keys": sorted(
            {k for k in (b.get("metric") for b in definition["blocks"]) if k in metrics}
            - set(visible)
        ),
    }
    run.status = "ready"
    run.as_of = datetime.now(UTC)
    db.flush()
    event_bus.publish(
        db,
        "analytics.report.generated",
        {
            "startup_id": str(run.startup_id),
            "report_id": str(run.report_id) if run.report_id else None,
            "run_id": str(run.id),
            "report_name": definition["name"],
            "user_ids": [str(run.run_by)],
        },
    )


def get_run(db: Session, report: AnalyticsReport, run_id: uuid.UUID) -> AnalyticsReportRun:
    run = (
        db.query(AnalyticsReportRun)
        .filter(AnalyticsReportRun.id == run_id, AnalyticsReportRun.report_id == report.id)
        .first()
    )
    if run is None:
        raise not_found("Run not found.")
    return run


def list_runs(db: Session, report: AnalyticsReport) -> list[AnalyticsReportRun]:
    return (
        db.query(AnalyticsReportRun)
        .filter(AnalyticsReportRun.report_id == report.id)
        .order_by(AnalyticsReportRun.created_at.desc())
        .limit(RUN_LIST_LIMIT)
        .all()
    )


def view_snapshot(
    snap: dict[str, Any] | None, role: MembershipRole, metrics: dict[str, Any]
) -> dict[str, Any] | None:
    """The frozen snapshot as `role` may read it. The stored copy is never altered: locked blocks
    are replaced on the way out, so what was computed for the Founder cannot reach a mentor."""
    if snap is None:
        return None
    out: dict[str, Any] = {"report": {**snap["report"]}}
    out["report"]["blocks"] = rep.view_blocks(snap["report"]["blocks"], role, metrics)
    if "results" in snap:
        results = []
        for r in snap["results"]:
            if r["type"] != "metric":
                results.append(r)
                continue
            entry = metrics.get(str(r.get("result", {}).get("key")))
            locked = rep.hidden_hub(role, r.get("hub"), entry[0].hub if entry else None)
            results.append(rep.locked_stub(r["block_id"], locked) if locked else r)
        out["results"] = results
    return out


def serialize_run(
    run: AnalyticsReportRun,
    role: MembershipRole,
    metrics: dict[str, Any],
    names: dict[uuid.UUID, str],
    *,
    with_snapshot: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(run.id),
        "report_id": str(run.report_id) if run.report_id else None,
        "status": run.status,
        "triggered_by": run.triggered_by,
        "period": {"from": run.period_from.isoformat(), "to": run.period_to.isoformat()},
        "tz": run.tz,
        "run_by": (
            {"id": str(run.run_by), "name": names.get(run.run_by, "Member")} if run.run_by else None
        ),
        "as_of": run.as_of.isoformat() if run.as_of else None,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
    }
    if with_snapshot:
        out["snapshot"] = view_snapshot(run.snapshot_json, role, metrics)
    return out

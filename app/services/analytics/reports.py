"""Saved reports (slice B): definitions made of blocks over the metric catalog, who may see and
edit them, and the Founder's shares.

Authorisation is layered and every layer fails closed:
  1. Visibility (is the report yours to know about?): founder sees all, a team member sees their
     own plus what was shared with them, mentor / business consultant / accountant see only what
     was shared. A report outside that set is a 404, the same as one in another workspace.
  2. Edit rights: founder any report, team member their own. Visible but read-only is a 403.
  3. Per-metric authorisation (`catalog.report_can_see`): a block over a hub the viewer cannot open
     is redacted to `{locked, needs}` in every read of the definition, and its number is never
     computed for them (see runs.py).
"""

import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Query, Session

from app.core.errors import AppError
from app.db.models.analytics import AnalyticsReport, AnalyticsReportRun, AnalyticsReportShare
from app.db.models.enums import MembershipRole, MembershipStatus
from app.db.models.membership import Membership
from app.db.models.user import UserProfile
from app.services.analytics import catalog as cat
from app.services.analytics import periods as per
from app.services.analytics import service as svc
from app.services.analytics.errors import not_found, validation

MAX_BLOCKS = 30
MAX_REPORTS = 100
MAX_SHARES = 20
BLOCK_VERSION = 1
VIZ = ("number", "line", "bar")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_HTML_RE = re.compile(r"<[A-Za-z/!?]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ---- text hygiene (spec section 10, item 4: no raw HTML accepted anywhere) ----
def clean(field: str, value: str | None, max_len: int, *, required: bool = False) -> str | None:
    text = _CTRL_RE.sub("", value or "").strip()
    if not text:
        if required:
            raise validation(field, "This is required.")
        return None
    if len(text) > max_len:
        raise validation(field, f"Use at most {max_len} characters.")
    if _HTML_RE.search(text):
        raise validation(field, "Plain text only. Remove the HTML tags.")
    return text


# ---- definition ----
def normalise_range(raw: dict[str, Any] | None, field: str = "default_range") -> dict[str, Any]:
    raw = raw or {}
    preset = str(raw.get("range") or "30d")
    from_, to = raw.get("from"), raw.get("to")
    period = per.resolve(preset, _as_date(from_), _as_date(to))
    compare = str(raw.get("compare") or "previous")
    per.comparison(period, compare)  # validates the mode
    custom = preset == "custom"
    return {
        "range": preset,
        "from": period.start.isoformat() if custom else None,
        "to": period.end.isoformat() if custom else None,
        "compare": compare,
    }


def _as_date(v: Any) -> date | None:
    if v is None or isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError as e:
        raise validation("range", "Use a date like 2026-10-31.") from e


def normalise_blocks(
    db: Session, startup_id: uuid.UUID, raw: list[dict[str, Any]]
) -> dict[str, Any]:
    if len(raw) > MAX_BLOCKS:
        raise validation("blocks", f"A report can hold at most {MAX_BLOCKS} blocks.")
    metrics = svc.all_metrics(db, startup_id)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for i, b in enumerate(raw):
        f = f"blocks[{i}]"
        kind = b.get("type")
        block_id = b.get("id") or uuid.uuid4().hex[:8]
        if not _ID_RE.match(str(block_id)):
            raise validation(f"{f}.id", "Use letters, numbers, dashes and underscores.")
        if block_id in seen:
            raise validation(f"{f}.id", "Block ids must be unique within a report.")
        seen.add(block_id)
        title = clean(f"{f}.title", b.get("title"), 80)
        if kind == "text":
            text = clean(f"{f}.text", b.get("text"), 2000, required=True)
            out.append({"id": block_id, "type": "text", "title": title, "text": text})
            continue
        key = str(b.get("metric") or "")
        if key not in metrics:
            raise validation(f"{f}.metric", "That metric does not exist.")
        viz = b.get("viz") or "number"
        if viz not in VIZ:
            raise validation(f"{f}.viz", f"Pick one of: {', '.join(VIZ)}.")
        block_range = None
        if b.get("range") or b.get("from") or b.get("to"):
            block_range = normalise_range(
                {"range": b.get("range") or "custom", "from": b.get("from"), "to": b.get("to")},
                f"{f}.range",
            )
            block_range.pop("compare")
        compare = b.get("compare")
        if compare is not None:
            per.comparison(per.resolve("30d", None, None), compare)
        out.append(
            {
                "id": block_id,
                "type": "metric",
                "metric": key,
                "hub": metrics[key][0].hub,  # stored so redaction never depends on the live catalog
                "viz": viz,
                "title": title,
                "range": block_range,
                "compare": compare,
            }
        )
    return {"version": BLOCK_VERSION, "blocks": out}


def hidden_hub(role: MembershipRole, stored_hub: str | None, live_hub: str | None) -> str | None:
    """The hub to lock a block behind, or None when the viewer may read it. Fails closed: when the
    hub is unknown (a custom metric deleted since) only founder and team member keep the block."""
    hub = stored_hub or live_hub
    if hub is None:
        return None if role in cat.REPORT_WRITE_ROLES else "custom"
    return None if cat.report_can_see(role, hub) else hub


def view_blocks(
    blocks: list[dict[str, Any]], role: MembershipRole, metrics: dict[str, Any]
) -> list[dict[str, Any]]:
    """The definition as `role` may read it: a block over a locked hub carries no metric, title
    or range, only what the FE needs to draw a lock."""
    out = []
    for b in blocks:
        if b.get("type") != "metric":
            out.append(b)
            continue
        entry = metrics.get(str(b.get("metric")))
        locked = hidden_hub(role, b.get("hub"), entry[0].hub if entry else None)
        out.append(locked_stub(b["id"], locked) if locked else b)
    return out


def locked_stub(block_id: str, hub: str) -> dict[str, Any]:
    return {
        "id": block_id,
        "type": "metric",
        "locked": True,
        "needs": hub,
        "needs_label": cat.HUB_LABELS[hub],
    }


# ---- templates ----
def templates_for(role: MembershipRole, metrics: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for t in cat.TEMPLATES:
        blocks, omitted = [], set()
        for key, viz in t["metrics"]:  # type: ignore[attr-defined]
            hub = metrics[key][0].hub
            if cat.report_can_see(role, hub):
                blocks.append({"type": "metric", "metric": key, "viz": viz})
            else:
                omitted.add(hub)
        out.append(
            {
                "key": t["key"],
                "name": t["name"],
                "description": t["description"],
                "default_range": {
                    "range": t["range"],
                    "from": None,
                    "to": None,
                    "compare": t["compare"],
                },
                "blocks": blocks,
                "omitted_hubs": sorted(omitted),
            }
        )
    return out


# ---- access ----
def visible_query(db: Session, m: Membership) -> Query[AnalyticsReport]:
    q = db.query(AnalyticsReport).filter(
        AnalyticsReport.startup_id == m.startup_id, AnalyticsReport.deleted_at.is_(None)
    )
    if m.role == MembershipRole.founder:
        return q
    shared = select(AnalyticsReportShare.report_id).where(AnalyticsReportShare.user_id == m.user_id)
    if m.role == MembershipRole.team_member:
        return q.filter(or_(AnalyticsReport.owner_id == m.user_id, AnalyticsReport.id.in_(shared)))
    return q.filter(AnalyticsReport.id.in_(shared))


def get_visible(db: Session, m: Membership, report_id: uuid.UUID) -> AnalyticsReport:
    row = visible_query(db, m).filter(AnalyticsReport.id == report_id).first()
    if row is None:
        raise not_found("Report not found.")
    return row


def can_edit(m: Membership, r: AnalyticsReport) -> bool:
    return m.role == MembershipRole.founder or (
        m.role == MembershipRole.team_member and r.owner_id == m.user_id
    )


def require_edit(m: Membership, r: AnalyticsReport) -> None:
    if not can_edit(m, r):
        from app.core.errors import Forbidden

        raise Forbidden()


def names(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = db.query(UserProfile.user_id, UserProfile.full_name).filter(UserProfile.user_id.in_(ids))
    return {uid: (n or "Member") for uid, n in rows}


# ---- serialisation ----
def run_summary(run: AnalyticsReportRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": str(run.id),
        "status": run.status,
        "as_of": run.as_of.isoformat() if run.as_of else None,
        "error": run.error if run.status == "failed" else None,
        "created_at": run.created_at.isoformat(),
    }


def latest_runs(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, AnalyticsReportRun]:
    if not ids:
        return {}
    rows = (
        db.query(AnalyticsReportRun)
        .filter(AnalyticsReportRun.report_id.in_(ids))
        .order_by(AnalyticsReportRun.created_at.desc())
        .all()
    )
    out: dict[uuid.UUID, AnalyticsReportRun] = {}
    for r in rows:
        if r.report_id is not None:
            out.setdefault(r.report_id, r)
    return out


def serialize(
    db: Session,
    m: Membership,
    r: AnalyticsReport,
    metrics: dict[str, Any],
    *,
    owners: dict[uuid.UUID, str],
    last: AnalyticsReportRun | None,
    shares: int | None = None,
    detail: bool = True,
) -> dict[str, Any]:
    blocks = view_blocks(r.blocks_json["blocks"], m.role, metrics)
    out: dict[str, Any] = {
        "id": str(r.id),
        "name": r.name,
        "description": r.description,
        "owner": (
            {"id": str(r.owner_id), "name": owners.get(r.owner_id, "Member")}
            if r.owner_id
            else None
        ),
        "template_key": r.template_key,
        "default_range": r.default_range_json,
        "block_count": len(blocks),
        "version": r.version,
        "can_edit": can_edit(m, r),
        "can_share": m.role == MembershipRole.founder,
        "is_owner": r.owner_id == m.user_id,
        "shared_with_me": r.owner_id != m.user_id and m.role != MembershipRole.founder,
        "last_run": run_summary(last),
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
    }
    if shares is not None:
        out["share_count"] = shares
    if detail:
        out["blocks"] = blocks
    return out


def share_counts(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    rows = (
        db.query(AnalyticsReportShare.report_id, func.count())
        .filter(AnalyticsReportShare.report_id.in_(ids))
        .group_by(AnalyticsReportShare.report_id)
        .all()
    )
    return {rid: int(n) for rid, n in rows}


def list_reports(
    db: Session, m: Membership, q: str | None, sort: str, limit: int, offset: int
) -> dict[str, Any]:
    query = visible_query(db, m)
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.filter(AnalyticsReport.name.ilike(like, escape="\\"))
    total = query.count()
    order: Any = {
        "name": AnalyticsReport.name.asc(),
        "created": AnalyticsReport.created_at.desc(),
    }.get(sort, AnalyticsReport.updated_at.desc())
    rows = query.order_by(order, AnalyticsReport.id).offset(offset).limit(limit).all()
    ids = [r.id for r in rows]
    metrics = svc.all_metrics(db, m.startup_id)
    owners = names(db, {r.owner_id for r in rows if r.owner_id})
    last = latest_runs(db, ids)
    counts = share_counts(db, ids) if m.role == MembershipRole.founder else {}
    return {
        "reports": [
            serialize(
                db,
                m,
                r,
                metrics,
                owners=owners,
                last=last.get(r.id),
                shares=counts.get(r.id, 0) if m.role == MembershipRole.founder else None,
                detail=False,
            )
            for r in rows
        ],
        "total": total,
    }


def detail(db: Session, m: Membership, r: AnalyticsReport) -> dict[str, Any]:
    metrics = svc.all_metrics(db, m.startup_id)
    owners = names(db, {r.owner_id} if r.owner_id else set())
    last = latest_runs(db, [r.id]).get(r.id)
    shares = share_counts(db, [r.id]).get(r.id, 0) if m.role == MembershipRole.founder else None
    return serialize(db, m, r, metrics, owners=owners, last=last, shares=shares)


# ---- writes ----
def _count(db: Session, startup_id: uuid.UUID) -> int:
    return (
        db.query(AnalyticsReport)
        .filter(AnalyticsReport.startup_id == startup_id, AnalyticsReport.deleted_at.is_(None))
        .count()
    )


def create(
    db: Session,
    m: Membership,
    *,
    name: str | None,
    description: str | None,
    blocks: list[dict[str, Any]] | None,
    default_range: dict[str, Any] | None,
    template_key: str | None,
) -> AnalyticsReport:
    if _count(db, m.startup_id) >= MAX_REPORTS:
        raise validation("name", f"A workspace can hold at most {MAX_REPORTS} reports.")
    metrics = svc.all_metrics(db, m.startup_id)
    if template_key:
        if blocks:
            raise validation("template_key", "Send a template or your own blocks, not both.")
        tpl = next((t for t in templates_for(m.role, metrics) if t["key"] == template_key), None)
        if tpl is None:
            raise validation("template_key", "That template does not exist.")
        blocks = list(tpl["blocks"])
        default_range = default_range or tpl["default_range"]
        name = name or tpl["name"]
        description = description if description is not None else tpl["description"]
    clean_name = clean("name", name, 120, required=True)
    row = AnalyticsReport(
        startup_id=m.startup_id,
        name=clean_name or "",
        description=clean("description", description, 500),
        blocks_json=normalise_blocks(db, m.startup_id, blocks or []),
        default_range_json=normalise_range(default_range),
        owner_id=m.user_id,
        template_key=template_key,
        version=1,
    )
    db.add(row)
    db.flush()
    return row


def conflict() -> AppError:
    return AppError(
        "VERSION_CONFLICT",
        "Someone else changed this report. Reload it and apply your change again.",
        409,
    )


def update(
    db: Session, m: Membership, r: AnalyticsReport, version: int, changes: dict[str, Any]
) -> AnalyticsReport:
    """Compare-and-swap on `version`: the UPDATE only matches the version the editor loaded, so
    two overlapping saves can never both win (the loser gets a 409, never a silent overwrite)."""
    values: dict[Any, Any] = {}
    if "name" in changes:
        values["name"] = clean("name", changes["name"], 120, required=True)
    if "description" in changes:
        values["description"] = clean("description", changes["description"], 500)
    if "blocks" in changes:
        values["blocks_json"] = normalise_blocks(db, m.startup_id, changes["blocks"] or [])
    if "default_range" in changes:
        values["default_range_json"] = normalise_range(changes["default_range"])
    values["version"] = version + 1
    values["updated_at"] = datetime.now(UTC)
    n = (
        db.query(AnalyticsReport)
        .filter(
            AnalyticsReport.id == r.id,
            AnalyticsReport.version == version,
            AnalyticsReport.deleted_at.is_(None),
        )
        .update(values, synchronize_session=False)
    )
    if n == 0:
        raise conflict()
    db.expire(r)
    return r


def soft_delete(db: Session, r: AnalyticsReport) -> None:
    r.deleted_at = datetime.now(UTC)
    db.flush()


def duplicate(db: Session, m: Membership, src: AnalyticsReport) -> tuple[AnalyticsReport, int]:
    if _count(db, m.startup_id) >= MAX_REPORTS:
        raise validation("name", f"A workspace can hold at most {MAX_REPORTS} reports.")
    metrics = svc.all_metrics(db, m.startup_id)
    kept, dropped = [], 0
    for b in src.blocks_json["blocks"]:
        entry = metrics.get(b.get("metric")) if b.get("type") == "metric" else None
        if entry is not None and not cat.report_can_see(m.role, entry[0].hub):
            dropped += 1
            continue
        kept.append({**b, "id": uuid.uuid4().hex[:8]})
    row = AnalyticsReport(
        startup_id=m.startup_id,
        name=f"Copy of {src.name}"[:120],
        description=src.description,
        blocks_json={"version": BLOCK_VERSION, "blocks": kept},
        default_range_json=src.default_range_json,
        owner_id=m.user_id,
        template_key=src.template_key,
        version=1,
    )
    db.add(row)
    db.flush()
    return row, dropped


# ---- shares ----
def _members(db: Session, startup_id: uuid.UUID) -> list[Membership]:
    return (
        db.query(Membership)
        .filter(Membership.startup_id == startup_id, Membership.status == MembershipStatus.active)
        .all()
    )


def locked_hubs_for(role: MembershipRole, r: AnalyticsReport, metrics: dict[str, Any]) -> list[str]:
    hubs = {
        metrics[b["metric"]][0].hub
        for b in r.blocks_json["blocks"]
        if b.get("type") == "metric" and b.get("metric") in metrics
    }
    return sorted(h for h in hubs if not cat.report_can_see(role, h))


def shares_view(db: Session, r: AnalyticsReport) -> dict[str, Any]:
    metrics = svc.all_metrics(db, r.startup_id)
    members = _members(db, r.startup_id)
    granted = {
        s.user_id: s
        for s in db.query(AnalyticsReportShare).filter(AnalyticsReportShare.report_id == r.id)
    }
    nm = names(db, {x.user_id for x in members})
    shares: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for x in members:
        if x.role not in cat.SHARE_TARGET_ROLES or x.user_id == r.owner_id:
            continue
        row = {
            "user_id": str(x.user_id),
            "name": nm.get(x.user_id, "Member"),
            "role": x.role.value,
            "locked_hubs": locked_hubs_for(x.role, r, metrics),
        }
        (shares if x.user_id in granted else candidates).append(row)
    return {"shares": shares, "candidates": candidates}


def set_shares(
    db: Session, m: Membership, r: AnalyticsReport, user_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """Replace the share list; returns the newly added users (they are notified)."""
    wanted = list(dict.fromkeys(user_ids))
    if len(wanted) > MAX_SHARES:
        raise validation("user_ids", f"Share with at most {MAX_SHARES} people.")
    eligible = {
        x.user_id
        for x in _members(db, r.startup_id)
        if x.role in cat.SHARE_TARGET_ROLES and x.user_id != r.owner_id
    }
    if any(u not in eligible for u in wanted):
        raise validation(
            "user_ids", "Pick active members of this workspace who can be given access."
        )
    current = {
        s.user_id: s
        for s in db.query(AnalyticsReportShare).filter(AnalyticsReportShare.report_id == r.id)
    }
    for uid, s in current.items():
        if uid not in wanted:
            db.delete(s)
    added = [u for u in wanted if u not in current]
    for u in added:
        db.add(AnalyticsReportShare(report_id=r.id, user_id=u, created_by=m.user_id))
    db.flush()
    return added

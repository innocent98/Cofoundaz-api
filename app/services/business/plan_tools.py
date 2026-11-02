"""Business Builder slice (c) helpers: plan readiness (PRD 08.11), revenue projection and
"Send to financial model" (08.8 / 12.6), Value Proposition fit-lines (08.5)."""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.business import BusinessCanvas, BusinessRecord
from app.db.models.enums import CanvasType, RecordKind
from app.db.models.finance_runway import FinanceRunwaySettings
from app.db.models.startup import Startup
from app.services.business.canvas_defs import empty_blocks
from app.services.business.export import CANVAS_TITLES, RECORD_TITLES
from app.services.business.records import list_records
from app.services.business.service import completion

# --- readiness ----------------------------------------------------------------------------------


def readiness(db: Session, startup: Startup) -> dict[str, Any]:
    """Every artifact the plan draws on: ready (complete / has records), partial, or missing."""
    canvases = {c.type: c for c in db.query(BusinessCanvas).filter_by(startup_id=startup.id)}
    counts: dict[RecordKind, int] = dict(
        db.query(BusinessRecord.kind, func.count(BusinessRecord.id))
        .filter_by(startup_id=startup.id)
        .group_by(BusinessRecord.kind)
        .all()  # type: ignore[arg-type]
    )
    items: list[dict[str, Any]] = []
    for ctype in CanvasType:
        blocks = canvases[ctype].blocks if ctype in canvases else empty_blocks(ctype)
        comp = completion(ctype, blocks)
        status = {"complete": "ready", "continue": "partial"}.get(comp["status"], "missing")
        items.append(
            {
                "type": ctype.value,
                "label": CANVAS_TITLES[ctype],
                "status": status,
                "completion_pct": comp["completion_pct"],
            }
        )
    for kind in RecordKind:
        n = counts.get(kind, 0)
        items.append(
            {
                "type": kind.value,
                "label": RECORD_TITLES[kind],
                "status": "ready" if n else "missing",
                "completion_pct": 100 if n else 0,
            }
        )
    not_ready = [i["type"] for i in items if i["status"] != "ready"]
    return {
        "items": items,
        "ready_count": len(items) - len(not_ready),
        "total": len(items),
        "missing": not_ready,
    }


# --- revenue model ------------------------------------------------------------------------------


def _add_months(d: date, n: int) -> str:
    y, m = divmod(d.month - 1 + n, 12)
    return f"{d.year + y:04d}-{m + 1:02d}"


def _stream_rows(db: Session, startup: Startup) -> list[dict[str, Any]]:
    return [
        {
            "id": str(r.id),
            "name": str(r.data.get("name") or "Stream"),
            "pricing_basis": str(r.data.get("pricing_basis") or ""),
            "est_monthly": float(r.data.get("est_monthly") or 0),
        }
        for r in list_records(db, startup, RecordKind.revenue_stream)
    ]


def default_growth_pct(db: Session, startup: Startup) -> float:
    row = db.query(FinanceRunwaySettings).filter_by(startup_id=startup.id).first()
    return float(row.mom_growth_percent) if row else 0.0


def projection(db: Session, startup: Startup, months: int, growth_pct: float) -> dict[str, Any]:
    """Each stream compounded monthly at `growth_pct` from its est. monthly (month 1 = today's
    estimate). Values are in the streams' own (major) units, rounded to 2 dp."""
    g = growth_pct / 100
    streams = []
    for s in _stream_rows(db, startup):
        values = [round(s["est_monthly"] * (1 + g) ** i, 2) for i in range(months)]
        streams.append({"id": s["id"], "name": s["name"], "values": values})
    total = [round(sum(st["values"][i] for st in streams), 2) for i in range(months)]
    start = datetime.now(UTC).date()
    return {
        "growth_pct": growth_pct,
        "months": [_add_months(start, k) for k in range(1, months + 1)],
        "streams": streams,
        "total": total,
    }


def send_to_finance(db: Session, startup: Startup, user_id: uuid.UUID) -> dict[str, Any]:
    """Store the streams as the Finance model's revenue drivers (PRD 12.6). Overwrites the
    previous send; the 3-statement model uses them while there are no revenue actuals."""
    rows = _stream_rows(db, startup)
    if not rows:
        raise AppError(
            "NO_REVENUE_STREAMS",
            "Add at least one revenue stream before sending it to Finance.",
            422,
        )
    streams = [
        {
            "name": r["name"],
            "pricing_basis": r["pricing_basis"],
            "est_monthly": r["est_monthly"],
            "est_monthly_minor": round(r["est_monthly"] * 100),
        }
        for r in rows
    ]
    drivers = {
        "streams": streams,
        "total_monthly_minor": sum(s["est_monthly_minor"] for s in streams),
        "sent_at": datetime.now(UTC).isoformat(),
        "sent_by": str(user_id),
    }
    row = db.query(FinanceRunwaySettings).filter_by(startup_id=startup.id).first()
    if row is None:
        row = FinanceRunwaySettings(startup_id=startup.id)
        db.add(row)
    row.revenue_drivers = drivers
    db.flush()
    return drivers


# --- value proposition fit-lines ---------------------------------------------------------------

FIT_PAIRS = {"pain_relievers": "pains", "gain_creators": "gains"}


def _link_ok(blocks: dict[str, Any], link: dict[str, str]) -> bool:
    return (
        FIT_PAIRS.get(link["from_block"]) == link["to_block"]
        and link["from_item"] in (blocks.get(link["from_block"]) or [])
        and link["to_item"] in (blocks.get(link["to_block"]) or [])
    )


def live_fit_links(canvas: BusinessCanvas) -> list[dict[str, str]]:
    """Stored links whose items still exist (deleted or renamed items drop their lines)."""
    stored = (canvas.meta or {}).get("fit_links") or []
    return [link for link in stored if _link_ok(canvas.blocks or {}, link)]


def set_fit_links(
    db: Session, canvas: BusinessCanvas, links: list[dict[str, str]], user_id: uuid.UUID
) -> None:
    for i, link in enumerate(links):
        if not _link_ok(canvas.blocks or {}, link):
            raise AppError(
                "VALIDATION_ERROR",
                "A fit-line must join a pain reliever to a pain, or a gain creator to a gain, "
                "using items on the canvas.",
                422,
                field_errors=[{"field": f"links.{i}", "message": "Not a valid fit-line."}],
            )
    unique = list({tuple(sorted(link.items())): link for link in links}.values())
    canvas.meta = {**(canvas.meta or {}), "fit_links": unique}
    canvas.updated_by_id = user_id
    db.flush()

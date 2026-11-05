"""Read-only workspace context for the agents (gap row 2).

Each section is a small function returning `(label, text)` or None when the workspace has nothing
to say. Rules that shape this module:
  * Read-only, scoped to one workspace; a section is only read when the asker's role may read the
    matching module (the same role sets the module routes use).
  * Journal content never goes in. Neither does anything else private to another member.
  * User-authored text (task titles, canvas blocks, deal names) is length-capped and the whole
    block is fenced as untrusted data by the prompt builder, so it cannot act as instructions.
  * A section that fails to load is skipped (in its own savepoint), never the whole reply.
"""

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.logger import log
from app.db.models.business import BusinessCanvas
from app.db.models.enums import (
    FINANCE_ROLES,
    MISSION_ROLES,
    SALES_ROLES,
    CanvasType,
    MembershipRole,
    MembershipStatus,
    MissionTaskStatus,
    RecommendationStatus,
    RoadmapStatus,
    StageKind,
)
from app.db.models.health_score import HealthRecommendation, HealthScore
from app.db.models.membership import Membership
from app.db.models.mission import Mission, MissionTask
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.db.models.sales import Deal, PipelineStage
from app.db.models.startup import Startup
from app.services.ai_cofounder.finance_context import finance_summary
from app.services.business.canvas_defs import CANVAS_BLOCKS
from app.services.health_score.service import latest_completed_result

_CAP = 160  # characters kept from any one user-authored string
_MAX_ITEMS = 5


def _clip(value: Any, cap: int = _CAP) -> str:
    return " ".join(str(value).split())[:cap]


@dataclass(frozen=True)
class Section:
    key: str
    label: str  # shown to the founder as a source ("Your roadmap")
    roles: frozenset[MembershipRole] | None  # None = any member who can open the chat
    build: Callable[[Session, Startup], str | None]


def _profile(db: Session, s: Startup) -> str | None:
    p = s.profile
    goals = "; ".join(_clip(g) for g in (p.goals if p and p.goals else [])[:_MAX_ITEMS])
    return (
        f"Startup: {_clip(s.name or 'unspecified', 80)}. Industry: {_clip(s.industry or 'unspecified', 60)}. "
        f"Stage: {s.stage.value if s.stage else 'unspecified'}. "
        f"One-liner: {_clip(s.description or 'unspecified')}. Current goals: {goals or 'not specified'}."
    )


def _roadmap(db: Session, s: Startup) -> str | None:
    road = db.query(Roadmap).filter_by(startup_id=s.id).first()
    if road is None:
        return None
    phases = (
        db.query(RoadmapPhase)
        .filter_by(roadmap_id=road.id)
        .order_by(RoadmapPhase.order.asc())
        .all()
    )
    rows = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapPhase.id == RoadmapMilestone.phase_id)
        .filter(RoadmapPhase.roadmap_id == road.id)
        .all()
    )
    if not rows:
        return None
    done = sum(1 for m in rows if m.status == RoadmapStatus.done)
    open_ms = sorted(
        (m for m in rows if m.status != RoadmapStatus.done),
        key=lambda m: (m.due_on or dt.date.max, m.order),
    )[:_MAX_ITEMS]
    lines = [
        f"Roadmap ({road.stage.value} stage): {done} of {len(rows)} milestones done; phases: "
        + ", ".join(_clip(p.name, 60) for p in phases)
        + "."
    ]
    for m in open_ms:
        due = f", due {m.due_on.isoformat()}" if m.due_on else ""
        lines.append(f"- Open milestone: {_clip(m.title)} ({m.status.value}, {m.progress}%{due})")
    return "\n".join(lines)


def _mission(db: Session, s: Startup) -> str | None:
    m = db.query(Mission).filter_by(startup_id=s.id, mission_date=dt.date.today()).first()
    if m is None:
        return None
    tasks = db.query(MissionTask).filter_by(mission_id=m.id).order_by(MissionTask.order).all()
    if not tasks:
        return None
    done = sum(1 for t in tasks if t.status == MissionTaskStatus.done)
    lines = [f"Today's mission: {done} of {len(tasks)} tasks done."]
    lines += [f"- {_clip(t.title)} ({t.status.value})" for t in tasks[:_MAX_ITEMS]]
    return "\n".join(lines)


def _health(db: Session, s: Startup) -> str | None:
    hs = db.query(HealthScore).filter_by(startup_id=s.id).first()
    if hs is None:
        return None
    dims = sorted((hs.dimension_scores or {}).items(), key=lambda kv: kv[1])
    weakest = ", ".join(f"{k} {v}" for k, v in dims[:2])
    lines = [f"Health score: {hs.score}/100 ({hs.band}); weakest dimensions: {weakest or 'n/a'}."]
    recs = (
        db.query(HealthRecommendation)
        .filter_by(startup_id=s.id, status=RecommendationStatus.pending)
        .order_by(HealthRecommendation.priority.asc())
        .limit(3)
        .all()
    )
    lines += [f"- Open recommendation: {_clip(r.title)}" for r in recs]
    return "\n".join(lines)


def _assessment(db: Session, s: Startup) -> str | None:
    res = latest_completed_result(db, s.id)
    if res is None:
        return None
    dims = ", ".join(f"{k} {v}" for k, v in (res.dimension_scores or {}).items())
    return f"Latest assessment: overall {res.overall_provisional}/100; dimensions: {dims}."


def _canvas(db: Session, s: Startup) -> str | None:
    c = db.query(BusinessCanvas).filter_by(startup_id=s.id, type=CanvasType.business_model).first()
    if c is None or not c.blocks:
        return None
    lines = []
    for block in CANVAS_BLOCKS[CanvasType.business_model]:
        v = c.blocks.get(block.key)
        text = "; ".join(_clip(x, 80) for x in v[:3]) if isinstance(v, list) else _clip(v or "")
        if text:
            lines.append(f"- {block.label}: {text}"[: _CAP + 40])
    return "Business model canvas:\n" + "\n".join(lines) if lines else None


def _pipeline(db: Session, s: Startup) -> str | None:
    deals = (
        db.query(Deal, PipelineStage)
        .join(PipelineStage, PipelineStage.id == Deal.stage_id)
        .filter(Deal.startup_id == s.id, PipelineStage.kind == StageKind.open)
        .order_by(Deal.value_minor.desc())
        .all()
    )
    if not deals:
        return None
    total = sum(d.value_minor for d, _ in deals)
    cur = deals[0][0].currency
    lines = [f"Sales pipeline: {len(deals)} open deals worth {cur} {total / 100:,.0f}."]
    lines += [
        f"- {_clip(d.name, 80)} ({_clip(st.name, 40)}, {cur} {d.value_minor / 100:,.0f})"
        for d, st in deals[:3]
    ]
    return "\n".join(lines)


def _finance(db: Session, s: Startup) -> str | None:
    f = finance_summary(db, startup_id=s.id)
    cur = f["currency"]

    def money(minor: int) -> str:
        return f"{cur} {minor / 100:,.0f}"

    runway = (
        f"{f['runway_months']} months"
        if f["runway_months"] is not None
        else "no runway figure (not burning cash, or out of cash)"
    )
    return (
        "Finance snapshot (from their ledger): "
        f"cash on hand {money(f['cash_on_hand'])}; monthly burn {money(f['monthly_burn'])}; "
        f"monthly revenue {money(f['monthly_revenue'])} (3-month average, funding rounds excluded); "
        f"runway {runway}; overdue invoices {f['overdue_invoice_count']} "
        f"totalling {money(f['overdue_invoice_total'])}."
    )


SECTIONS: dict[str, Section] = {
    sec.key: sec
    for sec in (
        Section("profile", "Your startup profile", None, _profile),
        Section("roadmap", "Your roadmap", MISSION_ROLES, _roadmap),
        Section("mission", "Today's mission", MISSION_ROLES, _mission),
        Section("health", "Your health score", MISSION_ROLES, _health),
        Section("assessment", "Your assessment", MISSION_ROLES, _assessment),
        Section("canvas", "Your business canvas", MISSION_ROLES, _canvas),
        Section("pipeline", "Your sales pipeline", SALES_ROLES, _pipeline),
        Section("finance", "Your finance snapshot", FINANCE_ROLES, _finance),
    )
}


def _role(db: Session, startup_id: uuid.UUID, user_id: uuid.UUID | None) -> MembershipRole | None:
    if user_id is None:
        return None
    role: MembershipRole | None = (
        db.query(Membership.role)
        .filter(
            Membership.startup_id == startup_id,
            Membership.user_id == user_id,
            Membership.status == MembershipStatus.active,
        )
        .scalar()
    )
    return role


def build_context(
    db: Session, startup: Startup, user_id: uuid.UUID | None, section_keys: tuple[str, ...]
) -> tuple[str, list[dict[str, str]]]:
    """The context text for an agent plus the sources actually used (`[{key, label}]`).

    Sections the asker's role may not read are skipped silently; so are empty ones and ones that
    raise. Pure reads: nothing here writes."""
    role = _role(db, startup.id, user_id)
    parts: list[str] = []
    used: list[dict[str, str]] = []
    for key in section_keys:
        sec = SECTIONS[key]
        if sec.roles is not None and role not in sec.roles:
            continue
        try:
            with db.begin_nested():
                text = sec.build(db, startup)
        except Exception as exc:  # noqa: BLE001 - one broken section must not fail the reply
            log.warning(f"[ai] context section {key} skipped: {exc}")
            continue
        if text:
            parts.append(text)
            used.append({"key": sec.key, "label": sec.label})
    return "\n\n".join(parts), used

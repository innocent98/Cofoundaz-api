"""Global ⌘K search (PRD §2.1 top bar; decision D-DASH-3: v1 covers everything that exists).

One declarative table of sources. Every source is tenant-scoped to the caller's workspace and gated
by the same roles as the module it comes from, so search never surfaces a row the caller couldn't
open from that module's page. Matching is a case-insensitive substring (ILIKE) with LIKE
metacharacters escaped. Each source returns at most `PER_SOURCE` rows.

Not searchable by design: journal entries (no title; content is encrypted at rest) and Sales
contacts (no backend yet).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Query, Session

from app.db.models.ai_chat import AiConversation
from app.db.models.calendar import CalendarEvent
from app.db.models.document import Document
from app.db.models.enums import FINANCE_ROLES, MARKETING_ROLES, MISSION_ROLES, MembershipRole
from app.db.models.finance import Transaction
from app.db.models.invoice import Invoice
from app.db.models.marketing import AudienceSegment, Campaign, ContentCalendarEntry, SeoKeyword
from app.db.models.mission import Mission, MissionTask
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase, RoadmapTask
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.models.validation import Assumption, Experiment, Survey

PER_SOURCE = 5
MIN_QUERY, MAX_QUERY = 2, 100
CORE_ROLES = frozenset({MembershipRole.founder, MembershipRole.team_member})
ALL_ROLES = frozenset(MembershipRole)

Row = tuple[Any, str, str | None]  # (id, title, subtitle)


@dataclass(frozen=True)
class Page:
    title: str
    href: str
    roles: frozenset[MembershipRole]
    keywords: str = ""


# The sidebar (PRD §2.1) plus the sub-pages people jump to. Roles mirror each module's route gate,
# so a role never finds a page it would be refused.
PAGES: tuple[Page, ...] = (
    Page("Dashboard", "/dashboard", ALL_ROLES, "home overview"),
    Page("Today's Mission", "/mission", MISSION_ROLES, "tasks today daily"),
    Page("Health Score", "/health", ALL_ROLES, "score dimensions"),
    Page("Roadmap", "/roadmap", ALL_ROLES, "plan timeline"),
    Page("Milestones", "/roadmap/milestones", ALL_ROLES, "roadmap"),
    Page("Business Builder", "/business-builder", ALL_ROLES, "canvas plan"),
    Page("Business Plan", "/business-builder/plan", ALL_ROLES, "business builder"),
    Page("Lean Canvas", "/business-builder/lean-canvas", ALL_ROLES, "business builder"),
    Page("Business Model Canvas", "/business-builder/business-model-canvas", ALL_ROLES, "bmc"),
    Page("Value Proposition", "/business-builder/value-proposition", ALL_ROLES, "business"),
    Page("Customer Personas", "/business-builder/personas", ALL_ROLES, "business customers"),
    Page("Pricing", "/business-builder/pricing", ALL_ROLES, "business price tiers"),
    Page("Revenue Model", "/business-builder/revenue-model", ALL_ROLES, "business"),
    Page(
        "Competitive Analysis", "/business-builder/competitive-analysis", ALL_ROLES, "competitors"
    ),
    Page("SWOT", "/business-builder/swot", ALL_ROLES, "business strengths weaknesses"),
    Page("Mission & Vision", "/business-builder/mission-vision", ALL_ROLES, "business purpose"),
    Page("Validation Hub", "/validation", CORE_ROLES, "experiments"),
    Page("Assumptions", "/validation/assumptions", CORE_ROLES, "validation"),
    Page("Interviews", "/validation/interviews", CORE_ROLES, "validation customer"),
    Page("Surveys", "/validation/surveys", CORE_ROLES, "validation"),
    Page("Assessment", "/assessment", CORE_ROLES, "calibrate"),
    Page("Marketing Hub", "/marketing", MARKETING_ROLES, "growth"),
    Page("Campaigns", "/marketing/campaigns", MARKETING_ROLES, "marketing"),
    Page("Content Calendar", "/marketing/calendar", MARKETING_ROLES, "marketing posts"),
    Page("SEO Tools", "/marketing/seo", MARKETING_ROLES, "marketing keywords"),
    Page("Finance Hub", "/finance", FINANCE_ROLES, "money cash flow"),
    Page("Runway", "/finance/runway", FINANCE_ROLES, "finance burn"),
    Page("Invoices", "/finance/invoices", FINANCE_ROLES, "finance billing"),
    Page("Expenses", "/finance/expenses", FINANCE_ROLES, "finance spend"),
    Page("Budgets", "/finance/budgets", FINANCE_ROLES, "finance"),
    Page("Financial Model", "/finance/model", FINANCE_ROLES, "finance forecast"),
    Page("Learning Academy", "/academy", CORE_ROLES, "courses learn"),
    Page("Documents", "/documents", ALL_ROLES, "files docs"),
    Page("Calendar", "/calendar", CORE_ROLES, "events schedule"),
    Page("AI Co-Founder", "/ai", CORE_ROLES, "chat assistant ask"),
    Page("Journal", "/journal", frozenset({MembershipRole.founder}), "private notes"),
    Page("Notifications", "/notifications", ALL_ROLES, "alerts"),
    Page("Settings & Billing", "/settings", ALL_ROLES, "account preferences"),
)


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _has(col: Any, pattern: str) -> Any:
    return cast(col, String).ilike(pattern, escape="\\")


@dataclass(frozen=True)
class Source:
    type: str
    group: str
    roles: frozenset[MembershipRole]
    href: Callable[[Any], str]
    rows: Callable[[Session, Startup, User, str], Query[Any]]


def _milestones(db: Session, s: Startup, _u: User, p: str) -> Query[Any]:
    return (
        db.query(RoadmapMilestone.id, RoadmapMilestone.title, RoadmapMilestone.due_on)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(Roadmap.startup_id == s.id, _has(RoadmapMilestone.title, p))
        .order_by(RoadmapMilestone.due_on.asc().nulls_last())
    )


def _roadmap_tasks(db: Session, s: Startup, _u: User, p: str) -> Query[Any]:
    return (
        db.query(RoadmapTask.id, RoadmapTask.title, RoadmapMilestone.title)
        .join(RoadmapMilestone, RoadmapTask.milestone_id == RoadmapMilestone.id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(Roadmap.startup_id == s.id, _has(RoadmapTask.title, p))
        .order_by(RoadmapTask.title)
    )


def _mission_tasks(db: Session, s: Startup, _u: User, p: str) -> Query[Any]:
    since = date.today() - timedelta(days=14)
    return (
        db.query(MissionTask.id, MissionTask.title, Mission.mission_date)
        .join(Mission, MissionTask.mission_id == Mission.id)
        .filter(
            Mission.startup_id == s.id,
            Mission.mission_date >= since,
            _has(MissionTask.title, p),
        )
        .order_by(Mission.mission_date.desc())
    )


def _documents(db: Session, s: Startup, _u: User, p: str) -> Query[Any]:
    return (
        db.query(Document.id, Document.title, Document.updated_at)
        .filter(Document.startup_id == s.id, _has(Document.title, p))
        .order_by(Document.updated_at.desc())
    )


def _conversations(db: Session, s: Startup, u: User, p: str) -> Query[Any]:
    # Private to their creator, exactly like GET /ai/conversations.
    return (
        db.query(AiConversation.id, AiConversation.title, AiConversation.updated_at)
        .filter(
            AiConversation.startup_id == s.id,
            AiConversation.created_by == u.id,
            _has(AiConversation.title, p),
        )
        .order_by(AiConversation.updated_at.desc())
    )


def _events(db: Session, s: Startup, _u: User, p: str) -> Query[Any]:
    return (
        db.query(CalendarEvent.id, CalendarEvent.title, CalendarEvent.event_date)
        .filter(CalendarEvent.startup_id == s.id, _has(CalendarEvent.title, p))
        .order_by(CalendarEvent.event_date.desc())
    )


def _assumptions(db: Session, s: Startup, _u: User, p: str) -> Query[Any]:
    return (
        db.query(Assumption.id, Assumption.statement, Assumption.status)
        .filter(Assumption.startup_id == s.id, _has(Assumption.statement, p))
        .order_by(Assumption.created_at.desc())
    )


def _experiments(db: Session, s: Startup, _u: User, p: str) -> Query[Any]:
    return (
        db.query(Experiment.id, Experiment.name, Experiment.created_at)
        .filter(Experiment.startup_id == s.id, _has(Experiment.name, p))
        .order_by(Experiment.created_at.desc())
    )


def _surveys(db: Session, s: Startup, _u: User, p: str) -> Query[Any]:
    return (
        db.query(Survey.id, Survey.title, Survey.created_at)
        .filter(Survey.startup_id == s.id, _has(Survey.title, p))
        .order_by(Survey.created_at.desc())
    )


def _transactions(db: Session, s: Startup, _u: User, p: str) -> Query[Any]:
    return (
        db.query(Transaction.id, Transaction.description, Transaction.date)
        .filter(Transaction.startup_id == s.id, _has(Transaction.description, p))
        .order_by(Transaction.date.desc())
    )


def _invoices(db: Session, s: Startup, _u: User, p: str) -> Query[Any]:
    return (
        db.query(Invoice.id, Invoice.number, Invoice.client_name)
        .filter(
            Invoice.startup_id == s.id,
            or_(_has(Invoice.number, p), _has(Invoice.client_name, p)),
        )
        .order_by(Invoice.created_at.desc())
    )


def _campaigns(db: Session, s: Startup, _u: User, p: str) -> Query[Any]:
    return (
        db.query(Campaign.id, Campaign.name, Campaign.status)
        .filter(Campaign.startup_id == s.id, _has(Campaign.name, p))
        .order_by(Campaign.created_at.desc())
    )


def _posts(db: Session, s: Startup, _u: User, p: str) -> Query[Any]:
    return (
        db.query(ContentCalendarEntry.id, ContentCalendarEntry.title, ContentCalendarEntry.status)
        .filter(ContentCalendarEntry.startup_id == s.id, _has(ContentCalendarEntry.title, p))
        .order_by(ContentCalendarEntry.created_at.desc())
    )


def _keywords(db: Session, s: Startup, _u: User, p: str) -> Query[Any]:
    return (
        db.query(SeoKeyword.id, SeoKeyword.keyword, SeoKeyword.created_at)
        .filter(SeoKeyword.startup_id == s.id, _has(SeoKeyword.keyword, p))
        .order_by(SeoKeyword.keyword)
    )


def _segments(db: Session, s: Startup, _u: User, p: str) -> Query[Any]:
    return (
        db.query(AudienceSegment.id, AudienceSegment.name, AudienceSegment.created_at)
        .filter(AudienceSegment.startup_id == s.id, _has(AudienceSegment.name, p))
        .order_by(AudienceSegment.name)
    )


def _fixed(href: str) -> Callable[[Any], str]:
    return lambda _id: href


SOURCES: tuple[Source, ...] = (
    Source("milestone", "Roadmap", ALL_ROLES, _fixed("/roadmap/milestones"), _milestones),
    Source("roadmap_task", "Roadmap", ALL_ROLES, _fixed("/roadmap"), _roadmap_tasks),
    Source("mission_task", "Mission", MISSION_ROLES, _fixed("/mission"), _mission_tasks),
    Source("document", "Documents", ALL_ROLES, lambda i: f"/documents/{i}", _documents),
    Source("conversation", "AI Co-Founder", CORE_ROLES, lambda i: f"/ai?c={i}", _conversations),
    Source("calendar_event", "Calendar", CORE_ROLES, _fixed("/calendar"), _events),
    Source("assumption", "Validation", CORE_ROLES, _fixed("/validation/assumptions"), _assumptions),
    Source("experiment", "Validation", CORE_ROLES, _fixed("/validation/smoke-tests"), _experiments),
    Source("survey", "Validation", CORE_ROLES, _fixed("/validation/surveys"), _surveys),
    Source("transaction", "Finance", FINANCE_ROLES, _fixed("/finance"), _transactions),
    Source("invoice", "Finance", FINANCE_ROLES, _fixed("/finance/invoices"), _invoices),
    Source("campaign", "Marketing", MARKETING_ROLES, _fixed("/marketing/campaigns"), _campaigns),
    Source("post", "Marketing", MARKETING_ROLES, _fixed("/marketing/calendar"), _posts),
    Source("keyword", "Marketing", MARKETING_ROLES, _fixed("/marketing/seo"), _keywords),
    Source("segment", "Marketing", MARKETING_ROLES, _fixed("/marketing/segments"), _segments),
)

_SUBTITLE: dict[str, Callable[[Any], str | None]] = {
    "milestone": lambda v: f"Milestone · due {v.isoformat()}" if v else "Milestone",
    "roadmap_task": lambda v: f"Task in {v}" if v else "Roadmap task",
    "mission_task": lambda v: f"Mission task · {v.isoformat()}",
    "document": lambda _v: "Document",
    "conversation": lambda _v: "AI conversation",
    "calendar_event": lambda v: f"Event · {v.isoformat()}" if v else "Event",
    "assumption": lambda v: f"Assumption · {getattr(v, 'value', v)}",
    "experiment": lambda _v: "Smoke test",
    "survey": lambda _v: "Survey",
    "transaction": lambda v: f"Transaction · {v.isoformat()}",
    "invoice": lambda v: f"Invoice · {v}",
    "campaign": lambda v: f"Campaign · {getattr(v, 'value', v)}",
    "post": lambda v: f"Scheduled post · {getattr(v, 'value', v)}",
    "keyword": lambda _v: "SEO keyword",
    "segment": lambda _v: "Audience segment",
}


def normalize_query(raw: str | None) -> str | None:
    q = " ".join((raw or "").split())
    return q[:MAX_QUERY] if len(q) >= MIN_QUERY else None


def search(db: Session, startup: Startup, user: User, role: MembershipRole, raw: str) -> dict:
    q = normalize_query(raw)
    if q is None:
        return {"query": raw or "", "results": []}
    needle = q.lower()
    results: list[dict[str, Any]] = [
        {
            "type": "page",
            "group": "Pages",
            "id": p.href,
            "title": p.title,
            "subtitle": None,
            "href": p.href,
        }
        for p in PAGES
        if role in p.roles and (needle in p.title.lower() or needle in p.keywords)
    ][:PER_SOURCE]
    pattern = _like(q)
    for src in SOURCES:
        if role not in src.roles:
            continue
        for row_id, title, extra in src.rows(db, startup, user, pattern).limit(PER_SOURCE):
            results.append(
                {
                    "type": src.type,
                    "group": src.group,
                    "id": str(row_id),
                    "title": title,
                    "subtitle": _SUBTITLE[src.type](extra),
                    "href": src.href(row_id),
                }
            )
    return {"query": q, "results": results}

"""Live Health Score signals (PRD Module 06: "weighs live signals from every hub").

One declarative table. Each signal reads a real number from a module, has a stage-scaled target,
a source link, and the recommendation it produces while it is below target. A signal scores
`min(value / target, 1) * 100`. `None` means "not applicable yet" (e.g. runway with no finance
data) and is left out instead of counted as 0. Read-only: nothing here writes.

Blending into the dimension score lives in `service.recompute_health_score` (decision D-HEA-1).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models.activity import ActivityLog
from app.db.models.document import SignatureRequest
from app.db.models.enums import (
    AssumptionStatus,
    MembershipStatus,
    RoadmapStatus,
    SignatureRequestStatus,
    StartupStage,
)
from app.db.models.finance import Transaction
from app.db.models.membership import Membership
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase, RoadmapTask
from app.db.models.startup import Startup
from app.db.models.validation import Assumption, Interview
from app.services.dashboard.kpis import kpi_metrics

STAGES = tuple(StartupStage)


def _by_stage(*values: float) -> dict[StartupStage, float]:
    """Targets in stage order: idea, validation, build, launch, growth, scale."""
    assert len(values) == len(STAGES)  # nosec B101 - table-definition invariant
    return dict(zip(STAGES, values, strict=True))


@dataclass(frozen=True)
class SignalDef:
    key: str
    dimension: str
    label: str
    unit: str
    targets: dict[StartupStage, float]
    href: str
    read: Callable[[Session, Startup], float | None]
    rec_title: str
    rec_body: str
    rec_effort: str

    def target(self, stage: StartupStage | None) -> float:
        return self.targets[stage or StartupStage.idea]


def _today() -> date:
    return datetime.now(UTC).date()


def _roadmap_done_pct(db: Session, s: Startup) -> float | None:
    rows = (
        db.query(RoadmapTask.status)
        .join(RoadmapMilestone, RoadmapTask.milestone_id == RoadmapMilestone.id)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(Roadmap.startup_id == s.id)
        .all()
    )
    if not rows:
        return None
    return round(100 * sum(1 for (st,) in rows if st == RoadmapStatus.done) / len(rows), 1)


def _mission_completion(db: Session, s: Startup) -> float | None:
    from app.services.mission.service import _weekly_completion_pct  # avoid an import cycle

    return float(_weekly_completion_pct(db, s))


def _assumptions_tested(db: Session, s: Startup) -> float:
    return float(
        db.query(Assumption)
        .filter(
            Assumption.startup_id == s.id,
            Assumption.status.in_([AssumptionStatus.validated, AssumptionStatus.invalidated]),
        )
        .count()
    )


def _interviews_30d(db: Session, s: Startup) -> float:
    since = _today() - timedelta(days=30)
    return float(
        db.query(Interview).filter(Interview.startup_id == s.id, Interview.held_on >= since).count()
    )


def _transactions_30d(db: Session, s: Startup) -> float:
    today = _today()
    return float(
        db.query(Transaction)
        .filter(
            Transaction.startup_id == s.id,
            Transaction.date >= today - timedelta(days=30),
            Transaction.date <= today,
        )
        .count()
    )


def _runway_months(db: Session, s: Startup) -> float | None:
    from app.db.models.enums import MembershipRole

    runway = kpi_metrics(db, s, MembershipRole.founder)["runway"]
    status = runway.get("status")
    if status == "ok":
        return float(runway["months"])
    if status == "out_of_cash":
        return 0.0
    if status == "not_burning":
        return 999.0  # profitable: full marks
    return None  # no finance data: not counted (transactions signal covers tracking)


def _signatures_complete(db: Session, s: Startup) -> float:
    return float(
        db.query(SignatureRequest)
        .filter(
            SignatureRequest.startup_id == s.id,
            SignatureRequest.status == SignatureRequestStatus.complete,
        )
        .count()
    )


def _active_members(db: Session, s: Startup) -> float:
    return float(
        db.query(Membership)
        .filter(Membership.startup_id == s.id, Membership.status == MembershipStatus.active)
        .count()
    )


def _activity_7d(db: Session, s: Startup) -> float:
    since = datetime.now(UTC) - timedelta(days=7)
    return float(
        db.query(ActivityLog)
        .filter(ActivityLog.startup_id == s.id, ActivityLog.created_at >= since)
        .count()
    )


def _streak(db: Session, s: Startup) -> float:
    from app.services.mission.service import streak

    return float(streak(db, s))


SIGNALS: tuple[SignalDef, ...] = (
    SignalDef(
        "product.roadmap_progress",
        "product",
        "Roadmap tasks done",
        "%",
        _by_stage(10, 20, 40, 60, 70, 80),
        "/roadmap",
        _roadmap_done_pct,
        "Close out your next roadmap tasks",
        "Finish the open tasks on your nearest milestone. Shipped work is the clearest product signal.",
        "medium",
    ),
    SignalDef(
        "product.mission_completion",
        "product",
        "Mission completion (7 days)",
        "%",
        _by_stage(70, 70, 70, 70, 70, 70),
        "/mission",
        _mission_completion,
        "Complete your daily missions",
        "Finish today's mission. Consistent execution on the highest-leverage tasks compounds fast.",
        "low",
    ),
    SignalDef(
        "market.assumptions_tested",
        "market",
        "Assumptions tested",
        "count",
        _by_stage(2, 5, 8, 10, 10, 10),
        "/validation/assumptions",
        _assumptions_tested,
        "Test your riskiest assumption",
        "Pick the assumption that would sink you if wrong and run a quick experiment to validate or kill it.",
        "medium",
    ),
    SignalDef(
        "market.interviews_30d",
        "market",
        "Customer interviews (30 days)",
        "count",
        _by_stage(3, 5, 5, 3, 3, 3),
        "/validation/interviews",
        _interviews_30d,
        "Talk to customers this month",
        "Log a few customer interviews. Real conversations beat assumptions every time.",
        "medium",
    ),
    SignalDef(
        "money.transactions_30d",
        "money",
        "Transactions tracked (30 days)",
        "count",
        _by_stage(3, 5, 8, 10, 15, 20),
        "/finance",
        _transactions_30d,
        "Track your money in and out",
        "Record this month's income and expenses so your runway and burn are based on real numbers.",
        "low",
    ),
    SignalDef(
        "money.runway_live",
        "money",
        "Runway",
        "months",
        _by_stage(12, 12, 12, 12, 12, 12),
        "/finance/runway",
        _runway_months,
        "Extend your runway",
        "Cut a cost line or bring in revenue. Twelve months of runway keeps you out of panic mode.",
        "high",
    ),
    SignalDef(
        "legal.signatures_complete",
        "legal",
        "Agreements signed",
        "count",
        _by_stage(1, 1, 2, 3, 4, 5),
        "/documents/signatures",
        _signatures_complete,
        "Get your key agreements signed",
        "Send your founders' agreement or first customer contract for e-signature and get it fully signed.",
        "medium",
    ),
    SignalDef(
        "team.members",
        "team",
        "Active team members",
        "count",
        _by_stage(1, 2, 3, 4, 6, 10),
        "/team",
        _active_members,
        "Bring in your next teammate",
        "Invite a co-founder, advisor or first hire. Even one more person changes what you can ship.",
        "medium",
    ),
    SignalDef(
        "team.activity_7d",
        "team",
        "Workspace activity (7 days)",
        "events",
        _by_stage(10, 10, 15, 20, 25, 30),
        "/dashboard",
        _activity_7d,
        "Keep the workspace moving",
        "Log progress across your hubs this week: tasks, validation notes, finance entries.",
        "low",
    ),
    SignalDef(
        "team.streak",
        "team",
        "Mission streak",
        "days",
        _by_stage(5, 5, 5, 5, 5, 5),
        "/mission/streaks",
        _streak,
        "Build a mission streak",
        "Complete your mission a few days in a row. Consistency beats intensity.",
        "low",
    ),
)
SIGNALS_BY_KEY = {s.key: s for s in SIGNALS}


@dataclass(frozen=True)
class SignalReading:
    signal: SignalDef
    value: float
    target: float
    score: float  # 0..100


def read_signals(db: Session, startup: Startup) -> list[SignalReading]:
    """Every applicable live signal for the startup, scored against its stage target."""
    out = []
    for sig in SIGNALS:
        value = sig.read(db, startup)
        if value is None:
            continue
        target = sig.target(startup.stage)
        score = 100.0 if target <= 0 else min(value / target, 1.0) * 100
        out.append(SignalReading(sig, value, target, round(score, 1)))
    return out

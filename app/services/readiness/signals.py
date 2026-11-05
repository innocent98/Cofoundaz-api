"""Investor Readiness signals (PRD 15.1): one declarative table, like Health Score's.

Readers that Health Score already has are imported from `health_score.live_signals` (never
copied); new ones read the other hubs directly. A reader returns the number to compare with the
stage target, or `None` when the signal does not apply yet (it then drops out of its dimension
instead of counting as 0). Where absence IS the finding (no model, no deck) the reader returns 0.
Read-only: nothing here writes.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models.business import BusinessCanvas, BusinessPlan, BusinessRecord
from app.db.models.document import Document, SignatureRequest
from app.db.models.enums import (
    AssumptionStatus,
    BusinessPlanStatus,
    DocumentKind,
    ExperimentStatus,
    FinancialModelStatus,
    InvoiceStatus,
    MembershipRole,
    MembershipStatus,
    RecordKind,
    SignatureRequestStatus,
    StageKind,
    StartupStage,
)
from app.db.models.financial_model import FinancialModel
from app.db.models.invoice import Invoice
from app.db.models.membership import Membership
from app.db.models.readiness import MockSession, ReadinessInput
from app.db.models.sales import Deal, PipelineStage
from app.db.models.startup import Startup
from app.db.models.validation import Assumption, Experiment, SmokeTestEvent, SurveyResponse
from app.services.health_score.live_signals import SIGNALS_BY_KEY as HEALTH_SIGNALS
from app.services.health_score.service import latest_completed_result
from app.services.readiness import deck
from app.services.readiness.scoring import Reading, signal_score

STAGES = tuple(StartupStage)
ADVISOR_ROLES = (
    MembershipRole.mentor,
    MembershipRole.business_consultant,
    MembershipRole.legal_advisor,
)
# Monthly revenue targets, in naira, by stage. Other currencies are not converted yet, so the
# signal is "not applicable" for them rather than wrong.
_REVENUE_NGN = (50_000, 150_000, 300_000, 1_000_000, 3_000_000, 10_000_000)


def by_stage(*values: float) -> dict[StartupStage, float]:
    """Targets in stage order: idea, validation, build, launch, growth, scale."""
    assert len(values) == len(STAGES)  # nosec B101 - table-definition invariant
    return dict(zip(STAGES, values, strict=True))


@dataclass(frozen=True)
class Signal:
    key: str
    dimension: str
    label: str
    unit: str
    targets: dict[StartupStage, float]
    href: str
    read: Callable[[Session, Startup], float | None]
    note: Callable[[float, float], str]  # (value, target) -> gap note with the real numbers
    weight: float = 1.0

    def target(self, stage: StartupStage | None) -> float:
        return self.targets[stage or StartupStage.idea]


def _health(key: str) -> Callable[[Session, Startup], float | None]:
    """Reuse a Health Score reader as it is."""
    return HEALTH_SIGNALS[key].read


def _count(db: Session, model: Any, *where: Any) -> int:
    return int(db.query(func.count()).select_from(model).filter(*where).scalar() or 0)


def _n(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


# ---- Team ----------------------------------------------------------------------------------


def _founder_agreement(db: Session, s: Startup) -> float:
    """1 once a founder agreement exists, 2 once the workspace also has a completed signature
    (a signature request is not linked to a rich document yet, so this is the honest proxy)."""
    drafted = _count(
        db,
        Document,
        Document.startup_id == s.id,
        Document.kind == DocumentKind.founder_agreement,
        Document.trashed_at.is_(None),
    )
    if not drafted:
        return 0.0
    signed = _count(
        db,
        SignatureRequest,
        SignatureRequest.startup_id == s.id,
        SignatureRequest.status == SignatureRequestStatus.complete,
    )
    return 2.0 if signed else 1.0


def _advisors(db: Session, s: Startup) -> float:
    return float(
        _count(
            db,
            Membership,
            Membership.startup_id == s.id,
            Membership.status == MembershipStatus.active,
            Membership.role.in_(ADVISOR_ROLES),
        )
    )


def _assessment_team(db: Session, s: Startup) -> float | None:
    result = latest_completed_result(db, s.id)
    if result is None:
        return None
    return float(result.dimension_scores.get("team", 0))


# ---- Traction ------------------------------------------------------------------------------


def _monthly_revenue(db: Session, s: Startup) -> float | None:
    from app.services.finance.cashflow import cash_flow_summary

    summary = cash_flow_summary(db, startup_id=s.id)
    if summary["currency"] != "NGN":
        return None
    return float(summary["monthly_revenue"]) / 100  # kobo -> naira


def _won_deals(db: Session, s: Startup) -> int:
    return int(
        db.query(func.count(Deal.id))
        .join(PipelineStage, Deal.stage_id == PipelineStage.id)
        .filter(Deal.startup_id == s.id, PipelineStage.kind == StageKind.won)
        .scalar()
        or 0
    )


def _paying_customers(db: Session, s: Startup) -> float:
    """The larger of won deals and distinct paying invoice clients (they overlap, so no sum)."""
    clients = int(
        db.query(func.count(func.distinct(func.lower(Invoice.client_email))))
        .filter(Invoice.startup_id == s.id, Invoice.status == InvoiceStatus.paid)
        .scalar()
        or 0
    )
    return float(max(_won_deals(db, s), clients))


def _open_deals(db: Session, s: Startup) -> float:
    return float(
        db.query(func.count(Deal.id))
        .join(PipelineStage, Deal.stage_id == PipelineStage.id)
        .filter(Deal.startup_id == s.id, PipelineStage.kind == StageKind.open)
        .scalar()
        or 0
    )


def _signups(db: Session, s: Startup) -> int:
    return _count(
        db, SmokeTestEvent, SmokeTestEvent.startup_id == s.id, SmokeTestEvent.kind == "signup"
    )


def _demand_proof(db: Session, s: Startup) -> float:
    return float(_signups(db, s) + _count(db, SurveyResponse, SurveyResponse.startup_id == s.id))


# ---- Market --------------------------------------------------------------------------------


def _canvas_block_filled(db: Session, s: Startup, *keys: str) -> bool:
    for canvas in db.query(BusinessCanvas).filter(BusinessCanvas.startup_id == s.id):
        for k in keys:
            if (canvas.blocks or {}).get(k):
                return True
    return False


def _customer_segments(db: Session, s: Startup) -> float:
    return 1.0 if _canvas_block_filled(db, s, "customer_segments") else 0.0


def _competitors(db: Session, s: Startup) -> float:
    return float(
        _count(
            db,
            BusinessRecord,
            BusinessRecord.startup_id == s.id,
            BusinessRecord.kind == RecordKind.competitor,
        )
    )


def _market_size(db: Session, s: Startup) -> float:
    """1 only when TAM, SAM and SOM are all given with a source: a claim without a source is not
    evidence (D-IR-2 note)."""
    row = db.query(ReadinessInput).filter(ReadinessInput.startup_id == s.id).first()
    if row is None or not row.source_url:
        return 0.0
    return 1.0 if row.tam_minor and row.sam_minor and row.som_minor else 0.0


# ---- Product -------------------------------------------------------------------------------


def _solution_defined(db: Session, s: Startup) -> float:
    return 1.0 if _canvas_block_filled(db, s, "solution", "value_propositions") else 0.0


def _experiments_run(db: Session, s: Startup) -> float:
    return float(
        _count(
            db,
            Experiment,
            Experiment.startup_id == s.id,
            Experiment.status.in_([ExperimentStatus.live, ExperimentStatus.ended]),
        )
    )


# ---- Financials ----------------------------------------------------------------------------


def _latest_model(db: Session, s: Startup) -> FinancialModel | None:
    return (
        db.query(FinancialModel)
        .filter(
            FinancialModel.startup_id == s.id,
            FinancialModel.status == FinancialModelStatus.complete,
        )
        .order_by(FinancialModel.generated_at.desc().nullslast(), FinancialModel.created_at.desc())
        .first()
    )


def _model_fresh(db: Session, s: Startup) -> float:
    """0 no model, 1 a model that was edited since it was last calculated, 2 a current model."""
    m = _latest_model(db, s)
    if m is None:
        return 0.0
    return 2.0 if m.overrides_rev == m.calculated_rev else 1.0


# ---- Story ---------------------------------------------------------------------------------


def _doc_exists(db: Session, s: Startup, kind: DocumentKind) -> float:
    return float(
        _count(
            db,
            Document,
            Document.startup_id == s.id,
            Document.kind == kind,
            Document.trashed_at.is_(None),
        )
        > 0
    )


def _deck_reviewed(db: Session, startup: Startup) -> bool:
    return deck.latest_ready(db, startup.id) is not None


def _deck_exists(db: Session, s: Startup) -> float:
    """A pitch deck document, or any deck that has been reviewed."""
    return 1.0 if _doc_exists(db, s, DocumentKind.pitch_deck) or _deck_reviewed(db, s) else 0.0


def _deck_review_score(db: Session, s: Startup) -> float:
    """Overall score (/10) of the latest finished review; 0 when there is none (absence is the
    finding)."""
    latest = deck.latest_ready(db, s.id)
    return float((latest.scores or {}).get("overall", 0)) if latest else 0.0


def _qa_average(db: Session, s: Startup) -> float:
    """Average answer rating (out of 5) of the latest scored mock Q&A; 0 when there is none
    (absence is the finding)."""
    latest = (
        db.query(MockSession.average)
        .filter(MockSession.startup_id == s.id, MockSession.status == "scored")
        .order_by(MockSession.scored_at.desc())
        .first()
    )
    return float(latest[0] or 0) if latest else 0.0


def _investor_plan(db: Session, s: Startup) -> float:
    for plan in db.query(BusinessPlan).filter(
        BusinessPlan.startup_id == s.id, BusinessPlan.status == BusinessPlanStatus.complete
    ):
        if (plan.options or {}).get("audience") == "investors":
            return 1.0
    return 0.0


SIGNALS: tuple[Signal, ...] = (
    # Team
    Signal(
        "team.members",
        "team",
        "Active team members",
        "count",
        by_stage(1, 2, 2, 3, 4, 6),
        "/team",
        _health("team.members"),
        lambda v, t: f"{_n(v)} active member(s). Investors back teams: invite a co-founder or first hire.",
    ),
    Signal(
        "team.founder_agreement",
        "team",
        "Founder agreement drafted and signed",
        "step",
        by_stage(2, 2, 2, 2, 2, 2),
        "/documents",
        _founder_agreement,
        lambda v, t: (
            "No founder agreement yet. Diligence asks for it first."
            if v < 1
            else "Your founder agreement is drafted but no agreement is signed yet."
        ),
    ),
    Signal(
        "team.advisors",
        "team",
        "Advisor or mentor on the team",
        "count",
        by_stage(1, 1, 1, 1, 2, 2),
        "/team",
        _advisors,
        lambda v, t: "No advisor or mentor attached. Invite one whose name an investor will know.",
    ),
    Signal(
        "team.assessment",
        "team",
        "Assessment Team score",
        "score",
        by_stage(70, 70, 70, 70, 70, 70),
        "/assessment",
        _assessment_team,
        lambda v, t: f"Your assessment scored Team at {_n(v)}. Retake it after closing the gaps.",
    ),
    Signal(
        "team.activity_7d",
        "team",
        "Workspace activity (7 days)",
        "events",
        by_stage(10, 10, 15, 20, 25, 30),
        "/dashboard",
        _health("team.activity_7d"),
        lambda v, t: f"Only {_n(v)} of {_n(t)} workspace actions this week. Investors read momentum.",
    ),
    # Traction
    Signal(
        "traction.revenue",
        "traction",
        "Monthly revenue (3 month average)",
        "NGN",
        by_stage(*_REVENUE_NGN),
        "/finance",
        _monthly_revenue,
        lambda v, t: f"Revenue averages ₦{v:,.0f} a month against ₦{t:,.0f} for your stage. Log income in Finance.",
        2.0,
    ),
    Signal(
        "traction.customers",
        "traction",
        "Paying customers",
        "count",
        by_stage(1, 1, 3, 10, 30, 100),
        "/sales/pipeline",
        _paying_customers,
        lambda v, t: f"{_n(v)} paying customer(s) against {_n(t)}. Close a deal or record a paid invoice.",
    ),
    Signal(
        "traction.pipeline",
        "traction",
        "Open deals in the pipeline",
        "count",
        by_stage(1, 3, 5, 10, 20, 40),
        "/sales",
        _open_deals,
        lambda v, t: f"{_n(v)} open deal(s) against {_n(t)}. Add real prospects to the pipeline.",
    ),
    Signal(
        "traction.demand_proof",
        "traction",
        "Demand proof (signups and survey answers)",
        "count",
        by_stage(10, 30, 60, 100, 200, 400),
        "/validation",
        _demand_proof,
        lambda v, t: f"{_n(v)} signups or survey answers against {_n(t)}. Run a smoke test or a survey.",
    ),
    # Market
    Signal(
        "market.assumptions_tested",
        "market",
        "Assumptions tested",
        "count",
        by_stage(2, 5, 8, 10, 10, 10),
        "/validation/assumptions",
        _health("market.assumptions_tested"),
        lambda v, t: f"{_n(v)} of {_n(t)} assumptions tested. Test the one that would sink you.",
    ),
    Signal(
        "market.interviews_30d",
        "market",
        "Customer interviews (30 days)",
        "count",
        by_stage(3, 5, 5, 3, 3, 3),
        "/validation/interviews",
        _health("market.interviews_30d"),
        lambda v, t: f"{_n(v)} of {_n(t)} customer interviews this month. Talk to more customers.",
    ),
    Signal(
        "market.customer_segments",
        "market",
        "Customer segments defined",
        "step",
        by_stage(1, 1, 1, 1, 1, 1),
        "/business-builder/canvas",
        _customer_segments,
        lambda v, t: "Your canvas has no customer segments. Say exactly who you sell to.",
    ),
    Signal(
        "market.competitors",
        "market",
        "Competitors mapped",
        "count",
        by_stage(2, 3, 3, 3, 3, 3),
        "/business-builder/competitors",
        _competitors,
        lambda v, t: f"{_n(v)} competitor(s) mapped. Investors ask 'who else?' first.",
    ),
    Signal(
        "market.size",
        "market",
        "Market size with a source",
        "step",
        by_stage(1, 1, 1, 1, 1, 1),
        "/investor-readiness",
        _market_size,
        lambda v, t: "Add TAM, SAM and SOM with a source link so the size is checkable.",
        2.0,
    ),
    # Product
    Signal(
        "product.roadmap_progress",
        "product",
        "Roadmap tasks done",
        "%",
        by_stage(10, 20, 40, 60, 70, 80),
        "/roadmap",
        _health("product.roadmap_progress"),
        lambda v, t: f"Roadmap is {_n(v)}% done against {_n(t)}%. Close out your next tasks.",
    ),
    Signal(
        "product.mission_completion",
        "product",
        "Mission completion (7 days)",
        "%",
        by_stage(70, 70, 70, 70, 70, 70),
        "/mission",
        _health("product.mission_completion"),
        lambda v, t: f"Mission completion is {_n(v)}% against {_n(t)}%. Finish today's mission.",
    ),
    Signal(
        "product.solution",
        "product",
        "Solution defined on the canvas",
        "step",
        by_stage(1, 1, 1, 1, 1, 1),
        "/business-builder/canvas",
        _solution_defined,
        lambda v, t: "Your canvas has no solution or value proposition. Write what you build and why.",
    ),
    Signal(
        "product.experiments",
        "product",
        "Experiments run",
        "count",
        by_stage(1, 1, 2, 3, 3, 3),
        "/validation/experiments",
        _experiments_run,
        lambda v, t: f"{_n(v)} experiment(s) run. Launch a test that shows people want it.",
    ),
    # Financials
    Signal(
        "financials.runway",
        "financials",
        "Runway",
        "months",
        by_stage(12, 12, 12, 12, 12, 12),
        "/finance/runway",
        _health("money.runway_live"),
        lambda v, t: f"Runway is {_n(v)} months against {_n(t)}. Raise or cut before outreach.",
        2.0,
    ),
    Signal(
        "financials.model",
        "financials",
        "Financial model, current",
        "step",
        by_stage(2, 2, 2, 2, 2, 2),
        "/finance/model",
        _model_fresh,
        lambda v, t: (
            "You have no financial model. Investors expect one."
            if v < 1
            else "Your model was edited after it was last calculated. Recalculate it."
        ),
        2.0,
    ),
    Signal(
        "financials.books",
        "financials",
        "Transactions tracked (30 days)",
        "count",
        by_stage(3, 5, 8, 10, 15, 20),
        "/finance",
        _health("money.transactions_30d"),
        lambda v, t: f"{_n(v)} of {_n(t)} transactions logged this month. Keep the books current.",
    ),
    # Story (deck review, narrative check and mock Q&A signals arrive with their slices)
    Signal(
        "story.pitch_deck",
        "story",
        "Pitch deck",
        "step",
        by_stage(1, 1, 1, 1, 1, 1),
        "/investor-readiness/deck",
        _deck_exists,
        lambda v, t: "You have no pitch deck. Upload your PDF and have it reviewed.",
    ),
    Signal(
        "story.deck_review",
        "story",
        "Pitch deck review score (out of 10)",
        "/10",
        by_stage(6, 6, 6, 7, 7, 8),
        "/investor-readiness/deck",
        _deck_review_score,
        lambda v, t: (
            "Your deck has not been reviewed yet."
            if v <= 0
            else f"Your deck review scored {v:g} of 10 against {t:g}. Work through the top fixes."
        ),
        2.0,
    ),
    Signal(
        "story.mock_qa",
        "story",
        "Defended the pitch (mock Q&A rating, out of 5)",
        "/5",
        by_stage(3, 3, 3, 3.5, 3.5, 4),
        "/investor-readiness/qa",
        _qa_average,
        lambda v, t: (
            "You have not rehearsed an investor Q&A yet. Run a mock session."
            if v <= 0
            else f"Your last mock Q&A averaged {v:g} of 5 against {t:g}. Run another and fix your weakest answer."
        ),
        2.0,
    ),
    Signal(
        "story.investor_plan",
        "story",
        "Business plan written for investors",
        "step",
        by_stage(1, 1, 1, 1, 1, 1),
        "/business-builder/plan",
        _investor_plan,
        lambda v, t: "Generate a business plan with investors as the audience.",
    ),
    Signal(
        "story.one_pager",
        "story",
        "One-pager",
        "step",
        by_stage(1, 1, 1, 1, 1, 1),
        "/documents",
        lambda db, s: _doc_exists(db, s, DocumentKind.one_pager),
        lambda v, t: "You have no one-pager. It is what an investor forwards.",
    ),
)
SIGNALS_BY_KEY = {s.key: s for s in SIGNALS}


def read_signals(db: Session, startup: Startup) -> list[Reading]:
    """Every applicable signal, scored against its stage target."""
    out: list[Reading] = []
    for sig in SIGNALS:
        value = sig.read(db, startup)
        if value is None:
            continue
        target = sig.target(startup.stage)
        score = signal_score(value, target)
        out.append(
            Reading(
                key=sig.key,
                dimension=sig.dimension,
                label=sig.label,
                unit=sig.unit,
                weight=sig.weight,
                value=value,
                target=target,
                score=score,
                href=sig.href,
                note=sig.note(value, target) if score < 100 else "",
            )
        )
    return out


# ---- Must-haves (D-IR-2) -------------------------------------------------------------------


def must_haves(db: Session, startup: Startup) -> list[dict[str, Any]]:
    """The three things no investor conversation starts without. Until they exist the overall is
    capped at 69. The deck must-have needs a finished deck review (slice B), not just a file."""
    revenue = (_monthly_revenue(db, startup) or 0) > 0
    validated = _count(
        db,
        Assumption,
        Assumption.startup_id == startup.id,
        Assumption.status == AssumptionStatus.validated,
    )
    from app.db.models.validation import Interview

    interviews = _count(db, Interview, Interview.startup_id == startup.id)
    proof = (
        revenue
        or _won_deals(db, startup) > 0
        or (validated >= 1 and (_signups(db, startup) >= 10 or interviews >= 5))
    )
    items = [
        {
            "key": "financial_model",
            "label": "A financial model",
            "met": _latest_model(db, startup) is not None,
            "href": "/finance/model",
            "reason": "Generate a financial model in Finance.",
        },
        {
            # D-IR-2: "a reviewed deck": a finished deck review, not just a file.
            "key": "pitch_deck_reviewed",
            "label": "A reviewed pitch deck",
            "met": _deck_reviewed(db, startup),
            "href": "/investor-readiness/deck",
            "reason": "Upload your pitch deck PDF and run a deck review.",
        },
        {
            "key": "traction_proof",
            "label": "One proof point: revenue, a won deal, or a validated assumption with demand",
            "met": proof,
            "href": "/validation",
            "reason": "Record revenue, win a deal, or validate an assumption with real demand.",
        },
    ]
    for item in items:
        if item["met"]:
            item["reason"] = None
    return items

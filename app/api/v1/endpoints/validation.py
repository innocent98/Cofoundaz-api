from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError
from app.core.rate_limit import limiter
from app.db.models.enums import (
    AssumptionStatus,
    ExperimentStatus,
    ExperimentType,
    InterviewVerdict,
    MembershipRole,
    RiskLevel,
)
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.platform.jobs import job_dispatcher
from app.schemas.validation import (
    AssumptionCreate,
    AssumptionUpdate,
    ExperimentCreate,
    ExperimentUpdate,
    FeedbackCreate,
    FeedbackUpdate,
    InterviewCreate,
    InterviewUpdate,
    PageEvent,
    PageSignup,
    ResponseSubmit,
    ScriptsGenerate,
    ScriptUpdate,
    SendToRoadmap,
    SurveyCreate,
    SurveyUpdate,
    SynthesizeRequest,
)
from app.services.validation import feedback, insights, smoke
from app.services.validation.public import open_survey, public_view, submit_response
from app.services.validation.service import (
    create_assumption,
    create_experiment,
    create_interview,
    create_survey,
    delete_assumption,
    delete_experiment,
    delete_interview,
    delete_survey,
    get_assumption,
    get_experiment,
    get_interview,
    get_survey,
    list_assumptions,
    list_experiments,
    list_interviews,
    list_surveys,
    open_answers,
    overview,
    response_counts,
    rotate_share_link,
    serialize_assumptions,
    serialize_experiment,
    serialize_interview,
    serialize_survey,
    smoke_test_stats,
    smoke_test_waitlist,
    survey_analytics,
    update_assumption,
    update_experiment,
    update_interview,
    update_survey,
)

router = APIRouter()
# Founders and team members only (spec D6, PRD line 457).
_member = require_role(MembershipRole.founder, MembershipRole.team_member)


# --- overview (PRD 09.1) ---------------------------------------------------------------------


@router.get("/overview")
def overview_endpoint(
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Stat strip + the riskiest untested assumption for the header nudge."""
    return success_response(overview(db, membership.startup_id))


# --- assumptions ---------------------------------------------------------------------------


@router.get("/assumptions")
def list_assumptions_endpoint(
    status: AssumptionStatus | None = None,
    risk: RiskLevel | None = None,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_assumptions(db, membership.startup_id, status=status, risk=risk)
    return success_response({"assumptions": serialize_assumptions(db, membership.startup_id, rows)})


@router.post("/assumptions", status_code=status.HTTP_201_CREATED)
def create_assumption_endpoint(
    body: AssumptionCreate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = create_assumption(
        db, membership.startup_id, statement=body.statement, risk=body.risk, status=body.status
    )
    db.commit()
    return success_response(serialize_assumptions(db, membership.startup_id, [row])[0])


@router.patch("/assumptions/{assumption_id}")
def update_assumption_endpoint(
    assumption_id: str,
    body: AssumptionUpdate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = get_assumption(db, membership.startup_id, assumption_id)
    update_assumption(
        db,
        row,
        actor_id=membership.user_id,
        statement=body.statement,
        risk=body.risk,
        status=body.status,
    )
    db.commit()
    return success_response(serialize_assumptions(db, membership.startup_id, [row])[0])


# --- experiments ---------------------------------------------------------------------------


def _commit_slug(db: Session) -> None:
    """Commit; two saves racing for the same slug land on the unique constraint: 409, not 500."""
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError("SLUG_TAKEN", "That page address is taken. Try another.", 409) from exc


@router.get("/experiments")
def list_experiments_endpoint(
    type: ExperimentType | None = None,
    status: ExperimentStatus | None = None,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_experiments(db, membership.startup_id, type=type, status=status)
    return success_response({"experiments": [serialize_experiment(row) for row in rows]})


@router.post("/experiments", status_code=status.HTTP_201_CREATED)
def create_experiment_endpoint(
    body: ExperimentCreate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = create_experiment(
        db,
        membership.startup_id,
        name=body.name,
        type=body.type,
        config=body.config,
        status=body.status,
        metrics=body.metrics,
        assumption_ids=body.assumption_ids,
    )
    _commit_slug(db)
    return success_response(serialize_experiment(row))


@router.patch("/experiments/{experiment_id}")
def update_experiment_endpoint(
    experiment_id: str,
    body: ExperimentUpdate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = get_experiment(db, membership.startup_id, experiment_id)
    update_experiment(
        db,
        row,
        name=body.name,
        type=body.type,
        config=body.config,
        status=body.status,
        metrics=body.metrics,
        assumption_ids=body.assumption_ids,
    )
    _commit_slug(db)
    return success_response(serialize_experiment(row))


@router.get("/smoke-tests/{experiment_id}/signups")
def smoke_test_signups_endpoint(
    experiment_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """The waitlist: emails visitors typed on the hosted page, oldest first."""
    return success_response(
        {"signups": smoke_test_waitlist(db, membership.startup_id, experiment_id)}
    )


@router.get("/smoke-tests/{experiment_id}/stats")
def smoke_test_stats_endpoint(
    experiment_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(smoke_test_stats(db, membership.startup_id, experiment_id))


# --- interviews ----------------------------------------------------------------------------


@router.get("/interviews")
def list_interviews_endpoint(
    segment: str | None = None,
    verdict: InterviewVerdict | None = None,
    assumption_id: str | None = None,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_interviews(
        db, membership.startup_id, segment=segment, verdict=verdict, assumption_id=assumption_id
    )
    return success_response({"interviews": [serialize_interview(row) for row in rows]})


@router.post("/interviews", status_code=status.HTTP_201_CREATED)
def create_interview_endpoint(
    body: InterviewCreate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = create_interview(
        db,
        membership.startup_id,
        interviewee=body.interviewee,
        held_on=body.held_on,
        verdict=body.verdict,
        segment=body.segment,
        notes=body.notes,
        key_quotes=body.key_quotes,
        assumption_ids=body.assumption_ids,
    )
    db.commit()
    return success_response(serialize_interview(row))


@router.patch("/interviews/{interview_id}")
def update_interview_endpoint(
    interview_id: str,
    body: InterviewUpdate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = get_interview(db, membership.startup_id, interview_id)
    update_interview(
        db,
        row,
        interviewee=body.interviewee,
        held_on=body.held_on,
        verdict=body.verdict,
        segment=body.segment,
        notes=body.notes,
        key_quotes=body.key_quotes,
        assumption_ids=body.assumption_ids,
    )
    db.commit()
    return success_response(serialize_interview(row))


# --- surveys (members) ---------------------------------------------------------------------


@router.get("/surveys")
def list_surveys_endpoint(
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_surveys(db, membership.startup_id)
    counts = response_counts(db, membership.startup_id, rows)
    return success_response(
        {"surveys": [serialize_survey(row, counts[str(row.id)]) for row in rows]}
    )


@router.post("/surveys", status_code=status.HTTP_201_CREATED)
def create_survey_endpoint(
    body: SurveyCreate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = create_survey(db, membership.startup_id, title=body.title, questions=body.questions)
    db.commit()
    return success_response(serialize_survey(row, 0))


@router.patch("/surveys/{survey_id}")
def update_survey_endpoint(
    survey_id: str,
    body: SurveyUpdate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """``public_token`` carries the raw token the first time the survey is opened, and is null
    every other time. It is never recoverable afterwards (spec section 4)."""
    row = get_survey(db, membership.startup_id, survey_id)
    row, raw = update_survey(
        db, row, title=body.title, questions=body.questions, status=body.status
    )
    db.commit()
    counts = response_counts(db, membership.startup_id, [row])
    return success_response(
        {"survey": serialize_survey(row, counts[str(row.id)]), "public_token": raw}
    )


@router.get("/surveys/{survey_id}/analytics")
def survey_analytics_endpoint(
    survey_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(survey_analytics(db, membership.startup_id, survey_id))


@router.get("/surveys/{survey_id}/answers")
def survey_open_answers_endpoint(
    survey_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Every non-blank answer to each open question, newest first (gap row 9)."""
    return success_response(open_answers(db, membership.startup_id, survey_id))


@router.post("/surveys/{survey_id}/share-link")
def rotate_share_link_endpoint(
    survey_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """A fresh share link for an open survey; the previous link stops working (gap row 9)."""
    raw = rotate_share_link(db, get_survey(db, membership.startup_id, survey_id))
    db.commit()
    return success_response({"public_token": raw})


# --- surveys (public, no authentication at all) ---------------------------------------------


@router.get("/surveys/{token}")
# Token routes share one bucket per caller across every token, so tokens cannot be guessed freely.
@limiter.shared_limit("20/minute", scope="public-survey-read")
def public_survey_endpoint(
    request: Request,
    token: str,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """The form a respondent fills in. Title and questions only (spec section 4)."""
    return success_response(public_view(open_survey(db, token)))


@router.post("/surveys/{token}/responses", status_code=status.HTTP_201_CREATED)
@limiter.shared_limit("20/minute", scope="public-survey-respond")
def submit_survey_response_endpoint(
    request: Request,
    token: str,
    body: ResponseSubmit,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Anonymous submission. The reply is an acknowledgement and nothing else."""
    submit_response(db, token, body.answers)
    db.commit()
    return success_response({"received": True})


# --- smoke-test pages (public, no authentication; D-VAL-1, served at /p/{slug}) ----------------


@router.get("/pages/{slug}")
@limiter.limit("60/minute")
def public_page_endpoint(
    request: Request,
    slug: str,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """What the visitor sees. 404 unless the test is live."""
    return success_response(smoke.page_view(smoke.open_page(db, slug)))


@router.post("/pages/{slug}/events", status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
def page_event_endpoint(
    request: Request,
    slug: str,
    body: PageEvent,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    smoke.record_event(db, slug, body.kind, body.visitor_id)
    db.commit()
    return success_response({"received": True})


@router.post("/pages/{slug}/signups", status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
def page_signup_endpoint(
    request: Request,
    slug: str,
    body: PageSignup,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Join the waitlist. Same reply whether or not the email was already on it."""
    out = smoke.record_signup(db, slug, str(body.email), body.visitor_id)
    db.commit()
    return success_response(out)


# --- deletes (gap row 14) ----------------------------------------------------------------------


@router.delete("/assumptions/{assumption_id}")
def delete_assumption_endpoint(
    assumption_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    delete_assumption(db, membership.startup_id, assumption_id)
    db.commit()
    return success_response({"deleted": True})


@router.delete("/experiments/{experiment_id}")
def delete_experiment_endpoint(
    experiment_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    delete_experiment(db, membership.startup_id, experiment_id)
    db.commit()
    return success_response({"deleted": True})


@router.delete("/interviews/{interview_id}")
def delete_interview_endpoint(
    interview_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    delete_interview(db, membership.startup_id, interview_id)
    db.commit()
    return success_response({"deleted": True})


@router.delete("/surveys/{survey_id}")
def delete_survey_endpoint(
    survey_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    delete_survey(db, membership.startup_id, survey_id)
    db.commit()
    return success_response({"deleted": True})


# --- AI: "Design experiment" for an assumption (PRD 09.6 nudge), never auto-saved ------------


@router.post("/assumptions/{assumption_id}/experiment-draft", status_code=status.HTTP_202_ACCEPTED)
def experiment_draft_endpoint(
    assumption_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Queue an AI smoke-test draft for this assumption. The draft comes back in the job's
    `result`; nothing is created until the founder saves it through the normal builder."""
    row = get_assumption(db, membership.startup_id, assumption_id)
    job = job_dispatcher.enqueue(
        db,
        "validation.experiment.draft",
        {"startup_id": str(membership.startup_id), "assumption_id": str(row.id)},
        membership.startup_id,
    )
    db.commit()
    return success_response({"job_id": str(job.id), "status": job.status.value})


# --- interview scripts (PRD 09.4, gap row 7) --------------------------------------------------


@router.post("/scripts/generate", status_code=status.HTTP_202_ACCEPTED)
def generate_scripts_endpoint(
    body: ScriptsGenerate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Queue an AI interview script for the chosen assumptions. The worker saves it; the job's
    `result.script_id` points at the new script."""
    # Same workspace check the write endpoints apply, so the job never carries an
    # assumption id from another workspace (or a repeat) into its prompt.
    assumption_ids = insights.script_assumption_ids(db, membership.startup_id, body.assumption_ids)
    job = job_dispatcher.enqueue(
        db,
        "validation.scripts.generate",
        {"startup_id": str(membership.startup_id), "assumption_ids": assumption_ids},
        membership.startup_id,
    )
    db.commit()
    return success_response({"job_id": str(job.id), "status": job.status.value})


@router.get("/scripts")
def list_scripts_endpoint(
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = insights.list_scripts(db, membership.startup_id)
    return success_response({"scripts": [insights.serialize_script(row) for row in rows]})


@router.get("/scripts/{script_id}")
def get_script_endpoint(
    script_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        insights.serialize_script(insights.get_script(db, membership.startup_id, script_id))
    )


@router.patch("/scripts/{script_id}")
def update_script_endpoint(
    script_id: str,
    body: ScriptUpdate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = insights.get_script(db, membership.startup_id, script_id)
    insights.update_script(
        db, row, title=body.title, questions=body.questions, assumption_ids=body.assumption_ids
    )
    db.commit()
    return success_response(insights.serialize_script(row))


@router.delete("/scripts/{script_id}")
def delete_script_endpoint(
    script_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    insights.delete_script(db, membership.startup_id, script_id)
    db.commit()
    return success_response({"deleted": True})


# --- Insight Synthesizer (gap rows 12, 13) -----------------------------------------------------


@router.post("/synthesize", status_code=status.HTTP_202_ACCEPTED)
def synthesize_endpoint(
    body: SynthesizeRequest | None = None,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Queue a synthesis over interviews, open survey answers, or both (no body = both). The
    job's `result.insight_id` points at the stored report."""
    source = body.source if body else "all"
    insights.require_inputs(db, membership.startup_id, source)
    job = job_dispatcher.enqueue(
        db,
        "validation.synthesize",
        {"startup_id": str(membership.startup_id), "source": source},
        membership.startup_id,
    )
    db.commit()
    return success_response({"job_id": str(job.id), "status": job.status.value})


@router.get("/insights")
def list_insights_endpoint(
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = insights.list_reports(db, membership.startup_id)
    return success_response({"insights": [insights.serialize_report(row) for row in rows]})


@router.get("/insights/{insight_id}")
def get_insight_endpoint(
    insight_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        insights.serialize_report(insights.get_report(db, membership.startup_id, insight_id))
    )


@router.post("/insights/{insight_id}/suggestions/{suggestion_id}/apply")
def apply_insight_suggestion_endpoint(
    insight_id: str,
    suggestion_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Move the assumption to the suggested status. 409 ALREADY_APPLIED on a second click."""
    out = insights.apply_suggestion(
        db, membership.startup_id, insight_id, suggestion_id, actor_id=membership.user_id
    )
    db.commit()
    return success_response(out)


@router.delete("/insights/{insight_id}")
def delete_insight_endpoint(
    insight_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    insights.delete_report(db, membership.startup_id, insight_id)
    db.commit()
    return success_response({"deleted": True})


# --- MVP feedback (PRD 09.7, gap row 11, D-VAL-2/3) ------------------------------------------


@router.get("/feedback")
def list_feedback_endpoint(
    source: Literal["interview", "survey", "in_app", "email"] | None = None,
    include_dismissed: bool = False,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """The inbox. Interview quotes and open survey answers are mirrored in on every read."""
    feedback.sync_imports(db, membership.startup_id)
    rows = feedback.list_items(
        db, membership.startup_id, source=source, include_dismissed=include_dismissed
    )
    out = {
        "items": [feedback.serialize_item(row) for row in rows],
        "counts": feedback.source_counts(db, membership.startup_id),
    }
    db.commit()
    return success_response(out)


@router.post("/feedback", status_code=status.HTTP_201_CREATED)
def create_feedback_endpoint(
    body: FeedbackCreate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = feedback.create_item(
        db,
        membership.startup_id,
        text=body.text,
        source=body.source,
        received_on=body.received_on,
        created_by=membership.user_id,
    )
    db.commit()
    return success_response(feedback.serialize_item(row))


@router.patch("/feedback/{item_id}")
def update_feedback_endpoint(
    item_id: str,
    body: FeedbackUpdate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Manual entries only; imported ones are edited at their source (422 IMPORTED_FEEDBACK)."""
    row = feedback.get_item(db, membership.startup_id, item_id)
    feedback.update_item(db, row, text=body.text, source=body.source, received_on=body.received_on)
    db.commit()
    return success_response(feedback.serialize_item(row))


@router.delete("/feedback/{item_id}")
def delete_feedback_endpoint(
    item_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """`{"deleted": true}` for a manual entry, `{"dismissed": true}` for an imported one."""
    out = feedback.delete_item(db, feedback.get_item(db, membership.startup_id, item_id))
    db.commit()
    return success_response(out)


@router.post("/feedback/{item_id}/restore")
def restore_feedback_endpoint(
    item_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = feedback.restore_item(db, feedback.get_item(db, membership.startup_id, item_id))
    db.commit()
    return success_response(feedback.serialize_item(row))


@router.post("/feedback/cluster", status_code=status.HTTP_202_ACCEPTED)
def cluster_feedback_endpoint(
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Queue AI theme clustering over the inbox; `result.run_id` when done."""
    feedback.sync_imports(db, membership.startup_id)
    if not feedback.list_items(db, membership.startup_id):
        db.commit()
        raise AppError(
            "NOTHING_TO_CLUSTER", "Add some feedback first, or record an interview quote.", 422
        )
    job = job_dispatcher.enqueue(
        db,
        "validation.feedback.cluster",
        {"startup_id": str(membership.startup_id)},
        membership.startup_id,
    )
    db.commit()
    return success_response({"job_id": str(job.id), "status": job.status.value})


@router.get("/feedback/themes")
def feedback_themes_endpoint(
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """The newest clustering run: "{theme} mentioned {n}x across {m} sources" with drill-in items."""
    return success_response(feedback.themes_view(db, membership.startup_id))


@router.post("/feedback/themes/{theme_id}/send-to-roadmap", status_code=status.HTTP_201_CREATED)
def send_theme_to_roadmap_endpoint(
    theme_id: str,
    body: SendToRoadmap | None = None,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Add the theme to the roadmap as one task (chosen milestone, else the first open one)."""
    out = feedback.send_to_roadmap(
        db, membership.startup_id, theme_id, milestone_id=body.milestone_id if body else None
    )
    db.commit()
    return success_response(out)

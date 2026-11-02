"""Validation Hub slice (b): interview scripts (PRD 09.4) and the Insight Synthesizer.

Services flush; the endpoints commit. The AI drafting lives in the worker
(`app/worker/handlers/validation.py`); this module stores, edits and reads what it produced, and
gathers what the synthesizer is allowed to read: interview segments, notes, quotes and verdicts
plus open survey answers. Interviewee names and closed survey answers never leave the database.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import AssumptionStatus
from app.db.models.validation import (
    Assumption,
    FeedbackItem,
    InsightReport,
    Interview,
    InterviewScript,
    Survey,
    SurveyResponse,
)
from app.services.validation.service import (
    get_assumption,
    link_ids,
    serialize_assumptions,
    update_assumption,
)

MAX_SCRIPT_ASSUMPTIONS = 5
MAX_SCRIPT_QUESTIONS = 30
MAX_QUESTION_CHARS = 500
SOURCES = ("interviews", "surveys", "feedback", "all")
MAX_FEEDBACK = 200
MAX_INTERVIEWS = 30
NOTE_CHARS = 1500
MAX_OPEN_ANSWERS = 200
ANSWER_CHARS = 500


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _invalid(message: str) -> AppError:
    return AppError("VALIDATION_ERROR", message, 422)


# --- interview scripts -------------------------------------------------------------------------


def script_assumption_ids(db: Session, startup_id: uuid.UUID, assumption_ids: Any) -> list[str]:
    ids = link_ids(db, startup_id, assumption_ids)
    if not ids:
        raise AppError(
            "ASSUMPTIONS_REQUIRED", "Pick at least one assumption to write a script for.", 422
        )
    if len(ids) > MAX_SCRIPT_ASSUMPTIONS:
        raise _invalid(f"A script can cover at most {MAX_SCRIPT_ASSUMPTIONS} assumptions.")
    return ids


def clean_questions(questions: Any, *, limit: int = MAX_SCRIPT_QUESTIONS) -> list[dict[str, str]]:
    """Validate an edited question list, keeping existing ids so edits never reshuffle them."""
    if not isinstance(questions, list):
        raise _invalid("Questions must be a list.")
    if not questions:
        raise _invalid("A script needs at least one question.")
    if len(questions) > limit:
        raise _invalid(f"A script may have at most {limit} questions.")
    out: list[dict[str, str]] = []
    for item in questions:
        if not isinstance(item, dict):
            raise _invalid("Each question must be an object.")
        question = str(item.get("question") or "").strip()
        tip = str(item.get("tip") or "").strip()
        if not question:
            raise _invalid("Each question needs text.")
        if len(question) > MAX_QUESTION_CHARS or len(tip) > MAX_QUESTION_CHARS:
            raise _invalid(f"Questions and tips are limited to {MAX_QUESTION_CHARS} characters.")
        qid = str(item.get("id") or "").strip()[:40] or _new_id()
        out.append({"id": qid, "question": question, "tip": tip})
    return out


def create_script(
    db: Session,
    startup_id: uuid.UUID,
    *,
    title: str,
    assumption_ids: list[str],
    questions: list[dict[str, str]],
) -> InterviewScript:
    row = InterviewScript(
        startup_id=startup_id,
        title=title.strip()[:255] or "Interview script",
        assumption_ids=assumption_ids,
        questions=questions,
    )
    db.add(row)
    db.flush()
    return row


def list_scripts(db: Session, startup_id: uuid.UUID) -> list[InterviewScript]:
    return (
        db.query(InterviewScript)
        .filter_by(startup_id=startup_id)
        .order_by(InterviewScript.created_at.desc(), InterviewScript.id.desc())
        .all()
    )


def get_script(db: Session, startup_id: uuid.UUID, script_id: Any) -> InterviewScript:
    row = db.query(InterviewScript).filter_by(id=script_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def update_script(
    db: Session,
    script: InterviewScript,
    *,
    title: str | None = None,
    questions: Any = None,
    assumption_ids: Any = None,
) -> InterviewScript:
    if title is not None:
        script.title = title.strip()
    if questions is not None:
        script.questions = clean_questions(questions)
    if assumption_ids is not None:
        script.assumption_ids = link_ids(db, script.startup_id, assumption_ids)
    db.flush()
    return script


def delete_script(db: Session, startup_id: uuid.UUID, script_id: Any) -> None:
    db.delete(get_script(db, startup_id, script_id))
    db.flush()


def serialize_script(script: InterviewScript) -> dict[str, Any]:
    return {
        "id": str(script.id),
        "title": script.title,
        "assumption_ids": script.assumption_ids,
        "questions": script.questions,
        "created_at": script.created_at.isoformat(),
        "updated_at": script.updated_at.isoformat(),
    }


# --- Insight Synthesizer: inputs -------------------------------------------------------------


def gather_inputs(db: Session, startup_id: uuid.UUID, source: str) -> dict[str, Any]:
    """What the synthesizer may read for `source`. No interviewee names, no closed answers.
    `feedback` is the MVP inbox's manual entries (imported ones are the interviews and answers)."""
    interviews: list[dict[str, Any]] = []
    open_answers: list[dict[str, str]] = []
    feedback: list[dict[str, str]] = []
    if source in ("feedback", "all"):
        feedback = [
            {"source": row.source, "text": row.text[:ANSWER_CHARS]}
            for row in db.query(FeedbackItem)
            .filter(
                FeedbackItem.startup_id == startup_id,
                FeedbackItem.origin_key.is_(None),
                FeedbackItem.dismissed.is_(False),
            )
            .order_by(FeedbackItem.received_on.desc(), FeedbackItem.created_at.desc())
            .limit(MAX_FEEDBACK)
        ]
    if source in ("interviews", "all"):
        statements = {
            str(a.id): a.statement for a in db.query(Assumption).filter_by(startup_id=startup_id)
        }
        for row in (
            db.query(Interview)
            .filter_by(startup_id=startup_id)
            .order_by(Interview.held_on.desc(), Interview.id.desc())
            .limit(MAX_INTERVIEWS)
        ):
            interviews.append(
                {
                    "segment": row.segment or "unspecified",
                    "verdict": row.verdict.value,
                    "notes": (row.notes or "").strip()[:NOTE_CHARS],
                    "quotes": [str(q).strip() for q in (row.key_quotes or []) if str(q).strip()],
                    "assumptions": [
                        statements[x] for x in (row.assumption_ids or []) if x in statements
                    ],
                }
            )
    if source in ("surveys", "all"):
        surveys = db.query(Survey).filter_by(startup_id=startup_id).all()
        prompts = {
            question["id"]: question["prompt"]
            for survey in surveys
            for question in (survey.questions or [])
            if question.get("type") == "open"
        }
        if prompts:
            responses = (
                db.query(SurveyResponse)
                .filter_by(startup_id=startup_id)
                .order_by(SurveyResponse.submitted_at.desc())
            )
            for response in responses:
                for key, value in (response.answers or {}).items():
                    if key in prompts and isinstance(value, str) and value.strip():
                        open_answers.append(
                            {"question": prompts[key], "answer": value.strip()[:ANSWER_CHARS]}
                        )
                if len(open_answers) >= MAX_OPEN_ANSWERS:
                    break
            open_answers = open_answers[:MAX_OPEN_ANSWERS]
    return {"interviews": interviews, "open_answers": open_answers, "feedback": feedback}


def has_inputs(inputs: dict[str, Any]) -> bool:
    return bool(inputs["interviews"] or inputs["open_answers"] or inputs["feedback"])


def require_inputs(db: Session, startup_id: uuid.UUID, source: str) -> None:
    if not has_inputs(gather_inputs(db, startup_id, source)):
        raise AppError(
            "NOTHING_TO_SYNTHESIZE",
            "Add an interview, collect an open survey answer or log some feedback first.",
            422,
        )


# --- Insight Synthesizer: reports --------------------------------------------------------------


def create_report(
    db: Session,
    startup_id: uuid.UUID,
    *,
    source: str,
    inputs: dict[str, int],
    patterns: list[dict[str, Any]],
    contradictions: list[dict[str, Any]],
    suggestions: list[dict[str, Any]],
) -> InsightReport:
    row = InsightReport(
        startup_id=startup_id,
        source=source,
        inputs=inputs,
        patterns=[{"id": _new_id(), **p} for p in patterns],
        contradictions=[{"id": _new_id(), **c} for c in contradictions],
        suggestions=[{"id": _new_id(), **s, "applied_at": None} for s in suggestions],
    )
    db.add(row)
    db.flush()
    return row


def list_reports(db: Session, startup_id: uuid.UUID) -> list[InsightReport]:
    return (
        db.query(InsightReport)
        .filter_by(startup_id=startup_id)
        .order_by(InsightReport.created_at.desc(), InsightReport.id.desc())
        .all()
    )


def get_report(
    db: Session, startup_id: uuid.UUID, report_id: Any, *, lock: bool = False
) -> InsightReport:
    query = db.query(InsightReport).filter_by(id=report_id, startup_id=startup_id)
    row = (query.with_for_update() if lock else query).first()
    if row is None:
        raise NotFound()
    return row


def delete_report(db: Session, startup_id: uuid.UUID, report_id: Any) -> None:
    db.delete(get_report(db, startup_id, report_id))
    db.flush()


def serialize_report(report: InsightReport) -> dict[str, Any]:
    return {
        "id": str(report.id),
        "source": report.source,
        "inputs": report.inputs,
        "patterns": report.patterns,
        "contradictions": report.contradictions,
        "suggestions": report.suggestions,
        "created_at": report.created_at.isoformat(),
    }


def apply_suggestion(
    db: Session, startup_id: uuid.UUID, report_id: Any, suggestion_id: str, *, actor_id: uuid.UUID
) -> dict[str, Any]:
    """Move the assumption to the suggested status, once. The report row is locked so two
    clicks can't both apply; `update_assumption` publishes validated/invalidated as usual."""
    report = get_report(db, startup_id, report_id, lock=True)
    suggestions = [dict(s) for s in report.suggestions]
    target = next((s for s in suggestions if s.get("id") == suggestion_id), None)
    if target is None:
        raise NotFound()
    if target.get("applied_at"):
        raise AppError("ALREADY_APPLIED", "This suggestion was already applied.", 409)
    assumption = get_assumption(db, startup_id, target["assumption_id"])
    update_assumption(
        db, assumption, actor_id=actor_id, status=AssumptionStatus(target["to_status"])
    )
    target["applied_at"] = datetime.now(UTC).isoformat()
    report.suggestions = suggestions
    db.flush()
    return {
        "insight": serialize_report(report),
        "assumption": serialize_assumptions(db, startup_id, [assumption])[0],
    }

"""The public survey surface (Module 09, spec section 4).

Addressed by an unguessable token whose SHA-256 hash alone is stored, exactly like the Module 18
share and signing links (``app/services/documents/shares.py``). Unknown, draft and closed surveys
all raise the same ``NotFound``, so a token cannot be probed, and nothing returned here mentions
the workspace, its members, or anyone else's answers.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import SurveyStatus
from app.db.models.validation import Survey, SurveyResponse
from app.services.auth.sessions import hash_token
from app.services.validation.questions import validate_answers


def open_survey(db: Session, token: str) -> Survey:
    survey = db.query(Survey).filter_by(token_hash=hash_token(token)).first()
    # Uniform 404 for unknown / draft / closed — never reveal which one it was.
    if survey is None or survey.status != SurveyStatus.open:
        raise NotFound()
    return survey


def public_view(survey: Survey) -> dict[str, Any]:
    """Exactly what a respondent may see: the title and the questions.

    Nothing else — no ids of the survey or workspace, no status, no counts.
    """
    return {
        "title": survey.title,
        "questions": [
            {
                "id": question["id"],
                "type": question["type"],
                "prompt": question["prompt"],
                "required": question["required"],
                **({"options": question["options"]} if question["type"] == "choice" else {}),
            }
            for question in survey.questions or []
        ],
    }


def submit_response(db: Session, token: str, answers: Any) -> SurveyResponse:
    """Record one anonymous response.

    Repeats are allowed; nothing about the respondent is stored beyond the moment they answered
    (spec D3).
    """
    survey = open_survey(db, token)
    row = SurveyResponse(
        survey_id=survey.id,
        startup_id=survey.startup_id,
        answers=validate_answers(survey.questions or [], answers),
        submitted_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row

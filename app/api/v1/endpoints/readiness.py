"""Investor Readiness (PRD Module 15). Slice A: score, recompute, history, market-size inputs and
the share-safe snapshot Funding reads. Founder and team member read; only the founder writes."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.core.rate_limit import limiter
from app.db.models.enums import READINESS_READ_ROLES, READINESS_WRITE_ROLES
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.platform.signed_urls import InvalidSignedToken, verify_key
from app.schemas.readiness import (
    DeckReviewIn,
    NarrativeIn,
    QaMessageIn,
    QaStartIn,
    ReadinessInputsIn,
)
from app.services.readiness import deck, narrative, qa, service

router = APIRouter()
_read = require_role(*READINESS_READ_ROLES)
_write = require_role(*READINESS_WRITE_ROLES)


def _startup(db: Session, membership: Membership) -> Startup:
    s = db.get(Startup, membership.startup_id)
    if s is None:
        raise NotFound()
    return s


@router.get("/score")
def get_score(
    membership: Membership = Depends(_read),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(service.get_score(db, _startup(db, membership)))


@router.post("/score/recompute")
def recompute(
    membership: Membership = Depends(_write),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(service.force_recompute(db, _startup(db, membership)))


@router.get("/history")
def get_history(
    range: int = Query(90, description="Days: 30, 90 or 365"),  # noqa: A002, B008
    membership: Membership = Depends(_read),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(service.history(db, _startup(db, membership), range))


@router.get("/snapshot")
def get_snapshot(
    membership: Membership = Depends(_read),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(service.snapshot(db, _startup(db, membership)))


@router.get("/inputs")
def get_inputs(
    membership: Membership = Depends(_read),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(service.get_inputs(db, membership.startup_id))


@router.put("/inputs")
def put_inputs(
    body: ReadinessInputsIn,
    membership: Membership = Depends(_write),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    if data.get("source_url") is not None:
        data["source_url"] = str(data["source_url"])
    data = {k: v for k, v in data.items() if not (k == "currency" and v is None)}
    return success_response(service.put_inputs(db, _startup(db, membership), user.id, data))


# ---- Pitch deck review (PRD 15.2) ----------------------------------------------------------


def _public_url() -> str:
    return settings.API_PUBLIC_URL or settings.SERVER_HOST


@router.post("/deck-reviews", status_code=status.HTTP_202_ACCEPTED)
def create_deck_review(
    body: DeckReviewIn,
    membership: Membership = Depends(_write),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    """Queue a review of a Documents PDF. The same bytes already reviewed return that review with
    `deduplicated: true` and spend nothing (HTTP 200); otherwise 202 with the job to poll."""
    review, created = deck.create_review(db, _startup(db, membership), user.id, body.file_id)
    db.commit()
    payload = {"review": deck.summary(review), "deduplicated": not created}
    resp = success_response(payload)
    if created:
        return resp
    return JSONResponse(status_code=200, content=resp)


@router.get("/deck-reviews")
def list_deck_reviews(
    membership: Membership = Depends(_read),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = deck.list_reviews(db, membership.startup_id)
    return success_response({"reviews": [deck.summary(r) for r in rows]})


@router.get("/deck-reviews/{review_id}")
def get_deck_review(
    review_id: uuid.UUID,
    membership: Membership = Depends(_read),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    r = deck.get_review(db, membership.startup_id, review_id)
    return success_response(deck.detail(r, _public_url()))


@router.get("/deck-thumbnails", response_class=FileResponse)
@limiter.shared_limit("120/minute", scope="public-deck-thumb")
def deck_thumbnail(
    request: Request,
    token: str = Query(..., min_length=1, max_length=2000),  # noqa: B008
) -> FileResponse:
    """Serves a locally stored slide thumbnail to the holder of a valid, unexpired signed link
    (issued inside `GET /deck-reviews/{id}` after the role check). Anything else is a 404."""
    try:
        claim = verify_key(token)
    except InvalidSignedToken:
        raise NotFound() from None
    path = deck.thumbnail_path(claim.startup_id, claim.key)
    if path is None:
        raise NotFound()
    return FileResponse(
        path,
        media_type="image/png",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


# ---- Mock Q&A (PRD 15.3, D-IR-7) -----------------------------------------------------------
# Founder: everything. Team member: ratings and counts only (no transcript, no scorecard text,
# no way to start or answer). Every other role: 403.


@router.post("/qa-sessions", status_code=status.HTTP_201_CREATED)
def start_qa_session(
    body: QaStartIn,
    membership: Membership = Depends(_write),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    s = qa.start_session(db, _startup(db, membership), user.id, body.focus, body.intensity)
    db.commit()
    return success_response({"session": qa.detail(s), "reply": s.transcript[-1]["text"]})


@router.post("/qa-sessions/{session_id}/messages")
def answer_qa_session(
    session_id: uuid.UUID,
    body: QaMessageIn,
    membership: Membership = Depends(_write),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    s = qa.get_for_update(db, membership.startup_id, session_id)
    result = qa.answer(db, startup, s, body.content, body.expected_turn)
    db.commit()
    return success_response({"session": qa.summary(s), **result})


@router.post("/qa-sessions/{session_id}/end")
def end_qa_session(
    session_id: uuid.UUID,
    membership: Membership = Depends(_write),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    s = qa.get_for_update(db, membership.startup_id, session_id)
    qa.end_session(db, startup, s)
    db.commit()
    return success_response({"session": qa.summary(s)})


@router.get("/qa-sessions")
def list_qa_sessions(
    membership: Membership = Depends(_read),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = qa.list_sessions(db, membership.startup_id)
    return success_response({"sessions": [qa.summary(r) for r in rows]})


@router.get("/qa-sessions/{session_id}")
def get_qa_session(
    session_id: uuid.UUID,
    membership: Membership = Depends(_read),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    s = qa.get_session(db, membership.startup_id, session_id)
    full = membership.role in READINESS_WRITE_ROLES
    return success_response(qa.detail(s) if full else qa.summary(s))


# ---- Financial narrative (PRD 15.4) --------------------------------------------------------
# Founder edits and runs the check; team member reads the draft and the latest check.


@router.get("/narrative")
def get_narrative(
    membership: Membership = Depends(_read),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(narrative.draft_view(db, membership.startup_id))


@router.put("/narrative")
def put_narrative(
    body: NarrativeIn,
    membership: Membership = Depends(_write),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    narrative.put_draft(db, _startup(db, membership), user.id, body.body)
    db.commit()
    return success_response(narrative.draft_view(db, membership.startup_id))


@router.post("/narrative/check", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("10/minute")
def check_narrative(
    request: Request,  # noqa: ARG001 - required by the rate limiter
    membership: Membership = Depends(_write),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    """Check the draft and the latest reviewed deck against the numbers. 202 with a job to poll;
    200 when the result is ready at once (`outcome: reused`: nothing changed since the last check,
    so the claims were reused, the numbers re-read, and no tokens spent) or a check is already
    running (`outcome: in_progress`, that check is returned)."""
    check, outcome = narrative.create_check(db, _startup(db, membership), user.id)
    db.commit()
    resp = success_response({"check": narrative.view(db, check), "outcome": outcome})
    if outcome == "queued":
        return resp
    return JSONResponse(status_code=200, content=resp)


@router.get("/narrative/checks/latest")
def latest_narrative_check(
    membership: Membership = Depends(_read),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    c = narrative.get_latest(db, membership.startup_id)
    return success_response({"check": narrative.view(db, c) if c else None})

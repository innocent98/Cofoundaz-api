"""Funding Hub slice E: grants. Founder and team member (FUNDING_ROLES), behind the `funding`
plan module. The catalogue is shared and human-verified; matches and the tracker are the
workspace's own. Matching and drafting run as jobs, so those routes answer 202 and the page
polls."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import FUNDING_ROLES
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.grant import (
    AnswerCreate,
    AnswerUpdate,
    ApplicationCreate,
    ApplicationUpdate,
    DraftRequest,
)
from app.services.billing.gate import assert_feature
from app.services.funding import grants as svc

router = APIRouter()
_role = require_role(*FUNDING_ROLES)
DISCLAIMER = (
    "Grant details change. Check the funder's own page before you apply: we show when each row "
    "was last checked and link to its source."
)


def _grants(
    membership: Membership = Depends(_role),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Membership:
    """Role first (403), then the plan module (402)."""
    assert_feature(db, membership.startup_id, "funding")
    return membership


@router.get("/grants", response_model=dict[str, Any])
def catalogue(
    q: str | None = Query(default=None, max_length=100),
    only_open: bool = False,
    kind: str | None = Query(default=None, pattern="^(cash|in_kind)$"),
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = svc.list_catalogue(
        db, q=q, only_open=only_open, kind="in_kind" if kind == "in_kind" else None
    )
    if kind == "cash":
        rows = [g for g in rows if not g.in_kind]
    return success_response(
        {"items": [svc.serialize_grant(g) for g in rows], "disclaimer": DISCLAIMER}
    )


@router.get("/grants/matches", response_model=dict[str, Any])
def matches(
    state: str = Query(
        default="suggested", pattern="^(suggested|dismissed|not_relevant|applying)$"
    ),
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        {
            "items": svc.list_matches(db, membership.startup_id, state=state),
            "refresh": svc.refresh_state(db, membership.startup_id),
            "disclaimer": DISCLAIMER,
        }
    )


@router.post("/grants/matches/refresh", response_model=dict[str, Any])
def refresh_matches(
    response: Response,
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Queue a fresh match run: 202 with the state to poll (`GET /grants/matches`). One already
    queued or running returns 200 with `deduplicated: true`."""
    _job, created = svc.queue_match(db, membership.startup_id)
    db.commit()
    response.status_code = status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK
    return success_response(
        {"deduplicated": not created, "refresh": svc.refresh_state(db, membership.startup_id)}
    )


def _set_state(
    db: Session, membership: Membership, user: User, match_id: uuid.UUID, state: str
) -> dict[str, Any]:
    svc.set_match_state(db, membership.startup_id, user.id, match_id, state)
    db.commit()
    return success_response({"id": str(match_id), "state": state})


@router.post("/grants/matches/{match_id}/dismiss", response_model=dict[str, Any])
def dismiss_match(
    match_id: uuid.UUID,
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return _set_state(db, membership, user, match_id, "dismissed")


@router.post("/grants/matches/{match_id}/not-relevant", response_model=dict[str, Any])
def not_relevant_match(
    match_id: uuid.UUID,
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return _set_state(db, membership, user, match_id, "not_relevant")


@router.post("/grants/matches/{match_id}/restore", response_model=dict[str, Any])
def restore_match(
    match_id: uuid.UUID,
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return _set_state(db, membership, user, match_id, "suggested")


@router.post("/grants/{grant_id}/apply", response_model=dict[str, Any])
def start_application(
    grant_id: uuid.UUID,
    response: Response,
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Start tracking a catalogue grant: 201 with the application and its starter questions. One
    already tracked returns it with 200."""
    row, created = svc.start_application(db, membership.startup_id, user.id, grant_id)
    db.commit()
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return success_response(svc.serialize_application(db, row, detail=True))


@router.get("/grant-applications", response_model=dict[str, Any])
def list_applications(
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(svc.list_applications(db, membership.startup_id))


@router.post("/grant-applications", status_code=201, response_model=dict[str, Any])
def create_application(
    payload: ApplicationCreate,
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.create_manual(db, membership.startup_id, user.id, payload.model_dump())
    db.commit()
    return success_response(svc.serialize_application(db, row, detail=True))


@router.get("/grant-applications/{app_id}", response_model=dict[str, Any])
def get_application(
    app_id: uuid.UUID,
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.get_application(db, membership.startup_id, app_id)
    out = svc.serialize_application(db, row, detail=True)
    db.commit()  # reconcile may have settled a dead draft
    return success_response(out)


@router.patch("/grant-applications/{app_id}", response_model=dict[str, Any])
def update_application(
    app_id: uuid.UUID,
    payload: ApplicationUpdate,
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.update_application(
        db, membership.startup_id, app_id, payload.model_dump(exclude_unset=True)
    )
    db.commit()
    return success_response(svc.serialize_application(db, row, detail=True))


@router.delete("/grant-applications/{app_id}", response_model=dict[str, Any])
def delete_application(
    app_id: uuid.UUID,
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    svc.delete_application(db, membership.startup_id, app_id)
    db.commit()
    return success_response({"deleted": True})


@router.post("/grant-applications/{app_id}/answers", status_code=201, response_model=dict[str, Any])
def add_answer(
    app_id: uuid.UUID,
    payload: AnswerCreate,
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.add_answer(db, membership.startup_id, app_id, payload.question, payload.answer)
    db.commit()
    return success_response(svc.serialize_answer(row))


@router.patch("/grant-applications/{app_id}/answers/{answer_id}", response_model=dict[str, Any])
def update_answer(
    app_id: uuid.UUID,
    answer_id: uuid.UUID,
    payload: AnswerUpdate,
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.update_answer(
        db, membership.startup_id, app_id, answer_id, payload.model_dump(exclude_unset=True)
    )
    db.commit()
    return success_response(svc.serialize_answer(row))


@router.delete("/grant-applications/{app_id}/answers/{answer_id}", response_model=dict[str, Any])
def delete_answer(
    app_id: uuid.UUID,
    answer_id: uuid.UUID,
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    svc.delete_answer(db, membership.startup_id, app_id, answer_id)
    db.commit()
    return success_response({"deleted": True})


@router.post(
    "/grant-applications/{app_id}/answers/{answer_id}/draft", response_model=dict[str, Any]
)
def draft_answer(
    app_id: uuid.UUID,
    answer_id: uuid.UUID,
    payload: DraftRequest,
    response: Response,
    membership: Membership = Depends(_grants),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Draft this answer with AI: 202 with the answer to poll (`draft_status: queued`, then null
    with the text filled in, or `failed`). An answer that already has text needs
    `overwrite: true`."""
    row = svc.queue_draft(db, membership.startup_id, app_id, answer_id, overwrite=payload.overwrite)
    db.commit()
    response.status_code = status.HTTP_202_ACCEPTED
    return success_response(svc.serialize_answer(row))

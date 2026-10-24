"""Sales slice B: sequences, enrollments, sending status, AI drafts, deal follow-up send, and the
public unsubscribe. The CRM and coach routes live in `sales.py`; this router is mounted under the
same `/sales` prefix."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError
from app.core.rate_limit import limiter
from app.db.models.enums import (
    SALES_ROLES,
    ActivityKind,
    DraftKind,
    EnrollmentStatus,
    MembershipRole,
)
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.platform.activity import write_activity
from app.schemas.sales import ActivityCreate
from app.schemas.sales_sequences import (
    EnrollIn,
    EnrollmentOut,
    FollowUpSend,
    SequenceCreate,
    SequenceUpdate,
    StepWriteIn,
)
from app.services.sales import deals as deals_svc
from app.services.sales import drafts as drafts_svc
from app.services.sales import sending as sending_svc
from app.services.sales import sequences as seq_svc
from app.services.sales import unsubscribe as unsub_svc
from app.services.sales.activities import create_activity
from app.services.sales.outreach_render import unsubscribe_page_url
from app.services.sales.tokens import verify_token

router = APIRouter()
public_router = APIRouter()
_sales = require_role(*SALES_ROLES)
_founder = require_role(MembershipRole.founder)


def _enrollment(e: Any) -> dict[str, Any]:
    return EnrollmentOut.model_validate(e).model_dump(mode="json")


# ---- sequences ----
@router.get("/sequences", response_model=dict[str, Any])
def list_sequences(
    include_archived: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    items, total = seq_svc.list_sequences(
        db, membership.startup_id, include_archived=include_archived, limit=limit, offset=offset
    )
    return success_response({"items": items, "total": total, "limit": limit, "offset": offset})


@router.post("/sequences", status_code=201, response_model=dict[str, Any])
def create_sequence(
    payload: SequenceCreate,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = seq_svc.create_sequence(db, membership.startup_id, payload, actor_id=user.id)
    db.commit()
    return success_response(seq_svc.sequence_detail(db, row))


@router.get("/sequences/{sequence_id}", response_model=dict[str, Any])
def get_sequence(
    sequence_id: uuid.UUID,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = seq_svc.get_sequence(db, membership.startup_id, sequence_id)
    return success_response(seq_svc.sequence_detail(db, row))


@router.patch("/sequences/{sequence_id}", response_model=dict[str, Any])
def update_sequence(
    sequence_id: uuid.UUID,
    payload: SequenceUpdate,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = seq_svc.get_sequence(db, membership.startup_id, sequence_id, lock=True)
    row = seq_svc.update_sequence(db, row, payload, actor_id=user.id)
    db.commit()
    return success_response(seq_svc.sequence_detail(db, row))


@router.delete("/sequences/{sequence_id}", response_model=dict[str, Any])
def archive_sequence(
    sequence_id: uuid.UUID,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = seq_svc.get_sequence(db, membership.startup_id, sequence_id, lock=True)
    stopped = seq_svc.archive_sequence(db, row, actor_id=user.id)
    db.commit()
    return success_response({"archived": True, "stopped_enrollments": stopped})


@router.get("/sequences/{sequence_id}/enrollments", response_model=dict[str, Any])
def list_enrollments(
    sequence_id: uuid.UUID,
    status: EnrollmentStatus | None = Query(default=None),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=200),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    seq_svc.get_sequence(db, membership.startup_id, sequence_id)  # 404 for another workspace's
    rows, total = seq_svc.list_enrollments(
        db, membership.startup_id, sequence_id, status=status, limit=limit, offset=offset
    )
    return success_response(
        {"items": [_enrollment(r) for r in rows], "total": total, "limit": limit, "offset": offset}
    )


@router.post("/sequences/{sequence_id}/enroll", response_model=dict[str, Any])
def enroll(
    sequence_id: uuid.UUID,
    payload: EnrollIn,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    seq = seq_svc.get_sequence(db, membership.startup_id, sequence_id, lock=True)
    rows, skipped = seq_svc.enroll(db, seq, payload, actor_id=user.id)
    db.commit()
    return success_response(
        {"enrolled": len(rows), "enrollments": [_enrollment(r) for r in rows], "skipped": skipped}
    )


@router.post("/enrollments/{enrollment_id}/replied", response_model=dict[str, Any])
def mark_replied(
    enrollment_id: uuid.UUID,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = seq_svc.mark_replied(db, membership.startup_id, enrollment_id, actor_id=user.id)
    db.commit()
    return success_response(_enrollment(row))


@router.post("/enrollments/{enrollment_id}/stop", response_model=dict[str, Any])
def stop_enrollment(
    enrollment_id: uuid.UUID,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = seq_svc.stop_enrollment(db, membership.startup_id, enrollment_id, actor_id=user.id)
    db.commit()
    return success_response(_enrollment(row))


# ---- AI drafts ----
@router.post(
    "/sequences/{sequence_id}/steps/{step_index}/write",
    status_code=202,
    response_model=dict[str, Any],
)
def write_step(
    sequence_id: uuid.UUID,
    step_index: int,
    payload: StepWriteIn | None = None,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    seq = seq_svc.get_sequence(db, membership.startup_id, sequence_id)
    row = drafts_svc.request_step_draft(
        db, seq, step_index, user.id, payload.instructions if payload else None
    )
    db.commit()
    return success_response(drafts_svc.serialize(row))


@router.get("/drafts/{draft_id}", response_model=dict[str, Any])
def get_draft(
    draft_id: uuid.UUID,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        drafts_svc.serialize(drafts_svc.get_draft(db, membership.startup_id, draft_id))
    )


# ---- sending status ----
@router.get("/sending", response_model=dict[str, Any])
def sending_status(
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    out = sending_svc.sending_status(db, membership.startup_id)
    db.commit()  # lazy settings seed persists
    return success_response(out)


@router.post("/sending/resume", response_model=dict[str, Any])
def resume_sending(
    membership: Membership = Depends(_founder),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    sending_svc.resume_sending(db, membership.startup_id)
    write_activity(
        db,
        startup_id=membership.startup_id,
        action="sales.sending.resumed",
        summary="Resumed sales emails",
        actor_user_id=user.id,
        entity_type="sales_settings",
    )
    out = sending_svc.sending_status(db, membership.startup_id)
    db.commit()
    return success_response(out)


# ---- deal follow-up ----
@router.post("/deals/{deal_id}/follow-up", status_code=202, response_model=dict[str, Any])
def request_follow_up(
    deal_id: uuid.UUID,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = drafts_svc.request_follow_up(db, membership.startup_id, deal_id, user.id)
    db.commit()
    return success_response(drafts_svc.serialize(row))


@router.get("/deals/{deal_id}/follow-up", response_model=dict[str, Any])
def latest_follow_up(
    deal_id: uuid.UUID,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = drafts_svc.latest_follow_up(db, membership.startup_id, deal_id)
    return success_response(drafts_svc.serialize(row) if row else None)


@router.post("/deals/{deal_id}/follow-up/send", response_model=dict[str, Any])
def send_follow_up(
    deal_id: uuid.UUID,
    payload: FollowUpSend,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    draft = drafts_svc.get_draft(db, membership.startup_id, payload.draft_id)
    if draft.kind != DraftKind.follow_up or draft.deal_id != deal_id:
        raise AppError("NOT_FOUND", "Not found.", 404)
    if draft.status.value != "ready":
        raise AppError("DRAFT_NOT_READY", "This follow-up is not ready to send.", 409)
    email, first, last, company = drafts_svc.recipient_for_deal(db, membership.startup_id, deal_id)
    sent = sending_svc.send_follow_up(
        db,
        startup_id=membership.startup_id,
        actor_id=user.id,
        deal_id=deal_id,
        to_email=email,
        contact_first=first,
        contact_last=last,
        company=company,
        draft=draft,
        subject=payload.subject,
        body=payload.body,
    )
    deal = deals_svc.get_deal(db, membership.startup_id, deal_id)
    create_activity(
        db,
        membership.startup_id,
        ActivityCreate(
            kind=ActivityKind.email,
            body=f"Sent follow-up: {payload.subject}",
            deal_id=deal.id,
            contact_id=deal.contact_id,
        ),
        author_id=user.id,
    )
    db.commit()
    return success_response(
        {
            "sent": True,
            "send_id": str(sent.id),
            "to": sent.recipient_email,
            "draft_id": str(draft.id),
        }
    )


# ---- public unsubscribe (no auth: the signed token is the credential) ----
@public_router.get("/unsubscribe/{token}", response_model=None)
# One bucket per caller across every token (see verify.py).
@limiter.shared_limit("30/minute", scope="public-unsubscribe-read")
def unsubscribe_status(
    request: Request,
    token: str,
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    # A mail client or browser that opens the List-Unsubscribe URL asks for HTML: send it to the
    # FE page (only for a token we signed). The FE's own fetch asks for JSON and gets the status.
    if "text/html" in request.headers.get("accept", "") and verify_token(token) is not None:
        return RedirectResponse(unsubscribe_page_url(token), status_code=303)
    return success_response(unsub_svc.unsubscribe_status(db, token))


@public_router.post("/unsubscribe/{token}", response_model=dict[str, Any])
@limiter.shared_limit("30/minute", scope="public-unsubscribe")
def unsubscribe(
    request: Request,  # noqa: ARG001 - required by the rate limiter
    token: str,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    # RFC 8058 one-click: mail clients POST `List-Unsubscribe=One-Click` (form-encoded) here, so
    # the body is deliberately never read.
    out = unsub_svc.unsubscribe(db, token)
    db.commit()
    return success_response(out)

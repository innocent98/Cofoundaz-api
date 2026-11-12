"""Funding Hub slice D: term sheets. FOUNDER ONLY (D-FUND-5): team members and every other role
get 403 on every route, behind the `funding` plan module. Extraction proposes values and a founder
confirms them (D-FUND-6); flags come from a fixed rules table; the page carries the disclaimer."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import TERM_SHEET_ROLES
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.term_sheet import (
    TermSheetConfirm,
    TermSheetCreate,
    TermSheetExtract,
    TermSheetUpdate,
)
from app.services.billing.gate import assert_feature
from app.services.funding import term_rules
from app.services.funding import term_sheets as svc
from app.services.funding.errors import _validation

router = APIRouter()
_role = require_role(*TERM_SHEET_ROLES)


def _term_sheets(
    membership: Membership = Depends(_role),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Membership:
    """Role first (403), then the Scale plan module (402)."""
    assert_feature(db, membership.startup_id, "funding")
    return membership


def _one(db: Session, membership: Membership, sheet_id: uuid.UUID) -> dict[str, Any]:
    row = svc.get(db, membership.startup_id, sheet_id)
    svc.reconcile(db, row)
    return svc.serialize(db, [row])[0]


@router.get("/term-sheets", response_model=dict[str, Any])
def list_term_sheets(
    investor_id: uuid.UUID | None = None,
    membership: Membership = Depends(_term_sheets),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = svc.list_sheets(db, membership.startup_id, investor_id=investor_id)
    db.commit()  # reconcile may have settled a dead extraction
    return success_response(
        {
            "items": svc.serialize(db, rows, detail=False),
            "currency": svc.currency(db, membership.startup_id),
            "disclaimer": term_rules.DISCLAIMER,
        }
    )


@router.get("/term-sheets/rules", response_model=dict[str, Any])
def rules(
    membership: Membership = Depends(_term_sheets),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
) -> dict[str, Any]:
    """The table the flags come from, so the page can show what each flag compares against."""
    return success_response(
        {
            "rules": [
                {"id": r.id, "term": r.term, "title": r.title, "threshold": r.threshold}
                for r in term_rules.RULES
            ],
            "disclaimer": term_rules.DISCLAIMER,
        }
    )


@router.get("/term-sheets/compare", response_model=dict[str, Any])
def compare(
    ids: str = Query(min_length=1, max_length=400),
    membership: Membership = Depends(_term_sheets),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """`ids` is a comma separated list of 2 to 6 confirmed term sheets."""
    try:
        parsed = [uuid.UUID(part) for part in ids.split(",") if part.strip()]
    except ValueError:
        raise _validation("ids", "Send term sheet ids separated by commas.") from None
    return success_response(svc.compare_sheets(db, membership.startup_id, parsed))


@router.get("/term-sheets/audit", response_model=dict[str, Any])
def audit(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    membership: Membership = Depends(_term_sheets),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(svc.audit_log(db, membership.startup_id, limit=limit, offset=offset))


@router.post("/term-sheets", status_code=201, response_model=dict[str, Any])
def create_term_sheet(
    payload: TermSheetCreate,
    membership: Membership = Depends(_term_sheets),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.create_typed(db, membership.startup_id, user.id, payload)
    db.commit()
    return success_response(svc.serialize(db, [row])[0])


@router.post("/term-sheets/extract", response_model=dict[str, Any])
def extract_term_sheet(
    payload: TermSheetExtract,
    response: Response,
    membership: Membership = Depends(_term_sheets),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Queue extraction from a Documents PDF: 202 with the sheet to poll (`status: extracting`).
    The same bytes already extracted return that sheet with 200 and `deduplicated: true`."""
    row, created = svc.create_extraction(db, membership.startup_id, user.id, payload)
    db.commit()
    response.status_code = status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK
    return success_response(
        {"deduplicated": not created, "term_sheet": svc.serialize(db, [row])[0]}
    )


@router.get("/term-sheets/{sheet_id}", response_model=dict[str, Any])
def get_term_sheet(
    sheet_id: uuid.UUID,
    membership: Membership = Depends(_term_sheets),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    out = _one(db, membership, sheet_id)
    db.commit()
    return success_response({**out, "disclaimer": term_rules.DISCLAIMER})


@router.post("/term-sheets/{sheet_id}/confirm", response_model=dict[str, Any])
def confirm_term_sheet(
    sheet_id: uuid.UUID,
    payload: TermSheetConfirm,
    membership: Membership = Depends(_term_sheets),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.confirm(db, membership.startup_id, user.id, sheet_id, payload)
    db.commit()
    return success_response(svc.serialize(db, [row])[0])


@router.patch("/term-sheets/{sheet_id}", response_model=dict[str, Any])
def update_term_sheet(
    sheet_id: uuid.UUID,
    payload: TermSheetUpdate,
    membership: Membership = Depends(_term_sheets),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = svc.update_sheet(db, membership.startup_id, user.id, sheet_id, payload)
    db.commit()
    return success_response(svc.serialize(db, [row])[0])


@router.delete("/term-sheets/{sheet_id}", response_model=dict[str, Any])
def delete_term_sheet(
    sheet_id: uuid.UUID,
    membership: Membership = Depends(_term_sheets),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    svc.delete_sheet(db, membership.startup_id, user.id, sheet_id)
    db.commit()
    return success_response({"deleted": True})

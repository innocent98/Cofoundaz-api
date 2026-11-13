import uuid
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Response, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import (
    LEGAL_ROLES,
    ComplianceStatus,
    IpAssetStatus,
    IpAssetType,
    MembershipRole,
)
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.legal import (
    ComplianceCreate,
    ComplianceUpdate,
    FormationStepPatch,
    IpAssetCreate,
    IpAssetUpdate,
)
from app.services.legal import compliance as compliance_svc
from app.services.legal import formation as formation_svc
from app.services.legal import ip as ip_svc
from app.services.legal.catalog import JURISDICTIONS

router = APIRouter()

# Two dependencies, not one (spec D1). A legal advisor reads all three Slice 1 surfaces and
# writes none of them; commenting is Slice 2. LEGAL_ROLES is shared with the notification
# AUDIENCE map, so a reminder naming a filing can never reach a role this would 403.
_legal_reader = require_role(*LEGAL_ROLES)
_legal_writer = require_role(MembershipRole.founder, MembershipRole.team_member)


# --- formation ---------------------------------------------------------------------------------


@router.get("/formation/jurisdictions", response_model=dict[str, Any])
def list_jurisdictions(
    membership: Membership = Depends(_legal_reader),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
) -> dict[str, Any]:
    """The jurisdictions Slice 1 supports, as codes plus a display name.

    Codes, not the prototype's display strings: the FE sends `NG`, and renders `name`.
    """
    return success_response(
        {"jurisdictions": [{"code": code, "name": name} for code, name in JURISDICTIONS.items()]}
    )


@router.get("/formation/checklist", response_model=dict[str, Any])
def get_checklist(
    jurisdiction: str,
    membership: Membership = Depends(_legal_reader),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        formation_svc.checklist(db, startup_id=membership.startup_id, jurisdiction=jurisdiction)
    )


@router.patch("/formation/steps/{step_key}", response_model=dict[str, Any])
def set_formation_step(
    step_key: str,
    payload: FormationStepPatch,
    membership: Membership = Depends(_legal_writer),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Tick or untick one step, returning the whole checklist so the FE needs no second call."""
    formation_svc.set_step(
        db,
        startup_id=membership.startup_id,
        jurisdiction=payload.jurisdiction,
        step_key=step_key,
        completed=payload.completed,
        actor_id=user.id,
    )
    db.commit()
    return success_response(
        formation_svc.checklist(
            db, startup_id=membership.startup_id, jurisdiction=payload.jurisdiction
        )
    )


# --- ip assets ---------------------------------------------------------------------------------


def _today() -> date:
    """The derived statuses and the reminders share one clock (spec D4)."""
    return datetime.now(UTC).date()


@router.post("/ip-assets", response_model=dict[str, Any])
def create_ip_asset(
    payload: IpAssetCreate,
    membership: Membership = Depends(_legal_writer),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = ip_svc.create_asset(
        db, startup_id=membership.startup_id, created_by=user.id, data=payload
    )
    db.commit()
    return success_response(ip_svc.serialize_asset(row, today=_today()))


@router.get("/ip-assets", response_model=dict[str, Any])
def list_ip_assets(
    type: IpAssetType | None = None,
    status: IpAssetStatus | None = None,
    membership: Membership = Depends(_legal_reader),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = ip_svc.list_assets(db, startup_id=membership.startup_id, type=type, status=status)
    today = _today()
    return success_response({"ip_assets": [ip_svc.serialize_asset(r, today=today) for r in rows]})


@router.get("/ip-assets/{asset_id}", response_model=dict[str, Any])
def get_ip_asset(
    asset_id: uuid.UUID,
    membership: Membership = Depends(_legal_reader),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = ip_svc.get_asset(db, startup_id=membership.startup_id, asset_id=asset_id)
    return success_response(ip_svc.serialize_asset(row, today=_today()))


@router.patch("/ip-assets/{asset_id}", response_model=dict[str, Any])
def update_ip_asset(
    asset_id: uuid.UUID,
    payload: IpAssetUpdate,
    membership: Membership = Depends(_legal_writer),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = ip_svc.update_asset(db, startup_id=membership.startup_id, asset_id=asset_id, data=payload)
    db.commit()
    return success_response(ip_svc.serialize_asset(row, today=_today()))


@router.delete("/ip-assets/{asset_id}", response_model=dict[str, Any])
def delete_ip_asset(
    asset_id: uuid.UUID,
    membership: Membership = Depends(_legal_writer),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    ip_svc.delete_asset(db, startup_id=membership.startup_id, asset_id=asset_id)
    db.commit()
    return success_response({"deleted": True})


# --- compliance items --------------------------------------------------------------------------


@router.post("/compliance-items", response_model=dict[str, Any])
def create_compliance_item(
    payload: ComplianceCreate,
    membership: Membership = Depends(_legal_writer),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = compliance_svc.create_item(
        db, startup_id=membership.startup_id, created_by=user.id, data=payload
    )
    db.commit()
    return success_response(compliance_svc.serialize_item(row, today=_today()))


@router.get("/compliance-items", response_model=dict[str, Any])
def list_compliance_items(
    date_from: date | None = None,
    date_to: date | None = None,
    status: ComplianceStatus | None = None,
    membership: Membership = Depends(_legal_reader),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """The compliance calendar. `status` filters the STORED value (open/done) only.

    The derived badge is returned per row, so the FE counts the overdue ones itself - which is
    what its banner already does, so no summary endpoint is needed (spec D13).
    """
    rows = compliance_svc.list_items(
        db,
        startup_id=membership.startup_id,
        date_from=date_from,
        date_to=date_to,
        status=status,
    )
    today = _today()
    return success_response(
        {"compliance_items": [compliance_svc.serialize_item(r, today=today) for r in rows]}
    )


@router.get("/compliance-items/{item_id}", response_model=dict[str, Any])
def get_compliance_item(
    item_id: uuid.UUID,
    membership: Membership = Depends(_legal_reader),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = compliance_svc.get_item(db, startup_id=membership.startup_id, item_id=item_id)
    return success_response(compliance_svc.serialize_item(row, today=_today()))


@router.patch("/compliance-items/{item_id}", response_model=dict[str, Any])
def update_compliance_item(
    item_id: uuid.UUID,
    payload: ComplianceUpdate,
    membership: Membership = Depends(_legal_writer),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = compliance_svc.update_item(
        db, startup_id=membership.startup_id, item_id=item_id, data=payload
    )
    db.commit()
    return success_response(compliance_svc.serialize_item(row, today=_today()))


@router.delete("/compliance-items/{item_id}", response_model=dict[str, Any])
def delete_compliance_item(
    item_id: uuid.UUID,
    membership: Membership = Depends(_legal_writer),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    compliance_svc.delete_item(db, startup_id=membership.startup_id, item_id=item_id)
    db.commit()
    return success_response({"deleted": True})


@router.post("/compliance-items/{item_id}/proof", response_model=dict[str, Any])
async def upload_compliance_proof(
    item_id: uuid.UUID,
    file: UploadFile = File(...),  # noqa: B008
    membership: Membership = Depends(_legal_writer),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Attach or replace the proof of filing (multipart).

    Reads one byte past the cap so an oversized file is refused without buffering all of it.
    """
    content = await file.read(compliance_svc.MAX_PROOF_BYTES + 1)
    row = compliance_svc.attach_proof(
        db,
        startup_id=membership.startup_id,
        item_id=item_id,
        content_type=file.content_type,
        size_bytes=len(content),
        content=content,
    )
    db.commit()
    return success_response(compliance_svc.serialize_item(row, today=_today()))


@router.get("/compliance-items/{item_id}/proof")
def download_compliance_proof(
    item_id: uuid.UUID,
    membership: Membership = Depends(_legal_reader),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    """Stream the private proof file (D-LEG-5): authenticated, never a signed or public link.

    The FE fetches it with the bearer token. No proof, a cross-workspace id and a missing file
    are all the same 404; the forbidden roles never get past the reader gate (403).
    """
    key, content = compliance_svc.read_proof(db, startup_id=membership.startup_id, item_id=item_id)
    return Response(
        content=content,
        media_type=compliance_svc.proof_content_type(key),
        headers={
            "Content-Disposition": f'attachment; filename="proof-of-filing{_ext(key)}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


def _ext(key: str) -> str:
    return "." + key.rsplit(".", 1)[-1] if "." in key else ""

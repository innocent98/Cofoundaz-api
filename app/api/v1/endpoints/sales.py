import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError
from app.core.redis import get_redis
from app.db.models.enums import SALES_ROLES, LeadSource, LeadStatus
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.sales import (
    AccountCreate,
    AccountDetailOut,
    AccountOut,
    AccountUpdate,
    ContactCreate,
    ContactOut,
    ContactUpdate,
    ImportCommit,
    LeadCreate,
    LeadOut,
    LeadUpdate,
    SettingsOut,
    SettingsUpdate,
    StageOut,
    StagesReplace,
)
from app.services.sales import csv_import as csv_svc
from app.services.sales import leads as leads_svc
from app.services.sales import people as people_svc
from app.services.sales import stages as stages_svc

router = APIRouter()
_sales = require_role(*SALES_ROLES)


# ---- Pipeline stages ----
@router.get("/stages", response_model=dict[str, Any])
def list_stages(
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = stages_svc.list_stages(db, membership.startup_id)
    db.commit()  # lazy-seed persists
    return success_response([StageOut.model_validate(r).model_dump(mode="json") for r in rows])


@router.put("/stages", response_model=dict[str, Any])
def replace_stages(
    payload: StagesReplace,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = stages_svc.replace_stages(db, membership.startup_id, payload.stages, actor_id=user.id)
    db.commit()
    return success_response([StageOut.model_validate(r).model_dump(mode="json") for r in rows])


# ---- Settings ----
@router.get("/settings", response_model=dict[str, Any])
def get_settings(
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = stages_svc.get_settings(db, membership.startup_id)
    db.commit()
    return success_response(SettingsOut.model_validate(row).model_dump())


@router.patch("/settings", response_model=dict[str, Any])
def update_settings(
    payload: SettingsUpdate,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = stages_svc.update_settings(db, membership.startup_id, payload, actor_id=user.id)
    db.commit()
    return success_response(SettingsOut.model_validate(row).model_dump())


# ---- Accounts ----
@router.get("/accounts", response_model=dict[str, Any])
def list_accounts(
    q: str | None = Query(default=None, max_length=200),  # noqa: B008
    owner_id: uuid.UUID | None = Query(default=None),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=200),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows, total = people_svc.list_accounts(
        db, membership.startup_id, q=q, owner_id=owner_id, limit=limit, offset=offset
    )
    return success_response(
        {
            "items": [AccountOut.model_validate(r).model_dump(mode="json") for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


@router.post("/accounts", response_model=dict[str, Any])
def create_account(
    payload: AccountCreate,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = people_svc.create_account(db, membership.startup_id, payload, actor_id=user.id)
    db.commit()
    return success_response(AccountOut.model_validate(row).model_dump(mode="json"))


@router.get("/accounts/{account_id}", response_model=dict[str, Any])
def get_account(
    account_id: uuid.UUID,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    detail = people_svc.account_detail(db, membership.startup_id, account_id)
    return success_response(
        AccountDetailOut(
            account=AccountOut.model_validate(detail["account"]),
            contacts=[ContactOut.model_validate(c) for c in detail["contacts"]],
            open_deals=detail["open_deals"],
            lifetime_won_minor=detail["lifetime_won_minor"],
        ).model_dump(mode="json")
    )


@router.patch("/accounts/{account_id}", response_model=dict[str, Any])
def update_account(
    account_id: uuid.UUID,
    payload: AccountUpdate,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = people_svc.update_account(
        db, membership.startup_id, account_id, payload, actor_id=user.id
    )
    db.commit()
    return success_response(AccountOut.model_validate(row).model_dump(mode="json"))


@router.delete("/accounts/{account_id}", response_model=dict[str, Any])
def delete_account(
    account_id: uuid.UUID,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    people_svc.delete_account(db, membership.startup_id, account_id, actor_id=user.id)
    db.commit()
    return success_response({"deleted": True})


# ---- Contacts ----
@router.get("/contacts", response_model=dict[str, Any])
def list_contacts(
    account_id: uuid.UUID | None = Query(default=None),  # noqa: B008
    q: str | None = Query(default=None, max_length=200),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=200),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows, total = people_svc.list_contacts(
        db, membership.startup_id, account_id=account_id, q=q, limit=limit, offset=offset
    )
    return success_response(
        {
            "items": [ContactOut.model_validate(r).model_dump(mode="json") for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


@router.post("/contacts", response_model=dict[str, Any])
def create_contact(
    payload: ContactCreate,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = people_svc.create_contact(db, membership.startup_id, payload, actor_id=user.id)
    db.commit()
    return success_response(ContactOut.model_validate(row).model_dump(mode="json"))


@router.get("/contacts/{contact_id}", response_model=dict[str, Any])
def get_contact(
    contact_id: uuid.UUID,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = people_svc.get_contact(db, membership.startup_id, contact_id)
    return success_response(ContactOut.model_validate(row).model_dump(mode="json"))


@router.patch("/contacts/{contact_id}", response_model=dict[str, Any])
def update_contact(
    contact_id: uuid.UUID,
    payload: ContactUpdate,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = people_svc.update_contact(
        db, membership.startup_id, contact_id, payload, actor_id=user.id
    )
    db.commit()
    return success_response(ContactOut.model_validate(row).model_dump(mode="json"))


@router.delete("/contacts/{contact_id}", response_model=dict[str, Any])
def delete_contact(
    contact_id: uuid.UUID,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    people_svc.delete_contact(db, membership.startup_id, contact_id, actor_id=user.id)
    db.commit()
    return success_response({"deleted": True})


# ---- Leads ----
@router.get("/leads", response_model=dict[str, Any])
def list_leads(
    status: LeadStatus | None = Query(default=None),  # noqa: B008
    owner_id: uuid.UUID | None = Query(default=None),  # noqa: B008
    source: LeadSource | None = Query(default=None),  # noqa: B008
    q: str | None = Query(default=None, max_length=200),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=200),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows, total = leads_svc.list_leads(
        db,
        membership.startup_id,
        status=status,
        owner_id=owner_id,
        source=source,
        q=q,
        limit=limit,
        offset=offset,
    )
    return success_response(
        {
            "items": [LeadOut.model_validate(r).model_dump(mode="json") for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


@router.post("/leads", response_model=dict[str, Any])
def create_lead(
    payload: LeadCreate,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = leads_svc.create_lead(db, membership.startup_id, payload, actor_id=user.id)
    db.commit()
    return success_response(LeadOut.model_validate(row).model_dump(mode="json"))


# The /leads/import* routes must stay above /leads/{lead_id} so the literal path wins.
_CHUNK_BYTES = 64 * 1024


@router.post("/leads/import/preview", response_model=dict[str, Any])
async def preview_lead_import(
    file: UploadFile = File(...),  # noqa: B008
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
) -> dict[str, Any]:
    content = b""
    while chunk := await file.read(_CHUNK_BYTES):
        content += chunk
        if len(content) > csv_svc.MAX_BYTES:
            raise AppError("PAYLOAD_TOO_LARGE", "CSV must be 2 MB or smaller.", 413)
    out = csv_svc.preview_csv(get_redis(), membership.startup_id, user.id, content)
    return success_response(out.model_dump(mode="json"))


@router.post("/leads/import", response_model=dict[str, Any])
def import_leads(
    payload: ImportCommit,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    res = csv_svc.commit_csv(
        db, get_redis(), membership.startup_id, user.id, payload.upload_id, payload.mapping
    )
    db.commit()
    return success_response(res.model_dump(mode="json"))


@router.get("/leads/{lead_id}", response_model=dict[str, Any])
def get_lead(
    lead_id: uuid.UUID,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = leads_svc.get_lead(db, membership.startup_id, lead_id)
    return success_response(LeadOut.model_validate(row).model_dump(mode="json"))


@router.patch("/leads/{lead_id}", response_model=dict[str, Any])
def update_lead(
    lead_id: uuid.UUID,
    payload: LeadUpdate,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = leads_svc.update_lead(db, membership.startup_id, lead_id, payload, actor_id=user.id)
    db.commit()
    return success_response(LeadOut.model_validate(row).model_dump(mode="json"))


@router.delete("/leads/{lead_id}", response_model=dict[str, Any])
def delete_lead(
    lead_id: uuid.UUID,
    membership: Membership = Depends(_sales),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    leads_svc.delete_lead(db, membership.startup_id, lead_id, actor_id=user.id)
    db.commit()
    return success_response({"deleted": True})

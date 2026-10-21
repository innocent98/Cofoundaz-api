from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.errors import AppError, NotFound
from app.db.models.enums import FINANCE_ROLES, MembershipRole
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.finance_connection import (
    ConnectRequest,
    IntegrationsResponse,
    ProviderEntry,
)
from app.services.finance.payments import connections as conn_svc
from app.services.finance.payments.crypto import encryption_configured
from app.services.finance.payments.registry import get_provider
from app.services.finance.payments.sync import sync_connection

router = APIRouter()

_finance = require_role(*FINANCE_ROLES)
_founder = require_role(MembershipRole.founder)


@router.get("/integrations", response_model=dict[str, Any])
def list_integrations(
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    live = conn_svc.get_active(db, membership.startup_id)
    entries = [
        ProviderEntry(
            provider=name,
            label=conn_svc.LABELS[name],
            key_hint=conn_svc.KEY_HINTS[name],
            connection=conn_svc.serialize(live) if live and live.provider == name else None,
        )
        for name in conn_svc.PROVIDER_NAMES
    ]
    body = IntegrationsResponse(encryption_configured=encryption_configured(), providers=entries)
    return success_response(body.model_dump(mode="json"))


@router.post("/integrations/{provider}/connect", response_model=dict[str, Any])
def connect_integration(
    provider: str,
    payload: ConnectRequest,
    membership: Membership = Depends(_founder),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = conn_svc.connect(
        db,
        startup_id=membership.startup_id,
        user_id=user.id,
        provider=provider,
        secret_key=payload.secret_key,
        secret_hash=payload.secret_hash,
    )
    db.commit()
    return success_response(conn_svc.serialize(row).model_dump(mode="json"))


@router.post("/integrations/{provider}/disconnect", response_model=dict[str, Any])
def disconnect_integration(
    provider: str,
    membership: Membership = Depends(_founder),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = conn_svc.disconnect(
        db, startup_id=membership.startup_id, user_id=user.id, provider=provider
    )
    db.commit()
    return success_response(conn_svc.serialize(row).model_dump(mode="json"))


@router.post("/integrations/{provider}/sync", response_model=dict[str, Any])
def sync_integration(
    provider: str,
    membership: Membership = Depends(_finance),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    get_provider(provider)
    conn = conn_svc.get_for_provider(db, membership.startup_id, provider)
    if conn is None or conn.status != "active":
        raise NotFound()
    outcome = sync_connection(db, conn.id, manual=True)
    if outcome is None:
        raise AppError("SYNC_IN_PROGRESS", "A sync is already running. Try again shortly.", 409)
    db.commit()
    return success_response(
        {
            "connection": conn_svc.serialize(outcome.connection).model_dump(mode="json"),
            "summary": outcome.summary,
        }
    )

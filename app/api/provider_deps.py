"""Guard for the provider portal (`/provider/*`), the expert-side counterpart of `staff_deps`.

Being an expert is a row in `marketplace_providers` linked to the caller's account, read from the DB
on every request (never from the JWT), so a rejection or suspension applies on the next call. An
expert has no workspace: nothing here consults `memberships`, and nothing in a workspace route
consults this table, so the two sets of routes can't be crossed.

  not signed in            -> 401 (`get_current_user`)
  email not verified       -> 403 EMAIL_NOT_VERIFIED
  account not active       -> 403 FORBIDDEN
  no provider row          -> 403 NOT_A_PROVIDER
  rejected                 -> 403 PROVIDER_REJECTED
  `approved=True` and not approved (still in vetting, or suspended) -> 403 PROVIDER_NOT_APPROVED
"""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.errors import AppError, Forbidden
from app.db.models.enums import ProviderStatus, UserStatus
from app.db.models.marketplace import MarketplaceProvider
from app.db.models.user import User
from app.db.session import get_db


@dataclass(frozen=True)
class ProviderContext:
    user: User
    provider: MarketplaceProvider


def _forbidden(code: str, message: str) -> AppError:
    return AppError(code=code, message=message, http_status=403)


def require_provider(*, approved: bool = False) -> Callable[..., ProviderContext]:
    def _dep(
        user: User = Depends(get_verified_user),  # noqa: B008
        db: Session = Depends(get_db),  # noqa: B008
    ) -> ProviderContext:
        if user.status != UserStatus.active:
            raise Forbidden()
        provider = (
            db.query(MarketplaceProvider).filter(MarketplaceProvider.user_id == user.id).first()
        )
        if provider is None:
            raise _forbidden("NOT_A_PROVIDER", "This account isn't a Marketplace expert.")
        if provider.status == ProviderStatus.rejected:
            raise _forbidden("PROVIDER_REJECTED", "Your expert application was not approved.")
        if approved and provider.status != ProviderStatus.approved:
            raise _forbidden("PROVIDER_NOT_APPROVED", "Your expert profile isn't approved yet.")
        return ProviderContext(user=user, provider=provider)

    return _dep

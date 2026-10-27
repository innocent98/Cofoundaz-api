from fastapi import APIRouter

from app.api.v1.endpoints import (
    ai,
    assessments,
    business,
    calendar,
    dashboard,
    documents,
    finance,
    finance_integrations,
    health,
    health_score,
    invitations,
    jobs,
    journal,
    learning,
    marketing,
    mission,
    notifications,
    payment_webhooks,
    roadmap,
    sales,
    sales_sequences,
    search,
    team,
    validation,
    verify,
    workspace,
)
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.onboarding import router as onboarding_router
from app.services.documents.grants import register as register_grant_binding
from app.services.health_score.triggers import register as register_health_triggers
from app.services.notifications.registry import register as register_notifications

register_notifications()
register_grant_binding()
register_health_triggers()

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(onboarding_router, prefix="/onboarding", tags=["onboarding"])
api_router.include_router(invitations.router, prefix="/invitations", tags=["invitations"])
api_router.include_router(assessments.router, prefix="/assessments", tags=["assessments"])
api_router.include_router(health_score.router, prefix="/health-score", tags=["health-score"])
api_router.include_router(roadmap.router, prefix="/roadmap", tags=["roadmap"])
api_router.include_router(mission.router, prefix="/missions", tags=["missions"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(business.router, prefix="/business-builder", tags=["business-builder"])
api_router.include_router(journal.router, prefix="/journal", tags=["journal"])
api_router.include_router(documents.router, tags=["documents"])
api_router.include_router(notifications.router, tags=["notifications"])
api_router.include_router(learning.router, prefix="/learning", tags=["learning"])
api_router.include_router(verify.router, prefix="/verify", tags=["verify"])
api_router.include_router(marketing.router, prefix="/marketing", tags=["marketing"])
api_router.include_router(sales.router, prefix="/sales", tags=["sales"])
api_router.include_router(sales_sequences.router, prefix="/sales", tags=["sales"])
api_router.include_router(
    sales_sequences.public_router, prefix="/public/sales", tags=["sales-public"]
)
api_router.include_router(finance.router, prefix="/finance", tags=["finance"])
api_router.include_router(finance_integrations.router, prefix="/finance", tags=["finance"])
api_router.include_router(payment_webhooks.router, prefix="/webhooks", tags=["webhooks"])
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(validation.router, prefix="/validation", tags=["validation"])
api_router.include_router(calendar.router, prefix="/calendar", tags=["calendar"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(team.router, prefix="/team", tags=["team"])
api_router.include_router(workspace.router, prefix="/workspace", tags=["workspace"])

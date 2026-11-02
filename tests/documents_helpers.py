"""Shared setup for the Documents BE-B tests: a workspace with one member per role."""

import io
from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user
from tests.pdf_helpers import make_pdf

PDF = make_pdf()


def headers(user, startup=None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}
    if startup is not None:
        h["X-Workspace-Id"] = str(startup.id)
    return h


def team(db, *, name="Acme"):
    """(startup, {role: user}) with a verified user for every role."""
    founder = create_user(db, email_verified_at=datetime.now(UTC))
    startup = create_startup(db, owner=founder, name=name, stage=StartupStage.validation)
    users = {}
    for role in MembershipRole:
        u = founder if role == MembershipRole.founder else create_user(db)
        u.email_verified_at = datetime.now(UTC)
        create_membership(db, u, startup, role=role)
        users[role] = u
    db.flush()
    return startup, users


def outsider(db, *, email=None, verified=True):
    """A user with no membership in the workspace."""
    return create_user(db, email=email, email_verified_at=datetime.now(UTC) if verified else None)


def new_doc(client, h, **body):
    body.setdefault("title", "Plan")
    r = client.post("/api/v1/documents", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def upload(client, h, *, name="a.pdf", ct="application/pdf", body=PDF, folder=None):
    data = {"folder": folder} if folder else None
    r = client.post(
        "/api/v1/documents/files",
        files={"file": (name, io.BytesIO(body), ct)},
        data=data,
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]

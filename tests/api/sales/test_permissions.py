"""Permission sweep: the route table of the sales router is enumerated, so a route added later
is covered automatically. Every route is auth-required and SALES_ROLES-only."""

import re
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.routing import APIRoute

from app.api.v1.endpoints import sales as sales_endpoints
from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/sales"
_ID = str(uuid.uuid4())
NON_SALES_ROLES = [
    MembershipRole.mentor,
    MembershipRole.business_consultant,
    MembershipRole.accountant,
    MembershipRole.investor,
    MembershipRole.legal_advisor,
]


def _route_table() -> list[tuple[str, str]]:
    out = []
    for route in sales_endpoints.router.routes:
        assert isinstance(route, APIRoute), route
        path = re.sub(r"\{[^}]+\}", _ID, route.path)
        out.extend((m, path) for m in sorted(route.methods - {"HEAD", "OPTIONS"}))
    return out


ROUTES = _route_table()
MULTIPART = {"/leads/import/preview"}


def _call(client, method, path, headers=None):
    kwargs = {}
    if path in MULTIPART:
        kwargs["files"] = {"file": ("a.csv", b"name\nx\n", "text/csv")}
    elif method in ("POST", "PUT", "PATCH"):
        kwargs["json"] = {}
    return client.request(method, BASE + path, headers=headers or {}, **kwargs)


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _member(db, startup, role):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, startup, role=role)
    db.flush()
    return _headers(u, startup)


@pytest.fixture
def startup(db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    create_membership(db, owner, s, role=MembershipRole.founder)
    db.flush()
    return s


def test_route_table_is_enumerated_and_not_trivially_small():
    assert len(ROUTES) >= 30
    assert ("GET", "") in ROUTES  # the overview


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_route_requires_auth(client, method, path):
    r = _call(client, method, path)
    assert r.status_code == 401, (method, path, r.text)


@pytest.mark.parametrize("role", NON_SALES_ROLES)
def test_every_route_is_403_for_non_sales_roles(client, db, startup, role):
    h = _member(db, startup, role)
    bad = []
    for method, path in ROUTES:
        r = _call(client, method, path, h)
        if r.status_code != 403:
            bad.append((method, path, r.status_code))
    assert bad == []


@pytest.mark.parametrize("role", [MembershipRole.founder, MembershipRole.team_member])
def test_sweep_is_meaningful_sales_roles_are_never_403(client, db, startup, role):
    h = _member(db, startup, role)
    seen = {}
    for method, path in ROUTES:
        r = _call(client, method, path, h)
        seen[(method, path)] = r.status_code
    assert {k: v for k, v in seen.items() if v == 403} == {}
    assert all(v in (200, 404, 422) for v in seen.values()), seen

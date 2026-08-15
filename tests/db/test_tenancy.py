import uuid

import pytest

from app.core.errors import Forbidden
from app.db.models.enums import MembershipRole
from app.db.tenancy import resolve_workspace
from tests.factories import create_membership, create_startup, create_user


def test_resolve_workspace_ok(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    m = resolve_workspace(db, u, s.id)
    assert m.role == MembershipRole.founder


def test_resolve_workspace_forbidden_for_non_member(db):
    u = create_user(db)
    other = create_user(db)
    s = create_startup(db, owner=other)
    create_membership(db, other, s, role=MembershipRole.founder)
    db.flush()
    with pytest.raises(Forbidden):
        resolve_workspace(db, u, s.id)


def test_resolve_workspace_forbidden_for_unknown_workspace(db):
    u = create_user(db)
    db.flush()
    with pytest.raises(Forbidden):
        resolve_workspace(db, u, uuid.uuid4())

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_verified_user
from app.core.security import create_access_token
from app.db.models.enums import UserStatus
from app.db.models.user import User
from app.db.session import get_db
from tests.factories import create_user


def _app():
    app = FastAPI()
    from app.core.errors import register_exception_handlers

    register_exception_handlers(app)

    @app.get("/vok")
    def vok(u: User = Depends(get_verified_user)):  # noqa: B008
        return {"e": u.email}

    return app


def test_verified_user_ok(db):
    from datetime import UTC, datetime

    u = create_user(db, status=UserStatus.active, email_verified_at=datetime.now(UTC))
    db.commit()
    app = _app()
    app.dependency_overrides[get_db] = lambda: db
    r = TestClient(app).get(
        "/vok", headers={"Authorization": f"Bearer {create_access_token(str(u.id))}"}
    )
    assert r.status_code == 200


def test_unverified_user_403(db):
    u = create_user(db, status=UserStatus.pending_verification)
    db.commit()
    app = _app()
    app.dependency_overrides[get_db] = lambda: db
    r = TestClient(app).get(
        "/vok", headers={"Authorization": f"Bearer {create_access_token(str(u.id))}"}
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"

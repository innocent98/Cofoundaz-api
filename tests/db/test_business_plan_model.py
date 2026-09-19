from app.db.models.business import BusinessPlan
from app.db.models.enums import BusinessPlanStatus
from tests.factories import create_startup, create_user


def test_business_plan_row_roundtrips(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    plan = BusinessPlan(startup_id=s.id, status=BusinessPlanStatus.generating, created_by_id=u.id)
    db.add(plan)
    db.flush()
    got = db.get(BusinessPlan, plan.id)
    assert got.status == BusinessPlanStatus.generating
    assert got.document_id is None

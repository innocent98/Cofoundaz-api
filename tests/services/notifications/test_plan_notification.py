from app.services.notifications.categories import category_for
from app.services.notifications.registry import SPECS


def test_plan_generated_notifies_and_maps_to_business():
    assert "business.plan.generated" in SPECS
    assert category_for("business.plan.generated") == "business"

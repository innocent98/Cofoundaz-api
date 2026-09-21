from pydantic import BaseModel, field_validator

from app.services.notifications.categories import CATEGORIES


class PreferencesUpdate(BaseModel):
    master_email: bool | None = None
    categories: dict[str, bool] | None = None

    @field_validator("categories")
    @classmethod
    def _known_categories(cls, v: dict[str, bool] | None) -> dict[str, bool] | None:
        if v:
            unknown = set(v) - set(CATEGORIES)
            if unknown:
                raise ValueError(f"unknown categories: {sorted(unknown)}")
        return v

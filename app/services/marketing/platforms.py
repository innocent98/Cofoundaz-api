"""Copy length limits per platform (D-MKT-3). A fixed table: the limit is stated in the prompt and
returned with the generation so the FE can show `n / limit` per variant."""

from typing import NamedTuple

from app.db.models.enums import CopyPlatform


class PlatformLimit(NamedTuple):
    key: CopyPlatform
    label: str
    limit: int
    # "hard": the platform rejects longer text. "recommended": longer text works but is truncated
    # or performs badly.
    kind: str


PLATFORM_LIMITS: dict[CopyPlatform, PlatformLimit] = {
    p.key: p
    for p in (
        PlatformLimit(CopyPlatform.x, "X", 280, "hard"),
        PlatformLimit(CopyPlatform.instagram, "Instagram caption", 2200, "hard"),
        PlatformLimit(CopyPlatform.linkedin, "LinkedIn post", 3000, "hard"),
        PlatformLimit(CopyPlatform.facebook, "Facebook post", 500, "recommended"),
        PlatformLimit(CopyPlatform.whatsapp, "WhatsApp message", 1024, "hard"),
        PlatformLimit(CopyPlatform.google_ads_headline, "Google Ads headline", 30, "hard"),
        PlatformLimit(CopyPlatform.google_ads_description, "Google Ads description", 90, "hard"),
        PlatformLimit(CopyPlatform.email_subject, "Email subject line", 60, "recommended"),
    )
}


def limit_for(platform: str | None) -> PlatformLimit | None:
    try:
        return PLATFORM_LIMITS[CopyPlatform(platform)] if platform else None
    except ValueError:
        return None

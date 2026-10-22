"""The entity-formation catalog: jurisdictions and their incorporation steps, defined in code.

Nothing here is stored in the database — only a startup's *progress* is
(`legal_formation_progress`). Modelled on `app/services/health_score/config.py`, which is the
house pattern for a code-defined config.

Only the two jurisdictions with real content are supported (spec D6). The FE prototype offers
four, but gives Nigeria, Kenya and Ghana the same generic placeholder steps, and shipping
placeholder legal steps as if they were guidance is worse than not supporting the jurisdiction.
"""

from datetime import date
from typing import NamedTuple

CATALOG_VERSION = 1

# The figures below are transcribed from the FE prototype and are NOT verified. The lead verifies
# them before production; until then this stays None, and callers are told the figures are
# estimates (spec D6).
CATALOG_REVIEWED_ON: date | None = None

# Adapted from the PRD's §1.4 standard disclaimer. The PRD string is written for *AI* output
# ("This is AI-assisted guidance, …"); nothing in this catalog is AI-generated, so that clause
# would be untrue here. The substance — not professional advice, involve a licensed professional —
# is kept verbatim in spirit.
ESTIMATE_DISCLAIMER = (
    "These steps, timings and fees are general guidance, not professional legal advice. "
    "Figures are estimates and change. For anything non-standard, involve a licensed "
    "professional — you can find one in the Marketplace."
)


class FormationStepDef(NamedTuple):
    key: str
    title: str
    authority: str
    est_days: int
    # Money follows Finance: integer minor units + an ISO-4217 code, never floats (spec D5).
    # Both null together means "we do not know the fee" — never a zero that reads as "free".
    est_cost_minor: int | None
    est_cost_currency: str | None


JURISDICTIONS: dict[str, str] = {
    "NG": "Nigeria",
    "US-DE": "Delaware, USA",
}

STEPS: dict[str, tuple[FormationStepDef, ...]] = {
    "NG": (
        FormationStepDef("reserve_name", "Reserve company name", "CAC", 1, 50_000, "NGN"),
        FormationStepDef(
            "file_incorporation",
            "File incorporation documents",
            "CAC",
            3,
            2_000_000,
            "NGN",
        ),
        FormationStepDef("tax_id", "Get Tax Identification Number", "FIRS", 5, None, None),
        FormationStepDef(
            "bank_account", "Open a corporate bank account", "Your bank", 2, None, None
        ),
    ),
    "US-DE": (
        FormationStepDef(
            "certificate_of_incorporation",
            "File Certificate of Incorporation",
            "Delaware Secretary of State",
            1,
            None,
            None,
        ),
        FormationStepDef("ein", "Obtain EIN", "IRS", 2, None, None),
        FormationStepDef(
            "bank_account", "Open a corporate bank account", "Mercury / Brex", 2, None, None
        ),
    ),
}


def steps_for(jurisdiction: str) -> tuple[FormationStepDef, ...] | None:
    """The catalog steps for a jurisdiction code, or None when it is not supported.

    Codes are exact: `"ng"` is not `"NG"`. Callers turn None into a 404 (spec D3) rather than a
    422 — the code names a resource in a catalog, and 404 keeps the catalog non-enumerable.
    """
    return STEPS.get(jurisdiction)

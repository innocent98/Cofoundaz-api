"""The Overview and By-hub reads, built only from `service.resolve` (so authorisation and the
cache apply to every number), plus rule-based "Working / Watch" notes (no AI in slice A)."""

from typing import Any

from sqlalchemy.orm import Session

from app.db.models.enums import MembershipRole
from app.db.models.startup import Startup
from app.services.analytics import catalog as cat
from app.services.analytics import periods as per
from app.services.analytics import service as svc

HUB_TILES: dict[str, tuple[str, ...]] = {
    "finance": ("finance.revenue", "finance.net_burn", "finance.runway_months"),
    "sales": ("sales.pipeline_value", "sales.deals_won", "sales.win_rate"),
    "marketing": ("marketing.impressions", "marketing.ctr", "marketing.conversions"),
    "validation": ("validation.assumptions_validated_pct",),
    "health": ("health.score",),
    "execution": ("execution.tasks_done", "execution.milestones_done_pct"),
    "funding": ("funding.investors_active",),
}
CHART_PREFERENCE = (
    ("finance.revenue", "finance.expenses"),
    ("sales.closed_revenue", "sales.deals_won"),
    ("marketing.clicks", "marketing.conversions"),
)


def _ok(card: dict[str, Any]) -> bool:
    return card.get("status") == "ok"


def _pct(card: dict[str, Any]) -> float | None:
    c = card.get("compare") or {}
    return c.get("delta_pct")


def overview(
    db: Session, startup: Startup, role: MembershipRole, p: per.Period, compare_mode: str
) -> dict[str, Any]:
    cards = []
    for key in cat.HEADLINE:
        hub = key.split(".")[0]
        if cat.can_see(role, hub):
            cards.append(svc.resolve(db, startup, role, key, p, compare_mode, with_series=True))
    if cat.can_see(role, "custom"):
        first = svc.custom_metrics(db, startup.id)[:1]
        for c in first:
            cards.append(svc.resolve(db, startup, role, c.key, p, compare_mode, with_series=True))
    chart = None
    for keys in CHART_PREFERENCE:
        if all(cat.can_see(role, k.split(".")[0]) for k in keys):
            chart = {
                "series": [
                    svc.resolve(db, startup, role, k, p, compare_mode, with_series=True)
                    for k in keys
                ]
            }
            break
    extras = {
        k: svc.resolve(db, startup, role, k, p, compare_mode)
        for k in ("sales.win_rate", "marketing.ctr")
        if cat.can_see(role, k.split(".")[0])
    }
    by_key = {c["key"]: c for c in cards} | extras
    cmp = per.comparison(p, compare_mode)
    return {
        "period": p.iso(),
        "compare_period": cmp.iso() if cmp else None,
        "currency": svc.CURRENCY,
        "cards": cards,
        "chart": chart,
        "insights": insights(by_key),
        "hubs": [
            {"hub": h, "label": cat.HUB_LABELS[h], "path": cat.HUB_PATHS[h]}
            for h in cat.visible_hubs(role)
        ],
    }


def insights(by_key: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic notes from the numbers on screen. Each links to its source."""
    notes: list[dict[str, Any]] = []

    def add(kind: str, text: str, key: str) -> None:
        card = by_key.get(key, {})
        notes.append({"kind": kind, "text": text, "source": key, "hub_path": card.get("hub_path")})

    rev, burn = by_key.get("finance.revenue"), by_key.get("finance.net_burn")
    run = by_key.get("finance.runway_months")
    rp = _pct(rev) if rev and _ok(rev) else None
    bp = _pct(burn) if burn and _ok(burn) else None
    if rp is not None:
        if rp >= 10:
            add(
                "working", f"Revenue grew {rp:g}% against the comparison period.", "finance.revenue"
            )
        elif rp <= -10:
            add(
                "watch",
                f"Revenue fell {abs(rp):g}% against the comparison period.",
                "finance.revenue",
            )
    if rp is not None and bp is not None:
        if bp > 0 and rp > 0:
            add("watch", f"Burn rose {bp:g}% while revenue grew {rp:g}%.", "finance.net_burn")
        elif bp < 0 and rp > 0:
            add(
                "working", f"Burn fell {abs(bp):g}% while revenue grew {rp:g}%.", "finance.net_burn"
            )
    if run and _ok(run) and run["value"] is not None and run["value"] < 6:
        add(
            "watch",
            f"Runway is {run['value']:g} months, under the six-month line.",
            "finance.runway_months",
        )
    wr = by_key.get("sales.win_rate")
    if wr and _ok(wr) and (wr.get("compare") or {}).get("delta") not in (None, 0):
        d = wr["compare"]["delta"]
        add(
            "working" if d > 0 else "watch",
            f"Win rate {'rose' if d > 0 else 'fell'} {abs(d):g} points to {wr['value']:g}%.",
            "sales.win_rate",
        )
    ctr = by_key.get("marketing.ctr")
    if ctr and _ok(ctr) and (ctr.get("compare") or {}).get("delta") not in (None, 0):
        d = ctr["compare"]["delta"]
        add(
            "working" if d > 0 else "watch",
            f"Click-through rate {'rose' if d > 0 else 'fell'} {abs(d):g} points to {ctr['value']:g}%.",
            "marketing.ctr",
        )
    return notes[:6]


def by_hub(
    db: Session,
    startup: Startup,
    role: MembershipRole,
    p: per.Period,
    compare_mode: str,
    hub: str | None,
) -> dict[str, Any]:
    visible = cat.visible_hubs(role)
    if hub is not None and hub not in cat.HUB_ROLES:
        from app.services.analytics.errors import validation

        raise validation("hub", "Unknown hub.")
    if hub is not None and hub not in visible:
        from app.core.errors import Forbidden

        raise Forbidden()
    out = []
    for h in [hub] if hub else visible:
        if h == "custom":
            keys = tuple(c.key for c in svc.custom_metrics(db, startup.id))
        else:
            keys = HUB_TILES.get(h, ())
        out.append(
            {
                "hub": h,
                "label": cat.HUB_LABELS[h],
                "path": cat.HUB_PATHS[h],
                "tiles": [svc.resolve(db, startup, role, k, p, compare_mode) for k in keys],
            }
        )
    cmp = per.comparison(p, compare_mode)
    return {"period": p.iso(), "compare_period": cmp.iso() if cmp else None, "hubs": out}

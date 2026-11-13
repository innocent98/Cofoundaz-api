"""Grants catalogue loader (slice E): `content/funding/grants-catalogue.csv` -> `grants`, by slug.

The CSV is the human-verified draft: every row keeps `verified_on` and `source_url`. The loader
validates the whole file and raises `CatalogueError` listing every problem, so a bad file fails the
sync (and CI) rather than shipping. `sync_catalogue` is idempotent: a second run changes nothing,
and a row that left the file is switched off (`active = false`), never deleted, because a workspace
may already track or have matched it.

Run it with `python -m app.services.funding.grants_catalogue` (the deploy `migrate` step does).
"""

import csv
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.grant import Grant

CATALOGUE_PATH = (
    Path(__file__).resolve().parents[3] / "content" / "funding" / "grants-catalogue.csv"
)
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,78}$")
COLUMNS = (
    "id,name,funder,kind,amount_min,amount_max,currency,amount_note,deadline,"
    "status_on_verified_date,regions,sectors,stages,eligibility,source_url,verified_on,"
    "verification,notes"
).split(",")
CURRENCIES = {"NGN", "USD", "EUR", "GBP", "CAD"}

# Named countries the prefilter understands (ISO 3166 alpha-2). Anything else in a region text is
# read as "open to everyone" rather than guessed at.
COUNTRIES = {
    "nigeria": "NG",
    "lagos": "NG",
    "egypt": "EG",
    "kenya": "KE",
    "rwanda": "RW",
    "south africa": "ZA",
    "tanzania": "TZ",
    "ghana": "GH",
    "uganda": "UG",
}
AFRICA = frozenset(
    "DZ AO BJ BW BF BI CV CM CF TD KM CG CD CI DJ EG GQ ER SZ ET GA GM GH GN GW KE LS LR LY MG MW "
    "ML MR MU MA MZ NA NE NG RW ST SN SC SL SO ZA SS SD TZ TG TN UG ZM ZW".split()
)


class CatalogueError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass
class Row:
    values: dict[str, Any]


def _minor(raw: str, where: str, problems: list[str]) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        problems.append(f"{where}: amount '{raw}' is not a whole number")
        return None
    if value < 0:
        problems.append(f"{where}: amount is negative")
    return value * 100


def derive_regions(regions: str) -> tuple[list[str], bool]:
    """`(named country codes, pan_africa)` from the stated text. Empty codes and not pan-Africa
    means the text names no country we know, which the prefilter treats as open."""
    low = regions.lower()
    codes = sorted({code for name, code in COUNTRIES.items() if name in low})
    # "Africa" as a word, but not "South Africa" on its own.
    pan = bool(re.search(r"(?<!south )\bafrica\b", low))
    return codes, pan


def _check_required(r: dict[str, str], where: str, problems: list[str]) -> None:
    for col in ("name", "funder", "kind", "source_url", "verified_on"):
        if not (r[col] or "").strip():
            problems.append(f"{where}: {col} is required")
    if not (r["source_url"] or "").startswith("https://"):
        problems.append(f"{where}: source_url must be an https link")


def load_catalogue(path: Path = CATALOGUE_PATH) -> list[dict[str, Any]]:
    problems: list[str] = []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if list(reader.fieldnames or []) != COLUMNS:
            raise CatalogueError([f"{path.name}: columns must be exactly {','.join(COLUMNS)}"])
        for n, r in enumerate(reader, start=2):
            where = f"row {n} ({r.get('id') or '?'})"
            slug = (r["id"] or "").strip()
            if not SLUG_RE.match(slug):
                problems.append(f"{where}: id must be a lowercase slug")
            if slug in seen:
                problems.append(f"{where}: duplicate id")
            seen.add(slug)
            _check_required(r, where, problems)
            try:
                verified_on = date.fromisoformat((r["verified_on"] or "").strip())
            except ValueError:
                problems.append(f"{where}: verified_on must be YYYY-MM-DD")
                verified_on = date.today()
            if r["verification"] not in ("verified", "partial"):
                problems.append(f"{where}: verification must be verified or partial")
            cur = (r["currency"] or "").strip() or None
            if cur and cur not in CURRENCIES:
                problems.append(f"{where}: currency {cur} is not supported")
            lo = _minor(r["amount_min"], where, problems)
            hi = _minor(r["amount_max"], where, problems)
            if lo is not None and hi is not None and lo > hi:
                problems.append(f"{where}: amount_min is above amount_max")
            if (lo is not None or hi is not None) and cur is None:
                problems.append(f"{where}: an amount needs a currency")
            codes, pan = derive_regions(r["regions"] or "")
            out.append(
                {
                    "slug": slug,
                    "name": r["name"].strip(),
                    "funder": r["funder"].strip(),
                    "kind": r["kind"].strip(),
                    "amount_min_minor": lo,
                    "amount_max_minor": hi,
                    "currency": cur,
                    "amount_note": (r["amount_note"] or "").strip() or None,
                    "deadline_text": (r["deadline"] or "").strip(),
                    "status_text": (r["status_on_verified_date"] or "").strip(),
                    "regions": (r["regions"] or "").strip(),
                    "sectors": (r["sectors"] or "").strip(),
                    "stages": (r["stages"] or "").strip(),
                    "eligibility": (r["eligibility"] or "").strip(),
                    "source_url": (r["source_url"] or "").strip(),
                    "verified_on": verified_on,
                    "verification": r["verification"],
                    "notes": (r["notes"] or "").strip() or None,
                    "country_codes": codes,
                    "pan_africa": pan,
                    "in_kind": "in-kind" in r["kind"].lower(),
                    "active": True,
                }
            )
    if problems:
        raise CatalogueError(problems)
    return out


def sync_catalogue(db: Session, rows: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """Upsert the file into `grants`. Returns counts of created, updated and retired rows."""
    rows = load_catalogue() if rows is None else rows
    existing = {g.slug: g for g in db.query(Grant).all()}
    created = updated = retired = 0
    for row in rows:
        grant = existing.get(row["slug"])
        if grant is None:
            db.add(Grant(**row))
            created += 1
            continue
        changed = False
        for key, value in row.items():
            if getattr(grant, key) != value:
                setattr(grant, key, value)
                changed = True
        updated += int(changed)
    keep = {r["slug"] for r in rows}
    for slug, grant in existing.items():
        if slug not in keep and grant.active:
            grant.active = False
            retired += 1
    db.flush()
    return {"created": created, "updated": updated, "retired": retired}


def main() -> int:
    from app.db.session import SessionLocal

    try:
        rows = load_catalogue()
    except CatalogueError as exc:
        print("grants catalogue is invalid:\n" + "\n".join(exc.problems), file=sys.stderr)
        return 1
    with SessionLocal() as db:
        result = sync_catalogue(db, rows)
        db.commit()
    print(f"grants catalogue: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

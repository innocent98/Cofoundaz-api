"""The grants catalogue loader (slice E): validation, derived regions, idempotent sync."""

import csv
from datetime import date

import pytest

from app.db.models.grant import Grant
from app.services.funding import grants_catalogue as cat

HEADER = cat.COLUMNS


def _write(path, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for r in rows:
            w.writerow([r.get(c, "") for c in HEADER])


def _row(**kw):
    base = {
        "id": "demo-grant",
        "name": "Demo Grant",
        "funder": "Demo Foundation",
        "kind": "cash grant",
        "amount_max": "5000",
        "currency": "USD",
        "deadline": "annual",
        "status_on_verified_date": "open",
        "regions": "Nigeria, Kenya",
        "source_url": "https://example.org/demo",
        "verified_on": "2026-11-12",
        "verification": "verified",
    }
    base.update(kw)
    return base


def test_the_shipped_catalogue_is_valid_and_every_row_keeps_its_source():
    rows = cat.load_catalogue()
    assert len(rows) == 15
    assert all(r["source_url"].startswith("https://") and r["verified_on"] for r in rows)
    assert {r["verification"] for r in rows} == {"verified", "partial"}
    tef = next(r for r in rows if r["slug"] == "tef-entrepreneurship")
    assert tef["amount_max_minor"] == 500_000 and tef["currency"] == "USD" and tef["pan_africa"]
    assert next(r for r in rows if r["slug"] == "aws-activate")["in_kind"] is True


def test_regions_are_read_into_countries_and_pan_africa():
    assert cat.derive_regions("Nigeria, Egypt, Kenya, Rwanda, South Africa") == (
        ["EG", "KE", "NG", "RW", "ZA"],
        False,
    )
    assert cat.derive_regions("Lagos, Nigeria (core business in Lagos)") == (["NG"], False)
    assert cat.derive_regions("sub-Saharan Africa") == ([], True)
    assert cat.derive_regions("South Africa only") == (["ZA"], False)
    assert cat.derive_regions("global") == ([], False)


def test_every_problem_is_listed_at_once(tmp_path):
    path = tmp_path / "g.csv"
    _write(
        path,
        [
            _row(id="Bad Slug", source_url="http://x", verification="maybe"),
            _row(id="two", amount_max="12x"),
            _row(id="two", amount_min="9", amount_max="5"),
            _row(id="three", currency="", amount_max="5"),
        ],
    )
    with pytest.raises(cat.CatalogueError) as err:
        cat.load_catalogue(path)
    text = "\n".join(err.value.problems)
    for needle in (
        "lowercase slug",
        "https",
        "verified or partial",
        "not a whole number",
        "duplicate id",
        "above amount_max",
        "needs a currency",
    ):
        assert needle in text


def test_a_changed_header_is_refused(tmp_path):
    path = tmp_path / "g.csv"
    path.write_text("id,name\nx,y\n")
    with pytest.raises(cat.CatalogueError):
        cat.load_catalogue(path)


def test_sync_is_idempotent_updates_and_retires(db, tmp_path):
    path = tmp_path / "g.csv"
    _write(path, [_row(), _row(id="other", name="Other")])
    first = cat.sync_catalogue(db, cat.load_catalogue(path))
    assert first["created"] >= 2
    again = cat.sync_catalogue(db, cat.load_catalogue(path))
    assert again["created"] == 0 and again["updated"] == 0
    _write(path, [_row(name="Renamed")])
    third = cat.sync_catalogue(db, cat.load_catalogue(path))
    assert third["updated"] == 1 and third["retired"] >= 1
    assert db.query(Grant).filter_by(slug="demo-grant").one().name == "Renamed"
    assert db.query(Grant).filter_by(slug="other").one().active is False
    assert db.query(Grant).filter_by(slug="demo-grant").one().verified_on == date(2026, 11, 12)

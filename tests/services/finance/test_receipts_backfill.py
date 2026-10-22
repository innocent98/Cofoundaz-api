"""One-off backfill that moves legacy public receipts behind authentication."""

import datetime as dt

from app.db.models.expense import Expense
from app.services.finance.receipts_backfill import privatise_legacy_receipts
from tests.factories import create_startup, create_user


class _Store:
    def __init__(self, fail_on: set[str] | None = None):
        self.moved: list[str] = []
        self.fail_on = fail_on or set()

    def make_private(self, key):
        if key in self.fail_on:
            raise RuntimeError("provider down")
        self.moved.append(key)


def _expense(db, startup, name, *, key, private):
    exp = Expense(
        startup_id=startup.id,
        vendor=name,
        category="Software",
        expense_date=dt.date(2026, 1, 5),
        amount_minor=100,
        currency="NGN",
        receipt_key=key,
        receipt_url="https://res.cloudinary.com/demo/raw/upload/" + key if key else None,
        receipt_private=private,
    )
    db.add(exp)
    db.flush()
    return exp


def _setup(db):
    return create_startup(db, owner=create_user(db))


def test_moves_only_legacy_receipts_and_clears_their_url(db):
    s = _setup(db)
    legacy = [_expense(db, s, f"L{i}", key=f"receipts/a/{i}.pdf", private=False) for i in range(3)]
    done = _expense(db, s, "Done", key="receipts/a/done.pdf", private=True)
    none = _expense(db, s, "None", key=None, private=False)
    store = _Store()
    result = privatise_legacy_receipts(db, store, batch_size=2)
    assert (result.moved, result.failed, result.scanned) == (3, 0, 3)
    assert sorted(store.moved) == sorted(e.receipt_key for e in legacy)
    for e in legacy:
        db.refresh(e)
        assert e.receipt_private is True and e.receipt_url is None
    db.refresh(done)
    db.refresh(none)
    assert done.receipt_key == "receipts/a/done.pdf" and none.receipt_key is None


def test_a_failure_is_counted_and_does_not_stop_the_run(db):
    s = _setup(db)
    bad = _expense(db, s, "Bad", key="receipts/a/bad.pdf", private=False)
    good = _expense(db, s, "Good", key="receipts/a/good.pdf", private=False)
    store = _Store(fail_on={"receipts/a/bad.pdf"})
    result = privatise_legacy_receipts(db, store, batch_size=1)
    assert (result.moved, result.failed) == (1, 1)
    db.refresh(bad)
    db.refresh(good)
    assert bad.receipt_private is False and good.receipt_private is True


def test_rerun_is_a_no_op_and_a_failed_row_is_retried(db):
    s = _setup(db)
    bad = _expense(db, s, "Bad", key="receipts/a/bad.pdf", private=False)
    first = privatise_legacy_receipts(db, _Store(fail_on={bad.receipt_key}))
    assert (first.moved, first.failed) == (0, 1)
    second = _Store()
    assert privatise_legacy_receipts(db, second).moved == 1
    assert second.moved == ["receipts/a/bad.pdf"]
    again = _Store()
    assert privatise_legacy_receipts(db, again).scanned == 0 and again.moved == []


def test_empty_database_is_fine(db):
    assert privatise_legacy_receipts(db, _Store()).scanned == 0

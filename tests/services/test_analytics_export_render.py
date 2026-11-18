"""Analytics export renderer (slice C): fail-closed flattening, formula guard, escaping."""

import io
import zipfile

from pypdf import PdfReader

from app.services.analytics import render as rnd

CTX = rnd.Context("Acme <Ltd>", "2026-10-01", "2026-10-31", "2026-11-01 08:00 UTC", "Ada")


def _metric(value, *, hub="finance", unit="money", status="ok", label="Revenue"):
    return {
        "block_id": "b1",
        "type": "metric",
        "hub": hub,
        "title": None,
        "result": {
            "key": "k",
            "label": label,
            "hub": hub,
            "unit": unit,
            "currency": "NGN" if unit == "money" else None,
            "status": status,
            "value": value,
            "period": {"from": "2026-10-01", "to": "2026-10-31"},
            "compare": {"value": 100, "delta": value - 100, "delta_pct": None},
            "series": [],
        },
    }


def test_rows_fail_closed_on_locked_and_unknown_shapes():
    view = {
        "report": {"name": "R"},
        "results": [
            {"id": "x", "type": "metric", "locked": True, "needs": "sales", "needs_label": "Sales"},
            {"block_id": "y", "type": "metric", "hub": "sales"},  # no result at all
            {
                "block_id": "z",
                "type": "metric",
                "result": {"status": "locked", "label": "Pipe", "value": 42, "needs": "sales"},
            },
            _metric(500_000),
        ],
    }
    rows = rnd.rows_from(view)
    assert [r.kind for r in rows] == ["locked", "locked", "locked", "metric"]
    assert all(r.value is None for r in rows[:3])
    assert rnd.hubs_in(rows) == ["finance"]


def test_csv_guards_formula_cells_and_keeps_numbers_numeric():
    view = {"report": {"name": "=HYPERLINK(1)"}, "results": [_metric(500_000, label="@SUM(A1)")]}
    view["results"][0]["title"] = "=cmd|' /C calc'!A0"
    text = rnd.render_csv(view, CTX, include_metadata=True).decode("utf-8-sig")
    assert "'=cmd|" in text and "'=HYPERLINK(1)" in text
    assert ",5000.00," in text  # money in major units, unquoted
    assert "\n=" not in text and ",=" not in text


def test_xlsx_text_is_inline_never_formula_and_pdf_escapes_markup():
    view = {
        "report": {"name": "<b>Board</b>"},
        "results": [
            _metric(250, unit="count", hub="marketing", label="Clicks"),
            {
                "block_id": "t",
                "type": "text",
                "title": "=1+1",
                "text": "<script>x</script>\n- item",
            },
        ],
    }
    with zipfile.ZipFile(io.BytesIO(rnd.render_xlsx(view, CTX))) as z:
        xml = "".join(z.read(n).decode() for n in z.namelist())
    assert "<f>" not in xml and "&lt;script&gt;" in xml and 's="3"' in xml
    pdf = rnd.render_pdf(view, CTX)
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "<b>Board</b>" in text and "<script>x</script>" in text and "Acme <Ltd>" in text


def test_value_formatting():
    row = rnd.rows_from({"results": [_metric(123_456)]})[0]
    assert rnd.fmt(row, row.value) == "₦1,234.56"
    assert rnd.cell_value(row, row.value).__str__() == "1234.56"


def test_metric_without_hub_is_recorded_as_custom():
    """Review L1: a custom metric deleted after the run has no hub; it must still be covered by
    the download re-check, as "custom", not dropped from `hubs`."""
    m = _metric(5, unit="count")
    m["hub"] = None
    m["result"]["hub"] = None
    rows = rnd.rows_from({"results": [m]})
    assert rnd.hubs_in(rows) == ["custom"]

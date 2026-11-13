"""Render a frozen run into a file (slice C): PDF, XLSX, or CSV for one data table.

Input is always the run's snapshot AS THE REQUESTER MAY READ IT (`runs.view_snapshot`), never the
stored copy and never a fresh query, so the file says exactly what the screen said at run time and
a locked block reaches the file only as "locked", with no number (D-ANA-4).

Every user-controlled string (report name, block titles, text blocks, custom metric labels) is
escaped for the PDF (reportlab's mini-HTML), written as inline text in XLSX (never a formula) and
prefixed with an apostrophe in CSV when it starts like a formula (spec section 10, item 4).
"""

import csv
import io
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from html import escape
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.analytics import catalog as cat
from app.services.documents.export import _pdf_text
from app.services.documents.export import blocks as text_blocks
from app.services.finance.xlsx import Cell, build_xlsx

PDF_TYPE = "application/pdf"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_TYPE = "text/csv; charset=utf-8"
CONTENT_TYPES = {"pdf": PDF_TYPE, "xlsx": XLSX_TYPE, "csv": CSV_TYPE}
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
_SYMBOL = {"NGN": "₦", "USD": "$"}
_STATUS_TEXT = {
    "no_data": "No data yet",
    "not_burning": "Not burning cash",
    "out_of_cash": "Out of cash",
    "error": "Could not compute this metric",
    "unknown": "Metric no longer available",
}
SUMMARY_HEADER: list[Cell] = [
    "Metric",
    "Hub",
    "Value",
    "Unit",
    "Currency",
    "Previous period",
    "Change",
    "Change %",
    "Status",
    "From",
    "To",
]


@dataclass(frozen=True)
class Context:
    workspace: str
    period_from: str
    period_to: str
    as_of: str
    generated_by: str


@dataclass(frozen=True)
class Row:
    """One block of the run, flattened for rendering. `locked` rows carry no value at all."""

    kind: str  # metric | text | locked
    title: str
    hub: str | None = None
    unit: str | None = None
    currency: str | None = None
    status: str | None = None
    value: Any = None
    compare: dict[str, Any] | None = None
    series: tuple[dict[str, Any], ...] = ()
    period: dict[str, str] | None = None
    text: str = ""
    needs_label: str = ""


def _locked(title: str, needs: str | None, needs_label: str | None) -> Row:
    label = needs_label or cat.HUB_LABELS.get(str(needs), "More")
    return Row("locked", title, needs_label=label)


def rows_from(view: dict[str, Any]) -> list[Row]:
    """Flatten a viewed snapshot. Fails closed: anything that is not a readable metric result
    (a locked stub, a `locked` result, an unknown shape) becomes a row with no number."""
    out: list[Row] = []
    for r in view.get("results") or []:
        if r.get("type") == "text":
            out.append(Row("text", r.get("title") or "", text=r.get("text") or ""))
            continue
        if r.get("locked"):
            out.append(_locked("Locked block", r.get("needs"), r.get("needs_label")))
            continue
        res = r.get("result")
        if not isinstance(res, dict):
            out.append(_locked("Locked block", None, None))
            continue
        title = r.get("title") or res.get("label") or "Metric"
        status = str(res.get("status") or "unknown")
        if status == "locked":
            out.append(_locked(title, res.get("needs"), res.get("needs_label")))
            continue
        out.append(
            Row(
                "metric",
                title,
                hub=res.get("hub") or r.get("hub"),
                unit=res.get("unit"),
                currency=res.get("currency"),
                status=status,
                value=res.get("value") if status not in ("unknown", "error") else None,
                compare=res.get("compare") if status not in ("unknown", "error") else None,
                series=tuple(res.get("series") or ()),
                period=res.get("period"),
            )
        )
    return out


def hubs_in(rows: Iterable[Row]) -> list[str]:
    """The hubs whose numbers are in the file (a download re-checks every one of them)."""
    return sorted({r.hub for r in rows if r.kind == "metric" and r.hub})


# ---- values ----
def _major(minor: Any) -> Decimal:
    return (Decimal(str(minor)) / 100).quantize(Decimal("0.01"))


def cell_value(row: Row, v: Any) -> Cell:
    """A numeric cell: money in major units (the currency is its own column), the rest as is."""
    if v is None:
        return None
    if row.unit == "money":
        return _major(v)
    return Decimal(str(v)) if not isinstance(v, int) else v


def fmt(row: Row, v: Any) -> str:
    if v is None:
        return "-"
    if row.unit == "money":
        sym = _SYMBOL.get(row.currency or "", f"{row.currency or ''} ")
        return f"{sym}{_major(v):,.2f}"
    if row.unit == "percent":
        return f"{v}%"
    if row.unit == "months":
        return f"{v} months"
    if row.unit == "days":
        return f"{v} days"
    if row.unit == "score":
        return f"{v} / 100"
    return f"{v:,}" if isinstance(v, int) else str(v)


def _status_text(row: Row) -> str:
    if row.kind == "locked":
        return f"Locked: {row.needs_label} access needed"
    return _STATUS_TEXT.get(row.status or "", "") if row.status != "ok" else "OK"


# ---- tables (XLSX and CSV share these rows) ----
def summary_rows(rows: list[Row]) -> list[list[Cell]]:
    out: list[list[Cell]] = [list(SUMMARY_HEADER)]
    for r in rows:
        if r.kind == "text":
            continue
        if r.kind == "locked":
            out.append(
                [r.title, r.needs_label, None, None, None, None, None, None, _status_text(r)]
            )
            continue
        cmp = r.compare or {}
        p = r.period or {}
        out.append(
            [
                r.title,
                cat.HUB_LABELS.get(r.hub or "", r.hub),
                cell_value(r, r.value),
                r.unit,
                r.currency,
                cell_value(r, cmp.get("value")),
                cell_value(r, cmp.get("delta")),
                cmp.get("delta_pct"),
                _status_text(r),
                p.get("from"),
                p.get("to"),
            ]
        )
    return out


def _series_rows(r: Row) -> list[list[Cell]]:
    head: list[Cell] = ["From", "To", f"Value ({r.currency})" if r.currency else "Value"]
    return [head] + [[s.get("from"), s.get("to"), cell_value(r, s.get("value"))] for s in r.series]


def _about_rows(ctx: Context, title: str) -> list[list[Cell]]:
    return [
        ["Field", "Value"],
        ["Workspace", ctx.workspace],
        ["Report", title],
        ["Period from", ctx.period_from],
        ["Period to", ctx.period_to],
        ["Numbers as of", ctx.as_of],
        ["Generated by", ctx.generated_by],
        ["Money", "Major units (for example naira, not kobo); currency in its own column."],
        ["Note", "Frozen at the time of the run. Locked rows hold no numbers by design."],
    ]


def render_xlsx(view: dict[str, Any], ctx: Context, *, include_metadata: bool = True) -> bytes:
    rows = rows_from(view)
    title = str(view.get("report", {}).get("name") or "Report")
    sheets: list[tuple[str, list[list[Cell]]]] = [("Summary", summary_rows(rows))]
    for r in rows:
        if r.kind == "metric" and r.series:
            sheets.append((r.title, _series_rows(r)))
    notes: list[list[Cell]] = [[r.title, r.text] for r in rows if r.kind == "text"]
    if notes:
        sheets.append(("Notes", [["Title", "Text"], *notes]))
    if include_metadata:
        sheets.append(("About", _about_rows(ctx, title)))
    return build_xlsx(sheets)


def _csv_safe(v: Cell) -> Cell:
    if isinstance(v, str) and v.startswith(_FORMULA_STARTS):
        return "'" + v
    return v


def render_csv(view: dict[str, Any], ctx: Context, *, include_metadata: bool = False) -> bytes:
    """One table: the summary, one row per metric. With metadata, a short header block first."""
    rows = rows_from(view)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    if include_metadata:
        title = str(view.get("report", {}).get("name") or "Data tables")
        for k, v in _about_rows(ctx, title)[1:]:
            w.writerow([_csv_safe(k), _csv_safe(v)])
        w.writerow([])
    for line in summary_rows(rows):
        w.writerow([_csv_safe(c) for c in line])
    # BOM so Excel opens UTF-8 (the naira sign, accented names) correctly.
    return ("﻿" + buf.getvalue()).encode("utf-8")


# ---- PDF ----
def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(_pdf_text(text), quote=False).replace("\n", "<br/>"), style)


def _footer(canvas: Canvas, label: str) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(20 * mm, 12 * mm, _pdf_text(label)[:120])
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


_GRID = TableStyle(
    [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.grey),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
)


def _metric_flow(
    r: Row, heading: ParagraphStyle, big: ParagraphStyle, muted: ParagraphStyle, width: float
) -> list[Any]:
    flow: list[Any] = [_p(r.title, heading)]
    if r.kind == "locked":
        box = Table([[_p(f"Locked. {r.needs_label} access is needed to see this.", muted)]])
        box.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.lightgrey),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F4F6")),
                ]
            )
        )
        return [*flow, box]
    flow.append(_p(fmt(r, r.value) if r.status != "unknown" else "-", big))
    notes = []
    if r.status and r.status != "ok":
        notes.append(_status_text(r))
    cmp = r.compare or {}
    if cmp.get("delta") is not None:
        pct = f" ({cmp['delta_pct']}%)" if cmp.get("delta_pct") is not None else ""
        sign = "+" if cmp["delta"] >= 0 else "-"
        notes.append(
            f"{sign}{fmt(r, abs(cmp['delta']))}{pct} vs {fmt(r, cmp.get('value'))} "
            "the period before"
        )
    if r.hub:
        notes.append(f"Source: {cat.HUB_LABELS.get(r.hub, r.hub)}")
    if notes:
        flow.append(_p("  |  ".join(notes), muted))
    if r.series:
        data = [["From", "To", "Value"]] + [
            [str(s.get("from")), str(s.get("to")), _pdf_text(fmt(r, s.get("value")))]
            for s in r.series
        ]
        t = Table(data, colWidths=[width * 0.3, width * 0.3, width * 0.4], hAlign="LEFT")
        t.setStyle(_GRID)
        flow += [Spacer(1, 4), t]
    return flow


def render_pdf(view: dict[str, Any], ctx: Context) -> bytes:
    rows = rows_from(view)
    report = view.get("report", {})
    name = str(report.get("name") or "Report")
    styles = getSampleStyleSheet()
    title = ParagraphStyle("t", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=20)
    heading = ParagraphStyle(
        "h", parent=styles["Heading3"], fontName="Helvetica-Bold", fontSize=12, spaceBefore=10
    )
    body = ParagraphStyle("b", parent=styles["BodyText"], fontName="Helvetica", leading=14)
    big = ParagraphStyle("v", parent=body, fontName="Helvetica-Bold", fontSize=16, leading=20)
    muted = ParagraphStyle("m", parent=body, fontSize=9, leading=12, textColor=colors.grey)
    item = ParagraphStyle("li", parent=body, leftIndent=14, bulletIndent=4)
    width = A4[0] - 40 * mm

    flow: list[Any] = [_p(name, title)]
    if report.get("description"):
        flow.append(_p(str(report["description"]), body))
    flow.append(
        _p(
            f"{ctx.workspace}  |  {ctx.period_from} to {ctx.period_to}  |  "
            f"numbers as of {ctx.as_of}  |  generated by {ctx.generated_by}",
            muted,
        )
    )
    flow.append(Spacer(1, 8))
    for r in rows:
        if r.kind == "text":
            if r.title:
                flow.append(_p(r.title, heading))
            for kind, text in text_blocks(r.text):
                if kind == "li":
                    flow.append(
                        Paragraph(escape(_pdf_text(text), quote=False), item, bulletText="•")
                    )
                else:
                    flow.append(_p(text, body))
            continue
        flow += _metric_flow(r, heading, big, muted, width)
    if not rows:
        flow.append(_p("This report has no blocks.", body))
    label = f"{ctx.workspace}  |  {ctx.period_from} to {ctx.period_to}  |  as of {ctx.as_of}"
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=_pdf_text(name),
        author="Cofoundaz",
    )
    doc.build(
        flow,
        onFirstPage=lambda c, _d: _footer(c, label),
        onLaterPages=lambda c, _d: _footer(c, label),
    )
    return buf.getvalue()

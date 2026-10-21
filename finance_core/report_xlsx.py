"""finance-analyze (Skill 3) .xlsx report writer.

Renders the aggregation structures from finance_core.analyze into a workbook
whose tables import cleanly into Google Sheets: plain A1-header tables, no
merged cells in data ranges, frozen headers, autofilter on Details - plus
native openpyxl charts (pie for the dimension breakdown, column chart for the
period breakdown, and clustered BarChart3D / PieChart3D for multi-topic
comparisons).

openpyxl is the only third-party dependency; ensure_openpyxl() transparently
installs it on first use and degrades to clear manual instructions if that
is not possible.
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
import sys

from .analyze import DETAIL_COLUMNS, fmt_ils

_MONEY_FMT = "#,##0.00"
_PCT_FMT = "0.0%"

_MANUAL_INSTALL = (
    "[error] openpyxl is required to generate the workbook and could not be "
    "installed automatically.\n"
    "        Install it manually, then re-run:\n"
    "            python -m pip install openpyxl\n"
    "        (add --user if you lack admin rights)"
)


def ensure_openpyxl():
    """Import openpyxl, trying a quiet `pip install` once if it is missing.
    Returns the module or exits with manual instructions."""
    try:
        import openpyxl  # noqa: F401
        return openpyxl
    except ImportError:
        pass
    print("[info] openpyxl not found - attempting automatic install ...")
    for extra in ([], ["--user"]):
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "openpyxl", *extra],
                capture_output=True, text=True, timeout=300)
            if proc.returncode == 0:
                try:
                    import openpyxl  # noqa: F811
                    print(f"[info] installed openpyxl {openpyxl.__version__}")
                    return openpyxl
                except ImportError:
                    continue
        except (OSError, subprocess.SubprocessError):
            continue
    print(_MANUAL_INSTALL)
    raise SystemExit(2)


# ----------------------------------------------------------------- helpers

def _money_ws_headers(ws, row, headers):
    from openpyxl.styles import Font
    for col, title in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col, value=title)
        cell.font = Font(bold=True)
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def _autofit(ws, max_width: int = 55) -> None:
    from openpyxl.utils import get_column_letter
    widths: dict[int, int] = {}
    for row in ws.iter_rows():
        for cell in row:
            v = cell.value
            if v is None:
                continue
            # CJK/Hebrew glyphs are roughly double-width in Excel's metric
            length = sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(v))
            col = cell.column
            if length > widths.get(col, 0):
                widths[col] = min(length + 2, max_width)
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w


def _write_table(ws, start_row: int, headers: list[str], rows: list[list],
                 money_cols: tuple[int, ...] = (), pct_cols: tuple[int, ...] = ()) -> tuple[int, int]:
    """Write header+rows starting at start_row; returns (first_data_row,
    last_row). money/pct cols are 1-based indices formatted accordingly."""
    _money_ws_headers(ws, start_row, headers)
    r = start_row
    for rec in rows:
        r += 1
        for c, value in enumerate(rec, start=1):
            cell = ws.cell(row=r, column=c)
            if isinstance(value, (list, tuple)):
                value = ";".join(str(v) for v in value)  # e.g. ledger flags
            if value is None or value == "":
                continue
            cell.value = value
            if c in money_cols and isinstance(value, (int, float)):
                cell.number_format = _MONEY_FMT
            elif c in pct_cols and isinstance(value, (int, float)):
                cell.number_format = _PCT_FMT
    return start_row + 1, r


def _section_title(ws, row: int, text: str) -> int:
    from openpyxl.styles import Font
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = Font(bold=True, size=12)
    return row + 1


# ------------------------------------------------------------------- sheets

def _overview_sheet(wb, report: dict, context: dict) -> None:
    ws = wb.active
    ws.title = "Overview"
    t = report["totals"]
    n = report["notes"]
    topics_meta = context.get("topics_meta") or []
    pairs: list[tuple[str, object]] = [
        ("report", report["label"]),
        ("generated", _dt.datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("project", context.get("project", "")),
        ("ledger", context.get("ledger_path", "")),
        ("topics", "; ".join(
            f"{m['name']} ({m['kind']}, {m['ids']} ids)" for m in topics_meta) or "-"),
        ("scope", report["scope"]),
        ("split_by", report["split_by"]),
        ("split date field", report["date_field"]),
        ("chart dimension", report["chart_dim"] +
         (" (= group; buckets at identity)" if not report["bucket_differs"] else "")),
        ("chart top-N", report["top_n"]),
        ("refunds mode", context.get("refunds_mode", "")),
        ("comparison", context.get("compare_note", "")),
        ("category map", context.get("category_map_path", "") or "(fallback failed)"),
        ("", ""),
        ("rows analyzed", t["count"]),
        ("expense rows", t["expense_rows"]),
        ("income rows", t["income_rows"]),
        ("expenses total", t["expenses"]),
        ("income total", t["income"]),
        ("net", t["net"]),
        ("annihilated refund pairs", len(n["annihilated"]) // 2),
        ("excluded transfers (abs ₪)", t["transfers_ils"]),
        ("forced into expense/income (--force-include)", len(n.get("forced_include") or [])),
        ("no_fx_rate rows (not summed)", len(n["fx_gap"])),
        ("stale topic ids", len(n["stale_ids"])),
    ]
    trip = report.get("trip_comparison")
    if trip:
        months_label = "covered months (fixed range)" if trip.get("fixed_range") \
            else "covered transaction months"
        pairs.extend((
            ("trip topics", trip["trip_count"]),
            ("vacation topics", trip["vacation_count"]),
            ("trip total", trip["trip_total"]),
            ("vacation total", trip["vacation_total"]),
            ("average trip cost", trip["average_trip_cost"]),
            ("average vacation cost", trip["average_vacation_cost"]),
            (months_label, trip["month_count"]),
        ))
    for w in n["warnings"]:
        pairs.append(("warning", w))
    rows = [[k, v] for k, v in pairs]
    _write_table(ws, 1, ["field", "value"], rows)
    for r, (k, _) in enumerate(pairs, start=2):
        if k in ("expenses total", "income total", "net", "excluded transfers (abs ₪)",
             "trip total", "vacation total", "average trip cost", "average vacation cost"):
            ws.cell(row=r, column=2).number_format = _MONEY_FMT
    _autofit(ws)


def _dim_sheet(wb, name: str, entries: list[dict], report: dict):
    from openpyxl.chart import PieChart, Reference
    from openpyxl.chart.label import DataLabelList

    ws = wb.create_sheet(name)
    metric = report["metric"]
    headers = [name.removeprefix("By "), "expenses ₪", "income ₪", "net ₪",
               "count", f"{metric} share"]
    if not entries:
        ws.cell(row=1, column=1, value=f"{name}: no rows in this scope.")
        return ws
    rows = [[e["label"], e["expenses"], e["income"], e["net"], e["count"], None]
            for e in entries]
    first, last = _write_table(ws, 1, headers, rows,
                               money_cols=(2, 3, 4), pct_cols=(6,))
    total_metric = sum(e[metric] for e in entries) or 1.0
    for i, e in enumerate(entries):
        ws.cell(row=first + i, column=6, value=e[metric] / total_metric)

    # ---- pie source table: top-N slices + אחר
    top = entries[: report["top_n"]]
    rest = entries[report["top_n"]: ]
    src_first = last + 3
    src_rows = [[e["label"], round(e[metric], 2)] for e in top]
    if rest:
        src_rows.append([f"אחר ({len(rest)} סעיפים)", round(sum(e[metric] for e in rest), 2)])
    hdr = src_first
    _money_ws_headers(ws, hdr, [f"chart: {metric} (top {report['top_n']})", metric + " ₪"])
    for i, rec in enumerate(src_rows):
        ws.cell(row=hdr + 1 + i, column=1, value=rec[0])
        c = ws.cell(row=hdr + 1 + i, column=2, value=rec[1])
        c.number_format = _MONEY_FMT
    src_last = hdr + len(src_rows)

    pie = PieChart()
    pie.title = f"{report['label']} - {metric} by {name.removeprefix('By ')}"
    data = Reference(ws, min_col=2, min_row=hdr, max_row=src_last)
    cats = Reference(ws, min_col=1, min_row=hdr + 1, max_row=src_last)
    pie.add_data(data, titles_from_data=True)
    pie.set_categories(cats)
    pie.dataLabels = DataLabelList()
    pie.dataLabels.showPercent = True
    pie.height, pie.width = 10, 16
    ws.add_chart(pie, f"H{hdr}")
    _autofit(ws)
    return ws


def _period_sheet(wb, report: dict):
    from openpyxl.chart import BarChart, Reference

    ws = wb.create_sheet("By period")
    metric_col = {"expenses": 2, "income": 3, "net": 4}[report["metric"]]
    headers = [report["split_by"], "expenses ₪", "income ₪", "net ₪", "count"]
    if not report["by_period"]:
        ws.cell(row=1, column=1, value="By period: no dated rows in this scope.")
        return ws
    rows = [[e["label"], e["expenses"], e["income"], e["net"], e["count"]]
            for e in report["by_period"]]
    first, last = _write_table(ws, 1, headers, rows, money_cols=(2, 3, 4))

    chart = BarChart()
    chart.type = "col"
    chart.style = 10
    chart.title = f"{report['label']} - {report['metric']} by {report['split_by']}"
    data = Reference(ws, min_col=metric_col, min_row=1, max_row=last)
    cats = Reference(ws, min_col=1, min_row=first, max_row=last)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    chart.y_axis.title = "ILS"
    chart.height, chart.width = 10, 18
    ws.add_chart(chart, "G3")
    _autofit(ws)
    return ws


def _monthly_comparison_sheet(wb, report: dict):
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.styles import Font

    matrix = report.get("monthly_matrix")
    if not matrix:
        return None
    ws = wb.create_sheet("Monthly comparison")
    groups = matrix["groups"]
    headers = ["month", *groups, "Total"]
    rows = [[entry["label"], *[entry["values"].get(group, 0.0) for group in groups], entry["total"]]
            for entry in matrix["rows"]]
    first, last = _write_table(ws, 1, headers, rows, money_cols=tuple(range(2, len(headers) + 1)))
    average_row, total_row = last - 1, last
    for col in range(1, len(headers) + 1):
        ws.cell(row=average_row, column=col).font = Font(bold=True)
        ws.cell(row=total_row, column=col).font = Font(bold=True)

    month_last = total_row - 2
    group_last_col = len(headers) - 1
    if groups:
        stacked = BarChart()
        stacked.type = "col"
        stacked.grouping = "stacked"
        stacked.overlap = 100
        stacked.style = 10
        stacked.title = f"{report['label']} - monthly expenses by group"
        stacked.y_axis.title = "ILS"
        stacked.add_data(Reference(ws, min_col=2, max_col=group_last_col, min_row=1, max_row=month_last),
                         titles_from_data=True)
        stacked.set_categories(Reference(ws, min_col=1, min_row=first, max_row=month_last))
        stacked.height, stacked.width = 12, 24
        ws.add_chart(stacked, f"A{last + 3}")

        totals = BarChart()
        totals.type = "bar"
        totals.style = 11
        totals.title = f"{report['label']} - total expenses by group"
        totals.x_axis.title = "ILS"
        totals.add_data(Reference(ws, min_col=2, max_col=group_last_col, min_row=total_row, max_row=total_row),
                        from_rows=True, titles_from_data=False)
        totals.set_categories(Reference(ws, min_col=2, max_col=group_last_col, min_row=1, max_row=1))
        totals.height, totals.width = 12, 18
        ws.add_chart(totals, f"Z{last + 3}")

    trend = LineChart()
    trend.style = 13
    trend.title = f"{report['label']} - total monthly expenses"
    trend.y_axis.title = "ILS"
    trend.add_data(Reference(ws, min_col=len(headers), min_row=1, max_row=month_last), titles_from_data=True)
    trend.set_categories(Reference(ws, min_col=1, min_row=first, max_row=month_last))
    trend.height, trend.width = 12, 20
    ws.add_chart(trend, f"A{last + 27}")
    _autofit(ws)
    return ws


def _trip_summary_sheet(wb, trip: dict):
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Font

    ws = wb.create_sheet("Trip summary")
    subcosts = trip["subcost_labels"]
    headers = ["topic", "type", "first date", "last date", "expense rows", "total cost ILS",
               *[f"{label} ILS" for label in subcosts]]
    rows = [[row["topic"], row["type"], row["first_date"], row["last_date"],
             row["expense_rows"], row["cost_ils"], *[row["subcosts"][label] for label in subcosts]]
            for row in trip["summary_rows"]]
    rows.extend((
        ["Average trips", "Trip", "", "", trip["trip_count"], trip["average_trip_cost"],
         *[trip["average_trip_subcosts"][label] for label in subcosts]],
        ["Average vacations", "Vacation", "", "", trip["vacation_count"], trip["average_vacation_cost"],
         *[trip["average_vacation_subcosts"][label] for label in subcosts]],
        ["Total", "", "", "", "", round(trip["trip_total"] + trip["vacation_total"], 2),
         *[trip["subcost_grand_totals"][label] for label in subcosts]],
    ))
    money_cols = tuple(range(6, 7 + len(subcosts)))
    first, last = _write_table(ws, 1, headers, rows, money_cols=money_cols)
    for row in range(last - 2, last + 1):
        for col in range(1, len(headers) + 1):
            ws.cell(row=row, column=col).font = Font(bold=True)
    data_last = last - 3
    chart = BarChart()
    chart.type = "bar"
    chart.style = 10
    chart.title = "Trip and vacation costs"
    chart.x_axis.title = "ILS"
    chart.add_data(Reference(ws, min_col=6, min_row=1, max_row=data_last), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=first, max_row=data_last))
    chart.height, chart.width = 12, 20
    ws.add_chart(chart, "H2")

    subcost_chart = BarChart()
    subcost_chart.type = "col"
    subcost_chart.grouping = "stacked"
    subcost_chart.overlap = 100
    subcost_chart.style = 10
    subcost_chart.title = "Sub-costs per trip (Flights / Lodging / Cash withdrawal / Other)"
    subcost_chart.y_axis.title = "ILS"
    subcost_chart.add_data(Reference(ws, min_col=7, max_col=len(headers), min_row=1, max_row=data_last),
                          titles_from_data=True)
    subcost_chart.set_categories(Reference(ws, min_col=1, min_row=first, max_row=data_last))
    subcost_chart.height, subcost_chart.width = 12, 25
    ws.add_chart(subcost_chart, "H27")
    _autofit(ws)
    return ws


def _trip_monthly_costs_sheet(wb, trip: dict):
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.styles import Font

    ws = wb.create_sheet("Trip monthly costs")
    names = trip["topics"]
    headers = ["month", *names, "Total"]
    rows = [[row["label"], *[row["values"].get(name, 0.0) for name in names], row["total"]]
            for row in trip["monthly_rows"]]
    first, last = _write_table(ws, 1, headers, rows, money_cols=tuple(range(2, len(headers) + 1)))
    for row in (last - 1, last):
        for col in range(1, len(headers) + 1):
            ws.cell(row=row, column=col).font = Font(bold=True)
    month_last = last - 2
    if names:
        stacked = BarChart()
        stacked.type = "col"
        stacked.grouping = "stacked"
        stacked.overlap = 100
        stacked.style = 10
        stacked.title = "Monthly trip and vacation costs"
        stacked.y_axis.title = "ILS"
        stacked.add_data(Reference(ws, min_col=2, max_col=len(headers) - 1, min_row=1, max_row=month_last),
                         titles_from_data=True)
        stacked.set_categories(Reference(ws, min_col=1, min_row=first, max_row=month_last))
        stacked.height, stacked.width = 12, 25
        ws.add_chart(stacked, f"A{last + 3}")
    trend = LineChart()
    trend.style = 13
    trend.title = "Total monthly trip and vacation costs"
    trend.y_axis.title = "ILS"
    trend.add_data(Reference(ws, min_col=len(headers), min_row=1, max_row=month_last), titles_from_data=True)
    trend.set_categories(Reference(ws, min_col=1, min_row=first, max_row=month_last))
    trend.height, trend.width = 12, 20
    ws.add_chart(trend, f"A{last + 27}")

    # second table: sub-cost breakdown aggregated across every selected trip
    sub_start = last + 55
    subcosts = trip["subcost_labels"]
    sub_headers = ["month", *subcosts, "Total"]
    sub_rows = [[row["label"], *[row["values"].get(label, 0.0) for label in subcosts], row["total"]]
                for row in trip["subcost_monthly_rows"]]
    sub_first, sub_last = _write_table(ws, sub_start, sub_headers, sub_rows,
                                       money_cols=tuple(range(2, len(sub_headers) + 1)))
    ws.cell(row=sub_start - 1, column=1, value="Monthly sub-cost breakdown (all trips combined)").font = \
        Font(bold=True, size=12)
    for row in (sub_last - 1, sub_last):
        for col in range(1, len(sub_headers) + 1):
            ws.cell(row=row, column=col).font = Font(bold=True)
    sub_month_last = sub_last - 2
    sub_stacked = BarChart()
    sub_stacked.type = "col"
    sub_stacked.grouping = "stacked"
    sub_stacked.overlap = 100
    sub_stacked.style = 11
    sub_stacked.title = "Monthly sub-costs (Flights / Lodging / Cash withdrawal / Other)"
    sub_stacked.y_axis.title = "ILS"
    sub_stacked.add_data(Reference(ws, min_col=2, max_col=len(sub_headers) - 1,
                                   min_row=sub_start, max_row=sub_month_last),
                         titles_from_data=True)
    sub_stacked.set_categories(Reference(ws, min_col=1, min_row=sub_first, max_row=sub_month_last))
    sub_stacked.height, sub_stacked.width = 12, 25
    ws.add_chart(sub_stacked, f"A{sub_last + 3}")

    sub_totals = BarChart()
    sub_totals.type = "bar"
    sub_totals.style = 12
    sub_totals.title = "Total cost by sub-cost"
    sub_totals.x_axis.title = "ILS"
    sub_totals.add_data(Reference(ws, min_col=2, max_col=len(sub_headers) - 1,
                                  min_row=sub_last, max_row=sub_last),
                        from_rows=True, titles_from_data=False)
    sub_totals.set_categories(Reference(ws, min_col=2, max_col=len(sub_headers) - 1,
                                        min_row=sub_start, max_row=sub_start))
    sub_totals.height, sub_totals.width = 12, 18
    ws.add_chart(sub_totals, f"Z{sub_last + 3}")
    _autofit(ws)
    return ws


def _details_sheet(wb, report: dict) -> None:
    ws = wb.create_sheet("Details")
    rows = [[rec.get(c, "") for c in DETAIL_COLUMNS] for rec in report["rows"]]
    money_idx = tuple(DETAIL_COLUMNS.index(c) + 1
                      for c in ("amount_ils", "amount_orig", "ils_rate"))
    first, last = _write_table(ws, 1, DETAIL_COLUMNS, rows, money_cols=money_idx)
    ws.auto_filter.ref = f"A1:{ws.cell(row=last, column=len(DETAIL_COLUMNS)).coordinate}"
    _autofit(ws)


def _reconciliation_sheet(wb, report: dict) -> None:
    ws = wb.create_sheet("Reconciliation")
    n = report["notes"]
    row = 1
    brief_cols = ["id", "date_iso", "description", "amount_ils", "flag", "explanation"]

    def flag_of(r):
        return ";".join(r.get("flags") or []) if isinstance(r.get("flags"), list) \
            else (r.get("flags") or "")

    def write_section(title, cols, recs, money_col=None, footer=None):
        nonlocal row
        if not recs and not footer:
            return
        row = _section_title(ws, row, title) + 1
        first, last = _write_table(ws, row, cols,
                                   [[r.get(c, "") for c in cols] for r in recs],
                                   money_cols=(money_col,) if money_col else ())
        if money_col:
            for rr in range(first, last + 1):
                ws.cell(row=rr, column=money_col).number_format = _MONEY_FMT
        if footer:
            row = last + 1
            ws.cell(row=row, column=1, value=footer)
        row = last + 3

    ann = n["annihilated"]
    st = n["annihilated_stats"] or {}
    write_section(
        f"Annihilated refund pairs ({st.get('pairs', 0)} pair(s), net effect ~0)",
        brief_cols, sorted(ann, key=lambda r: (r.get("seq") or 0, r.get("id"))),
        money_col=4,
        footer=(f"removed refunds {fmt_ils(st.get('refunds_ils'))}, removed expenses "
                f"{fmt_ils(st.get('expenses_ils'))}" if st else None))

    external_refunds = n.get("external_refund_partners") or []
    write_section(
        f"Refund-pair counterparts outside the selected range ({len(external_refunds)} row(s))",
        brief_cols, sorted(external_refunds, key=lambda r: (r.get("seq") or 0, r.get("id"))),
        money_col=4,
        footer=("The selected leg was removed from analysis via --refund-scope ledger; "
                "these counterpart rows are shown for audit only." if external_refunds else None))

    write_section(
        f"Internal transfers excluded from sums (total {fmt_ils(report['totals']['transfers_ils'])})",
        ["id", "date_iso", "description", "category", "amount_ils", "explanation"],
        sorted(n["transfers"], key=lambda r: (r.get("seq") or 0, r.get("id"))),
        money_col=5)

    if n.get("forced_include"):
        write_section(
            f"Forced into expense/income by --force-include ({len(n['forced_include'])} row(s))",
            ["id", "date_iso", "description", "category", "amount_ils", "explanation"],
            sorted(n["forced_include"], key=lambda r: (r.get("seq") or 0, r.get("id"))),
            money_col=5)

    fx_rows = [{
        "id": r.get("id"), "date_iso": r.get("date_iso"),
        "description": r.get("description"), "currency_orig": r.get("currency_orig"),
        "amount_orig": r.get("amount_orig"), "flags": flag_of(r),
        "explanation": r.get("explanation"),
    } for r in sorted(n["fx_gap"], key=lambda r: (r.get("seq") or 0, r.get("id")))]
    write_section(
        f"Rows without an ILS amount - no_fx_rate: excluded from every sum "
        f"({len(fx_rows)} row(s))",
        ["id", "date_iso", "description", "currency_orig", "amount_orig",
         "flags", "explanation"],
        fx_rows, money_col=5,
        footer=("These rows carry only their original-currency amount; fix them in "
                "finance-unify (e.g. after adding a matching FX observation) and re-run."
                if fx_rows else None))

    if n["stale_ids"]:
        row = _section_title(ws, row, "Stale topic ids (not present in the current ledger)") + 1
        for cid in n["stale_ids"]:
            ws.cell(row=row, column=1, value=cid)
            row += 1
        row += 2

    for w in n["warnings"]:
        ws.cell(row=row, column=1, value="[warn] " + w)
        row += 1

    if row == 1:  # nothing to reconcile at all
        ws.cell(row=1, column=1, value="Nothing to reconcile - no gaps detected.")
    _autofit(ws)


def _compare_sheets(wb, comparison: dict, label: str) -> None:
    from openpyxl.chart import BarChart3D, PieChart3D, Reference
    from openpyxl.styles import Font
    names = comparison["topics"]
    for dim, comp in comparison.items():
        if dim == "topics":
            continue
        ws = wb.create_sheet(f"Compare {dim}")
        headers = [dim, *names, "total"]
        rows = []
        for slot in comp["matrix"]:
            rows.append([slot["label"], *[slot["values"].get(nm, None) for nm in names],
                         slot["total"]])
        grand = comp["grand_totals"]
        rows.append(["TOTAL", *[grand.get(nm, None) for nm in names],
                     round(sum(grand.values()), 2)])
        ncols = len(headers)
        first, last = _write_table(ws, 1, headers, rows,
                                   money_cols=tuple(range(2, ncols + 1)))
        for c in range(1, ncols + 1):
            ws.cell(row=last, column=c).font = Font(bold=True)

        bar = BarChart3D()
        bar.type = "col"
        bar.grouping = "clustered"
        bar.title = f"{label} - {dim} by topic (3D)"
        data = Reference(ws, min_col=2, max_col=ncols - 1, min_row=1, max_row=last - 1)
        cats = Reference(ws, min_col=1, min_row=first, max_row=last - 1)
        bar.add_data(data, titles_from_data=True)
        bar.set_categories(cats)
        bar.height, bar.width = 11, 19
        ws.add_chart(bar, f"A{last + 3}")

        pie = PieChart3D()
        pie.title = f"{label} - grand totals per topic (3D)"
        pdata = Reference(ws, min_col=2, max_col=ncols - 1, min_row=last, max_row=last)
        pcats = Reference(ws, min_col=2, max_col=ncols - 1, min_row=1, max_row=1)
        pie.add_data(pdata, titles_from_data=False, from_rows=True)
        pie.set_categories(pcats)
        pie.height, pie.width = 10, 13
        ws.add_chart(pie, f"L{last + 3}")
        _autofit(ws)


# --------------------------------------------------------------------- main

def write_report(path: str, report: dict, comparison: dict | None = None,
                 context: dict | None = None) -> list[str]:
    """Build the workbook; returns the list of sheet names written."""
    from openpyxl import Workbook

    context = context or {}
    wb = Workbook()
    _overview_sheet(wb, report, context)
    if report["bucket_differs"]:
        _dim_sheet(wb, "By bucket", report["by_bucket"], report)
        _dim_sheet(wb, "By group", report["by_group"], report)
    else:
        _dim_sheet(wb, "By group", report["by_group"], report)
    _period_sheet(wb, report)
    _monthly_comparison_sheet(wb, report)
    if report.get("trip_comparison"):
        _trip_summary_sheet(wb, report["trip_comparison"])
        _trip_monthly_costs_sheet(wb, report["trip_comparison"])
    _details_sheet(wb, report)
    _reconciliation_sheet(wb, report)
    if comparison:
        _compare_sheets(wb, comparison, report["label"])

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    wb.save(path)
    return wb.sheetnames

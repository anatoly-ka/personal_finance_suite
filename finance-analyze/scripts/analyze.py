#!/usr/bin/env python3
"""finance-analyze: expense/income analysis of a finance-topic selection.

Always runs in two steps (the SKILL.md workflow enforces this):

  1. --plan   prints the full analysis plan - inputs, resolution counts,
              refund/transfers/no-fx handling, detected comparison, the exact
              sheet list and output path - and generates NOTHING.
  2. re-run without --plan (after the user approves the plan) to build the
     .xlsx workbook with native charts.

Input is one topic (--topic) or a suite of topics (--topics A,B,C: the union
of their approved ids). Plain date-range slices belong in finance-topic
(a `custom` topic) - create one there first, then analyze it here.
"""

from __future__ import annotations

import argparse
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # finance-analyze/
_SKILLS_ROOT = os.path.dirname(_SKILL_DIR)
for _p in (_SKILLS_ROOT,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from finance_core import analyze, refunds, taxonomy, topics  # noqa: E402


# ------------------------------------------------------------------ helpers

def _split_multi(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        out.extend(p.strip() for p in v.split(",") if p.strip())
    return out


def require_ledger(path: str) -> None:
    if not os.path.exists(path):
        print(f"[error] ledger not found: {path}")
        print("        Run finance-unify first to build it, e.g.:")
        print(f'        python "<skills>/finance-unify/scripts/unify.py" '
              f'--project "{os.path.dirname(os.path.dirname(path))}"')
        raise SystemExit(2)


def _default_out(project: str, label: str) -> str:
    return os.path.join(project, "data", "analysis", f"{label}.xlsx")


def _valid_month(value: str) -> bool:
    if len(value) != 7 or value[4] != "-":
        return False
    try:
        year, month = value.split("-")
        return len(year) == 4 and 1 <= int(month) <= 12
    except ValueError:
        return False


def _parse_subcost_splits(raw: str | None) -> tuple[dict[str, dict[str, float]], list[str]]:
    """Parse --subcost-split 'ID1|ID2:Label=pct,Label=pct;ID3:Label=pct' into
    {row id: {label: fraction}}; any fraction left unassigned per row falls to
    Other. Returns (splits, error messages)."""
    splits: dict[str, dict[str, float]] = {}
    errors: list[str] = []
    if not raw:
        return splits, errors
    for group in raw.split(";"):
        group = group.strip()
        if not group:
            continue
        ids_part, sep, shares_part = group.partition(":")
        if not sep:
            errors.append(f"{group!r} is missing ':label=fraction,...'")
            continue
        ids = [i.strip() for i in ids_part.split("|") if i.strip()]
        if not ids:
            errors.append(f"{group!r} has no row id(s) before ':'")
            continue
        shares: dict[str, float] = {}
        for pair in shares_part.split(","):
            pair = pair.strip()
            if not pair:
                continue
            label, eq, pct = pair.partition("=")
            label = label.strip()
            if not eq or label not in analyze.TRIP_SUBCOST_LABELS:
                errors.append(f"{pair!r} must be one of {', '.join(analyze.TRIP_SUBCOST_LABELS)}=fraction")
                continue
            try:
                shares[label] = float(pct)
            except ValueError:
                errors.append(f"{pair!r}: {pct!r} is not a number")
        if sum(shares.values()) > 1.0 + 1e-6:
            errors.append(f"{group!r}: fractions sum to more than 1.0")
        for cid in ids:
            splits[cid] = shares
    return splits, errors


def _topic_span(rows: list[dict]) -> str:
    dates = sorted(r.get("date_iso") or "" for r in rows if r.get("date_iso"))
    return f"{dates[0]} .. {dates[-1]}" if dates else "-"


def _detail_lines(recs: list[dict], cap: int = 40) -> list[str]:
    """One line per excluded/overridden row - id, date, amount, category,
    description - so a curated topic's exclusions can be eyeballed before
    generating the workbook."""
    lines = []
    for rec in recs[:cap]:
        date = rec.get("billing_date") or rec.get("date_iso") or "-"
        amt = analyze.fmt_ils(rec.get("amount_ils"))
        cat = (rec.get("category") or "-").strip()
        desc = (rec.get("description") or "").strip()
        lines.append(f"[plan]     {rec.get('id', '?'):<16} {date}  {amt:>14}  {cat} | {desc}")
    if len(recs) > cap:
        lines.append(f"[plan]     ... and {len(recs) - cap} more")
    return lines


def _subcost_split_lines(splits: dict[str, dict[str, float]], by_id: dict[str, dict]) -> list[str]:
    """One line per --subcost-split row - id, date, amount, and the applied
    Flights/Lodging/Cash withdrawal/Other percentages."""
    lines = []
    for cid, shares in sorted(splits.items()):
        rec = by_id.get(cid)
        if not rec:
            continue
        date = rec.get("billing_date") or rec.get("date_iso") or "-"
        amt = analyze.fmt_ils(rec.get("amount_ils"))
        remainder = round(1.0 - sum(shares.values()), 6)
        parts = [f"{label} {pct * 100:.0f}%" for label, pct in shares.items()]
        if remainder > 1e-9:
            parts.append(f"Other {remainder * 100:.0f}%")
        lines.append(f"[plan]     {cid:<16} {date}  {amt:>14}  " + " / ".join(parts))
    return lines


def _plan_lines(report: dict, comparison: dict | None, context: dict,
                out_path: str) -> list[str]:
    t = report["totals"]
    n = report["notes"]
    lines = [
        "[plan] " + "-" * 66,
        f"[plan] input      : {context['input_desc']}",
        f"[plan] ledger     : {context['ledger_path']}",
        f"[plan] resolution : {context.get('resolved_count', '?')} row(s) resolved"
        + (f", {len(n['stale_ids'])} STALE id(s) ignored" if n["stale_ids"] else ""),
    ]
    st = n.get("annihilated_stats") or {}
    refunds_mode = context.get("refunds_mode", "annihilate")
    if st.get("pairs"):
        verb = ("will annihilate" if refunds_mode == "annihilate"
                else "found but KEPT (--refunds keep)")
        lines.append(
            f"[plan] refunds    : {st['pairs']} bound pair(s) {verb} "
            f"({analyze.fmt_ils(st.get('refunds_ils'))} of refunds)")
        if st.get("cross_scope_pairs"):
            lines.append(
                f"[plan]              {st['cross_scope_pairs']} pair(s) cross the selected "
                "range; selected legs will be removed using full-ledger links")
    else:
        lines.append(f"[plan] refunds    : no bound pair(s) ({refunds_mode})")
    unmatched_income = t["income_rows"]
    lines.append(
        f"[plan] transfers  : {len(n['transfers'])} leftover internal-transfer "
        f"leg(s) excluded from sums ({analyze.fmt_ils(t['transfers_ils'])})")
    lines.extend(_detail_lines(n["transfers"]))
    if n.get("forced_include"):
        reason = "approved trip-topic row(s) included" if context.get("trip_comparison") else \
            "id(s) forced OUT of transfers into expense/income by --force-include"
        lines.append(f"[plan] overrides  : {len(n['forced_include'])} {reason}")
        lines.extend(_detail_lines(n["forced_include"]))
    lines.append(
        f"[plan] fx gaps    : {len(n['fx_gap'])} no_fx_rate row(s) cannot be "
        f"summed -> Reconciliation sheet")
    lines.extend(_detail_lines(n["fx_gap"]))
    lines.append(
        f"[plan] scope      : {report['scope']} "
        f"(expenses {analyze.fmt_ils(t['expenses'])} / income "
        f"{analyze.fmt_ils(t['income'])} / net {analyze.fmt_ils(t['net'], signed=True)})")
    dim_txt = report["chart_dim"] + (
        " (identity buckets)" if not report["bucket_differs"] else "")
    lines.append(
        f"[plan] breakdown  : by {dim_txt} (pie, top {report['top_n']} + אחר), "
        f"by {report['split_by']} on {report['date_field']} (column chart)")
    if report["split_by"] == "phase" and context.get("phase_ranges"):
        lines.append(
            "[plan] trip window: " + "; ".join(
                f"{name} {d1}..{d2}" for name, (d1, d2) in context["phase_ranges"].items()))
    if report["split_by"] == "custom" and context.get("split_dates"):
        lines.append(f"[plan] split dates: {', '.join(context['split_dates'])}")
    if comparison:
        lines.append(
            f"[plan] comparison : DETECTED - {context.get('compare_reason', '')}"
            f" -> Compare sheets + clustered BarChart3D / PieChart3D")
    elif len(context.get("topics_meta") or []) > 1 and not context.get("trip_comparison"):
        lines.append(
            f"[plan] comparison : skipped - {context.get('compare_reason', 'not similar')}")
    trip = report.get("trip_comparison")
    if trip:
        span_desc = (f"{trip['month_count']} fixed calendar month(s) ({context['month_range'][0]}"
                     f" .. {context['month_range'][1]})") if trip.get("fixed_range") \
            else f"{trip['month_count']} continuous transaction month(s)"
        lines.append(
            f"[plan] trips      : {trip['trip_count']} trip(s), {trip['vacation_count']} vacation(s); "
            f"{span_desc}")
        if trip.get("subcost_splits"):
            lines.append(f"[plan] subcost split: {len(trip['subcost_splits'])} row(s) overridden")
            lines.extend(context.get("subcost_split_lines") or [])
    lines.append(f"[plan] output     : {out_path}")
    for w in n["warnings"]:
        lines.append(f"[plan] WARNING    : {w}")
    sheets = analyze.planned_sheet_names(report, comparison)
    lines.append(f"[plan] sheets     : {' | '.join(sheets)}")
    lines.append("[plan] " + "-" * 66)
    return lines


# --------------------------------------------------------------------- main

def run(args) -> int:
    project = os.path.abspath(args.project)
    ledger_path = args.ledger or os.path.join(project, "data", "ledger.csv")
    require_ledger(ledger_path)
    tdir = os.path.join(project, "data", "topics")

    # ---- topic selection -------------------------------------------------
    if bool(args.topic) == bool(args.topics):
        print("[error] pass exactly one of --topic NAME or --topics A,B,C.")
        return 2
    names = [args.topic] if args.topic else _split_multi([args.topics])
    seen: set[str] = set()
    ordered_names = [n for n in names if not (n in seen or seen.add(n))]

    available = topics.discover_topic_names(tdir)
    specs, missing = analyze.load_specs(tdir, ordered_names)
    if missing:
        print(f"[error] topic(s) not found under {tdir}: {', '.join(missing)}")
        if available:
            print(f"        existing topics: {', '.join(available)}")
        else:
            print("        (no topics yet - create some with finance-topic first)")
        return 2

    blocked = [(s["topic"], len(s.get("pending_review") or []))
               for s in specs if s.get("pending_review")]
    if blocked and not args.force:
        for name, cnt in blocked:
            print(f"[error] topic {name!r} still has {cnt} pending_review row(s).")
        print("        Resolve them via finance-topic `review`, or re-run with --force.")
        return 1

    # ---- resolve against the ledger --------------------------------------
    rows_all = topics.load_ledger(ledger_path)
    by_id = {r["id"]: r for r in rows_all}
    union_ids: list[str] = []
    seen_ids: set[str] = set()
    for s in specs:
        for cid in s.get("ids") or []:
            if cid not in seen_ids:  # staleness decided in resolve_rows
                seen_ids.add(cid)
                union_ids.append(cid)

    resolved, stale = analyze.resolve_rows(rows_all, union_ids)
    if not resolved:
        print("[warn] no included rows resolved against the ledger - nothing to analyze.")
        return 1
    row_topics: dict[str, list[str]] = {}
    for s in specs:
        for cid in s.get("ids") or []:
            row_topics.setdefault(cid, []).append(s["topic"])

    # ---- split-by validation (phase needs trip windows, custom needs dates)
    phase_ranges: dict[str, tuple[str, str]] = {}
    phase_topic_ranges: dict[str, tuple[str, str]] = {}
    if args.split_by == "phase":
        missing = []
        for s in specs:
            p = s.get("params") or {}
            d1, d2 = p.get("date1"), p.get("date2")
            if not (d1 and d2):
                missing.append(s["topic"])
                continue
            phase_topic_ranges[s["topic"]] = (d1, d2)
            for cid in s.get("ids") or []:
                phase_ranges.setdefault(cid, (d1, d2))
        if missing:
            print(f"[error] --split-by phase needs date1/date2 on every selected "
                  f"topic; missing on: {', '.join(missing)}.")
            print("        Use a trip topic (trip_abroad/trip_local) or a custom "
                  "topic created with --date1/--date2, or pick another --split-by.")
            return 2

    split_dates: list[str] = []
    if args.split_by == "custom":
        split_dates = _split_multi([args.split_dates] if args.split_dates else [])
        if not split_dates:
            print("[error] --split-by custom needs --split-dates D1[,D2,...] "
                  "(ISO yyyy-mm-dd).")
            return 2
        bad = [d for d in split_dates if topics.parse_date(d) is None]
        if bad:
            print(f"[error] invalid date(s) in --split-dates: {', '.join(bad)} "
                  f"(expected yyyy-mm-dd).")
            return 2
        split_dates.sort(key=topics.parse_date)
    elif args.split_dates:
        print("[warn] --split-dates is ignored unless --split-by custom.")

    if args.monthly_matrix:
        if args.scope != "expenses" or args.split_by != "month":
            print("[error] --monthly-matrix requires --scope expenses and --split-by month.")
            return 2
        if not args.month_range:
            print("[error] --monthly-matrix requires --month-range START,END (YYYY-MM,YYYY-MM).")
            return 2
    if args.month_range and (args.monthly_matrix or args.trip_comparison):
        month_parts = _split_multi([args.month_range])
        if len(month_parts) != 2 or any(not _valid_month(value) for value in month_parts):
            print("[error] --month-range must contain exactly two YYYY-MM values.")
            return 2
        if month_parts[0] > month_parts[1]:
            print("[error] --month-range start must not be after its end.")
            return 2
        args.month_range = tuple(month_parts)
    elif args.month_range:
        print("[warn] --month-range is ignored unless --monthly-matrix or --trip-comparison is set.")
        args.month_range = None

    if args.trip_comparison:
        if not args.topics or args.scope != "expenses" or args.split_by != "month" \
                or args.split_date_field != "date_iso":
            print("[error] --trip-comparison requires --topics, --scope expenses, "
                  "--split-by month, and --split-date-field date_iso.")
            return 2
        non_trip = [spec["topic"] for spec in specs if spec.get("kind") not in analyze.TRIP_KINDS]
        if non_trip:
            print("[error] --trip-comparison only accepts trip_abroad/trip_local topics; "
                  f"not trips: {', '.join(non_trip)}")
            return 2
        if args.monthly_matrix:
            print("[error] --trip-comparison cannot be combined with --monthly-matrix.")
            return 2

    subcost_splits, subcost_errors = _parse_subcost_splits(args.subcost_split)
    if subcost_errors:
        for e in subcost_errors:
            print(f"[error] --subcost-split: {e}")
        return 2
    if subcost_splits and not args.trip_comparison:
        print("[warn] --subcost-split is ignored unless --trip-comparison is set.")
        subcost_splits = {}
    unknown_split_ids = [cid for cid in subcost_splits if cid not in seen_ids]
    if unknown_split_ids:
        print(f"[error] --subcost-split row id(s) not in any selected topic: "
              f"{', '.join(unknown_split_ids)}")
        return 2

    # ---- refund annihilation (suite-wide, before any partitioning) -------
    extra_notes: dict = {"stale_ids": stale}
    refunds_mode = args.refunds
    refund_ledger = rows_all if args.refund_scope == "ledger" else None
    _, would_drop, stats = refunds.annihilate_bound_pairs(resolved, refund_ledger)
    if refunds_mode == "annihilate":
        dropped_ids = {id(r) for r in would_drop}
        kept = [r for r in resolved if id(r) not in dropped_ids]
        extra_notes["annihilated"] = would_drop
        extra_notes["annihilated_stats"] = stats
        extra_notes["external_refund_partners"] = stats["external_partners"]
    else:
        kept, dropped = resolved, []

    mapping, map_path = analyze.load_category_map(project, args.category_map)

    force_include = set(_split_multi([args.force_include] if args.force_include else []))
    if args.trip_comparison:
        force_include.update(union_ids)

    label = "+".join(ordered_names)
    common = dict(scope=args.scope, split_by=args.split_by,
                  date_field=args.split_date_field, split_dates=split_dates,
                  phase_ranges=phase_ranges, force_include=force_include,
                  chart_dim=args.chart_dim, top_n=args.top_n, mapping=mapping,
                  monthly_matrix_range=args.month_range if args.monthly_matrix else None)
    report = analyze.build_report(
        kept, label=label, row_topics=row_topics, extra_notes=extra_notes, **common)

    # ---- per-topic sub-reports power the comparison matrices -------------
    sub_reports = []
    for s in specs:
        t_rows = [r for r in kept if r["id"] in set(s.get("ids") or [])]
        if t_rows:
            sub_reports.append(analyze.build_report(
                t_rows, label=s["topic"], **common))

    if args.trip_comparison:
        report["trip_comparison"] = analyze.build_trip_comparison(
            sub_reports, specs, month_range=args.month_range, subcost_splits=subcost_splits)

    # ---- comparison decision ---------------------------------------------
    comparison = None
    compare_reason = ""
    if len(specs) > 1 and not args.trip_comparison:
        similar, compare_reason = analyze.detect_similarity(specs)
        if args.compare != "never" and (args.compare == "always" or similar):
            dims = []
            for d in (report["chart_dim"], "period"):
                if d not in dims:
                    dims.append(d)
            comparison = analyze.build_comparisons(sub_reports, dims=dims)
            compare_reason += "; comparing on " + ", ".join(dims)
        elif args.compare == "never":
            compare_reason = "disabled (--compare never)"

    topics_meta = [{
        "name": s["topic"],
        "kind": s.get("kind", ""),
        "ids": len(s.get("ids") or []),
        "span": _topic_span([by_id[i] for i in (s.get("ids") or []) if i in by_id]),
    } for s in specs]
    context = {
        "project": project,
        "ledger_path": ledger_path,
        "topics_meta": topics_meta,
        "refunds_mode": refunds_mode,
        "refund_scope": args.refund_scope,
        "compare_note": (("on - " + compare_reason) if comparison else compare_reason),
        "compare_reason": compare_reason,
        "category_map_path": map_path,
        "input_desc": "; ".join(f"{m['name']} ({m['kind']}, {m['ids']} ids)"
                                for m in topics_meta),
        "resolved_count": len(resolved),
        "phase_ranges": phase_topic_ranges,
        "split_dates": split_dates,
        "trip_comparison": args.trip_comparison,
        "month_range": args.month_range,
        "subcost_split_lines": _subcost_split_lines(subcost_splits, by_id),
    }

    out_path = args.out or _default_out(project, label)
    plan = _plan_lines(report, comparison, context, out_path)
    for line in plan:
        print(line)

    if args.plan:
        print("\n[next] review the plan above; re-run WITHOUT --plan to generate the workbook.")
        return 0

    if os.path.exists(out_path) and not (args.update or args.force):
        print(f"\n[error] {out_path} already exists; pass --update to overwrite.")
        return 2

    from finance_core import report_xlsx
    report_xlsx.ensure_openpyxl()
    sheets = report_xlsx.write_report(out_path, report, comparison, context)

    print(f"\n=== analysis written: {out_path} ===")
    print(f"sheets: {', '.join(sheets)}")
    print(f"expenses {analyze.fmt_ils(report['totals']['expenses'])} | "
          f"income {analyze.fmt_ils(report['totals']['income'])} | "
          f"net {analyze.fmt_ils(report['totals']['net'], signed=True)} "
          f"over {report['totals']['count']} row(s)")
    if stats.get("pairs"):
        print(f"refund pairs annihilated: {stats['pairs']} "
              f"({analyze.fmt_ils(stats['refunds_ils'])} refunded)")
    if report["notes"]["fx_gap"]:
        print(f"[note] {len(report['notes']['fx_gap'])} no_fx_rate row(s) excluded "
              f"from sums - see the Reconciliation sheet.")
    if stale:
        print(f"[note] {len(stale)} stale topic id(s) ignored (re-run finance-unify "
              f"or refresh the topic).")
    print("[next] open the .xlsx directly, or import it into Google Sheets "
          "(File > Import) - tables and native charts come along.")
    return 0


# ---------------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Analyze a finance-topic selection into an .xlsx report "
                    "(always run --plan first).")
    ap.add_argument("--project", default=os.getcwd(), help="Project root (default: cwd).")
    ap.add_argument("--ledger", help="Ledger CSV path (default: <project>/data/ledger.csv).")

    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--topic", help="A single finance-topic name.")
    sel.add_argument("--topics", dest="topics",
                     help="Comma-separated topic names; analyzed as their union.")

    ap.add_argument("--scope", choices=list(analyze.SCOPES), default="expenses",
                    help="expenses | income | all (default: expenses). Positive rows "
                         "are always income; bound refund pairs are annihilated unless "
                         "--refunds keep.")
    ap.add_argument("--split-by", dest="split_by",
                    choices=["month", "quarter", "year", "week", "phase", "custom"],
                    default="month",
                    help="Period granularity for By-period breakdown (default: month). "
                         "'phase' splits before/during/after each row's own trip "
                         "(needs date1/date2 on every selected topic); 'custom' splits "
                         "on arbitrary --split-dates boundaries.")
    ap.add_argument("--split-date-field", dest="split_date_field",
                    choices=["date_iso", "billing_date"], default="billing_date",
                    help="Ledger date column driving every period split above "
                         "(default: billing_date; date_iso is the operation date).")
    ap.add_argument("--split-dates", dest="split_dates",
                    help="Comma-separated ISO boundary dates (yyyy-mm-dd) for "
                         "--split-by custom; N dates make N+1 buckets.")
    ap.add_argument("--chart-dim", dest="chart_dim", choices=["bucket", "group"],
                    default="bucket", help="Dimension driving the pie chart (default: bucket; "
                                           "falls back to group while buckets stay at identity).")
    ap.add_argument("--top-n", dest="top_n", type=int, default=12,
                    help="Pie slices before collapsing the rest into אחר (default: 12).")
    ap.add_argument("--refunds", choices=["annihilate", "keep"], default="annihilate",
                    help="Annihilate bound refund<->expense pairs inside the analyzed set "
                         "(default) or keep both legs visible.")
    ap.add_argument("--refund-scope", choices=["selected", "ledger"], default="selected",
                    help="selected: only annihilate pairs whose both legs are selected (default); "
                        "ledger: also remove a selected leg whose bound counterpart is outside the selection.")
    ap.add_argument("--monthly-matrix", action="store_true",
                    help="Add a monthly expense-group comparison table and charts; requires expenses/month.")
    ap.add_argument("--month-range",
                    help="Inclusive YYYY-MM,YYYY-MM range for --monthly-matrix (required) or "
                         "--trip-comparison (optional, default: continuous transaction months); "
                         "includes zero-spend months.")
    ap.add_argument("--trip-comparison", action="store_true",
                    help="Add trip/vacation cost and transaction-month comparison sheets for approved trip topics.")
    ap.add_argument("--subcost-split", dest="subcost_split",
                    help="With --trip-comparison, override the Flights/Lodging/Cash withdrawal/"
                         "Other sub-cost split for specific row(s) that cover more than one "
                         "(e.g. a combined flight+lodging payment): "
                         "'ID1|ID2:Label=fraction,Label=fraction;ID3:Label=fraction,...'. "
                         "Any unassigned fraction per row falls to Other.")
    ap.add_argument("--force-include", dest="force_include",
                    help="Comma-separated row id(s) to count as expense/income even though "
                         "their category/group would normally exclude them as an internal "
                         "transfer (e.g. an FX cash withdrawal that was actually trip spending).")
    ap.add_argument("--compare", choices=["auto", "always", "never"], default="auto",
                    help="Multi-topic comparison sheets/charts (default: auto-detect "
                         "similar suites such as trips or date-range slices).")
    ap.add_argument("--category-map", dest="category_map",
                    help="Override category_map.yaml path (default: <project>/category_map.yaml,"
                         " else the shared master).")
    ap.add_argument("--out", help=f"Output .xlsx (default: {_default_out('<project>', '<topics>')})).")
    ap.add_argument("--update", action="store_true", help="Overwrite an existing output.")
    ap.add_argument("--force", action="store_true",
                    help="Ignore pending_review blocks and existing-output guard.")
    ap.add_argument("--plan", action="store_true",
                    help="Print the analysis plan only - generate nothing (step 1 of 2).")
    return ap


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

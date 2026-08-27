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


def _topic_span(rows: list[dict]) -> str:
    dates = sorted(r.get("date_iso") or "" for r in rows if r.get("date_iso"))
    return f"{dates[0]} .. {dates[-1]}" if dates else "-"


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
    else:
        lines.append(f"[plan] refunds    : no bound pair(s) ({refunds_mode})")
    unmatched_income = t["income_rows"]
    lines.append(
        f"[plan] transfers  : {len(n['transfers'])} leftover internal-transfer "
        f"leg(s) excluded from sums ({analyze.fmt_ils(t['transfers_ils'])})")
    lines.append(
        f"[plan] fx gaps    : {len(n['fx_gap'])} no_fx_rate row(s) cannot be "
        f"summed -> Reconciliation sheet")
    lines.append(
        f"[plan] scope      : {report['scope']} "
        f"(expenses {analyze.fmt_ils(t['expenses'])} / income "
        f"{analyze.fmt_ils(t['income'])} / net {analyze.fmt_ils(t['net'], signed=True)})")
    dim_txt = report["chart_dim"] + (
        " (identity buckets)" if not report["bucket_differs"] else "")
    lines.append(
        f"[plan] breakdown  : by {dim_txt} (pie, top {report['top_n']} + אחר), "
        f"by {report['split_by']} (column chart)")
    if comparison:
        lines.append(
            f"[plan] comparison : DETECTED - {context.get('compare_reason', '')}"
            f" -> Compare sheets + clustered BarChart3D / PieChart3D")
    elif len(context.get("topics_meta") or []) > 1:
        lines.append(
            f"[plan] comparison : skipped - {context.get('compare_reason', 'not similar')}")
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

    # ---- refund annihilation (suite-wide, before any partitioning) -------
    extra_notes: dict = {"stale_ids": stale}
    refunds_mode = args.refunds
    _, would_drop, stats = refunds.annihilate_bound_pairs(resolved)
    if refunds_mode == "annihilate":
        dropped_ids = {id(r) for r in would_drop}
        kept = [r for r in resolved if id(r) not in dropped_ids]
        extra_notes["annihilated"] = would_drop
        extra_notes["annihilated_stats"] = stats
    else:
        kept, dropped = resolved, []

    mapping, map_path = analyze.load_category_map(project, args.category_map)

    label = "+".join(ordered_names)
    common = dict(scope=args.scope, split_by=args.split_by,
                  chart_dim=args.chart_dim, top_n=args.top_n, mapping=mapping)
    report = analyze.build_report(
        kept, label=label, row_topics=row_topics, extra_notes=extra_notes, **common)

    # ---- per-topic sub-reports power the comparison matrices -------------
    sub_reports = []
    for s in specs:
        t_rows = [r for r in kept if r["id"] in set(s.get("ids") or [])]
        if t_rows:
            sub_reports.append(analyze.build_report(
                t_rows, label=s["topic"], **common))

    # ---- comparison decision ---------------------------------------------
    comparison = None
    compare_reason = ""
    if len(specs) > 1:
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
        "compare_note": (("on - " + compare_reason) if comparison else compare_reason),
        "compare_reason": compare_reason,
        "category_map_path": map_path,
        "input_desc": "; ".join(f"{m['name']} ({m['kind']}, {m['ids']} ids)"
                                for m in topics_meta),
        "resolved_count": len(resolved),
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
                    choices=["month", "quarter", "year"], default="month",
                    help="Period granularity for By-period breakdown (default: month).")
    ap.add_argument("--chart-dim", dest="chart_dim", choices=["bucket", "group"],
                    default="bucket", help="Dimension driving the pie chart (default: bucket; "
                                           "falls back to group while buckets stay at identity).")
    ap.add_argument("--top-n", dest="top_n", type=int, default=12,
                    help="Pie slices before collapsing the rest into אחר (default: 12).")
    ap.add_argument("--refunds", choices=["annihilate", "keep"], default="annihilate",
                    help="Annihilate bound refund<->expense pairs inside the analyzed set "
                         "(default) or keep both legs visible.")
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

#!/usr/bin/env python3
"""finance-topic: extract a topic-related subset of the finance-unify ledger.

Three subcommands:

  propose  - scan the ledger, write/update data/topics/<name>.json (the spec)
             and data/topics/<name>.review.csv (human-readable).
  review   - move ids between included / pending_review / excluded without
             re-scanning the ledger.
  extract  - write the final topic CSV(s) from the spec's included ids.
  list     - show existing topics in this project (name, kind, counts).

See README.md for the full guide (including a non-technical, step-by-step
walkthrough). Depends only on the Python standard library; PyYAML is used
only if you choose to customize topic_signals.yaml (optional).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # finance-topic/
_SKILLS_ROOT = os.path.dirname(_SKILL_DIR)
if _SKILLS_ROOT not in sys.path:
    sys.path.insert(0, _SKILLS_ROOT)

from finance_core import topics  # noqa: E402


# ------------------------------------------------------------------ helpers

def _split_multi(values: list[str] | None) -> list[str]:
    """argparse append-collected values, each possibly comma-separated."""
    out: list[str] = []
    for v in values or []:
        out.extend(p.strip() for p in v.split(",") if p.strip())
    return out


def ensure_project_signals(project: str) -> str:
    """Create an editable per-project copy of topic_signals.yaml on first use.
    Purely optional to edit; the built-in defaults work with no editing."""
    dest = os.path.join(project, "topic_signals.yaml")
    if not os.path.exists(dest):
        master = os.path.join(_SKILLS_ROOT, "finance_core", "data", "topic_signals.yaml")
        if os.path.exists(master):
            os.makedirs(project, exist_ok=True)
            shutil.copyfile(master, dest)
            print(f"[info] created per-project heuristics file: {dest} (optional to edit)")
    return dest


def require_ledger(path: str) -> None:
    if not os.path.exists(path):
        print(f"[error] ledger not found: {path}")
        print("        Run finance-unify first to build it, e.g.:")
        print(f'        python "<skills>/finance-unify/scripts/unify.py" --project "{os.path.dirname(os.path.dirname(path))}"')
        raise SystemExit(2)


def topics_dir_for(project: str) -> str:
    return os.path.join(project, "data", "topics")


def ledger_path_for(project: str, explicit: str | None) -> str:
    return explicit or os.path.join(project, "data", "ledger.csv")


# ------------------------------------------------------------------ propose

def cmd_propose(args) -> int:
    project = os.path.abspath(args.project)
    ledger_path = ledger_path_for(project, args.ledger)
    require_ledger(ledger_path)
    signals_path = args.signals or ensure_project_signals(project)
    signals = topics.load_signals(signals_path)
    tdir = topics_dir_for(project)

    spec = topics.load_spec(tdir, args.topic)
    kind = args.kind or (spec.get("kind") if spec else None)
    if not kind:
        print("[error] --kind is required the first time a topic is proposed.")
        return 2
    if kind not in topics.KINDS:
        print(f"[error] unknown --kind {kind!r}; must be one of: {', '.join(topics.KINDS)}")
        return 2

    params = dict(spec.get("params", {})) if spec else topics.default_params_for_kind(kind)
    _apply_param_overrides(params, args)

    if spec is None:
        spec = topics.new_spec(args.topic, kind, params)
    else:
        spec["params"] = params
        spec["kind"] = kind

    rows = topics.load_ledger(ledger_path)
    try:
        candidates, warnings = topics.run_recipe(kind, rows, params, signals, tdir)
    except ValueError as exc:
        print(f"[error] {exc}")
        return 2

    stats = topics.merge_candidates(spec, candidates)
    topics.save_spec(tdir, args.topic, spec)

    ledger_by_id = {r["id"]: r for r in rows}
    review_path = topics.review_csv_path(tdir, args.topic)
    topics.write_review_csv(review_path, spec, ledger_by_id)

    print(f"\n=== propose: {args.topic} ({kind}) ===")
    print(f"params: {params}")
    for w in warnings:
        print(f"[warn] {w}")
    print(f"candidates scanned:   {stats['candidates']}")
    print(f"newly confident:      {stats['added_confident']}")
    print(f"newly pending review: {stats['added_pending']}")
    print(f"total included:       {stats['total_ids']}")
    print(f"total pending review: {stats['total_pending']}")
    print(f"\nspec:   {topics.spec_path(tdir, args.topic)}")
    print(f"review: {review_path}")
    if stats["total_pending"]:
        print(f"\n[next] {stats['total_pending']} row(s) need your decision - open the review file above,")
        print("       then run `review --include-id ...` / `--exclude-id ...` for each, or ask the assistant to do it.")
    else:
        print("\n[next] nothing pending - run `extract` to write the topic file.")
    return 0


def _apply_param_overrides(params: dict, args) -> None:
    if args.date1:
        params["date1"] = args.date1
    if args.date2:
        params["date2"] = args.date2
    if args.date_field:
        params["date_field"] = args.date_field
    if args.scope:
        params["scope"] = args.scope
    if args.lookback_days is not None:
        params["lookback_days"] = args.lookback_days
    if args.extended_lookback_days is not None:
        params["extended_lookback_days"] = args.extended_lookback_days
    if args.tail_days is not None:
        params["tail_days"] = args.tail_days
    destination = _split_multi(args.destination)
    if destination:
        params["destination"] = destination
    region = _split_multi(args.region)
    if region:
        params["region"] = region
    category = _split_multi(args.category)
    if category:
        params["category"] = category
    exclude_category = _split_multi(args.exclude_category)
    if exclude_category:
        params["exclude_category"] = exclude_category
    keyword = _split_multi(args.keyword)
    if keyword:
        params["keyword"] = keyword
    exclude_keyword = _split_multi(args.exclude_keyword)
    if exclude_keyword:
        params["exclude_keyword"] = exclude_keyword
    flag = _split_multi(args.flag)
    if flag:
        params["flag"] = flag
    source_type = _split_multi(args.source_type)
    if source_type:
        params["source_type"] = source_type
    if args.min_amount is not None:
        params["min_amount"] = args.min_amount
    if args.max_amount is not None:
        params["max_amount"] = args.max_amount
    exclude_topic = _split_multi(args.exclude_topic)
    if exclude_topic:
        params["exclude_topic"] = exclude_topic


# ------------------------------------------------------------------- review

def cmd_review(args) -> int:
    project = os.path.abspath(args.project)
    ledger_path = ledger_path_for(project, args.ledger)
    require_ledger(ledger_path)
    tdir = topics_dir_for(project)

    spec = topics.load_spec(tdir, args.topic)
    if spec is None:
        print(f"[error] topic {args.topic!r} not found under {tdir}. Run `propose` first.")
        return 2

    include_ids = _split_multi(args.include_id)
    exclude_ids = _split_multi(args.exclude_id)
    pending_ids = _split_multi(args.pending_id)
    if not (include_ids or exclude_ids or pending_ids):
        print("[error] nothing to do - pass --include-id / --exclude-id / --pending-id.")
        return 2

    moved = topics.apply_review(spec, include_ids, exclude_ids, pending_ids)
    topics.save_spec(tdir, args.topic, spec)

    rows = topics.load_ledger(ledger_path)
    ledger_by_id = {r["id"]: r for r in rows}
    review_path = topics.review_csv_path(tdir, args.topic)
    topics.write_review_csv(review_path, spec, ledger_by_id)

    print(f"\n=== review: {args.topic} ===")
    print(f"moved to included:  {moved['included']}")
    print(f"moved to excluded:  {moved['excluded']}")
    print(f"moved to pending:   {moved['pending']}")
    print(f"total included:     {len(spec['ids'])}")
    print(f"total pending:      {len(spec['pending_review'])}")
    print(f"total excluded:     {len(spec['excluded_ids'])}")
    print(f"\nreview: {review_path}")
    if spec["pending_review"]:
        print(f"[next] {len(spec['pending_review'])} row(s) still pending review.")
    else:
        print("[next] nothing pending - run `extract` to write the topic file.")
    return 0


# ------------------------------------------------------------------ extract

def cmd_extract(args) -> int:
    project = os.path.abspath(args.project)
    ledger_path = ledger_path_for(project, args.ledger)
    require_ledger(ledger_path)
    tdir = topics_dir_for(project)

    spec = topics.load_spec(tdir, args.topic)
    if spec is None:
        print(f"[error] topic {args.topic!r} not found under {tdir}. Run `propose` first.")
        return 2
    if spec["pending_review"]:
        print(f"[warn] {len(spec['pending_review'])} row(s) are still pending_review and will be OMITTED from the "
              f"output. Run `review` first if they belong to this topic, or re-run with --force to extract anyway.")
        if not args.force:
            return 1

    rows = topics.load_ledger(ledger_path)
    out_rows = topics.extract_rows(rows, spec, sort=args.sort)
    if not out_rows:
        print("[warn] no included rows to extract yet - nothing written.")
        return 1

    if args.split_by:
        out_dir = args.out or os.path.join(tdir, args.topic)
        if os.path.isdir(out_dir) and not args.update:
            existing = [f for f in os.listdir(out_dir) if f.endswith(".csv")]
            if existing:
                print(f"[error] {out_dir} already has output files; pass --update to overwrite.")
                return 2
        os.makedirs(out_dir, exist_ok=True)
        date_field = args.date_field or spec["params"].get("date_field", "date_iso")
        buckets: dict[str, list[dict]] = {}
        skipped = 0
        for r in out_rows:
            label = topics.period_label(r.get(date_field), args.split_by)
            if label is None:
                skipped += 1
                continue
            buckets.setdefault(label, []).append(r)
        written = []
        for label in sorted(buckets):
            path = os.path.join(out_dir, f"{label}.csv")
            from finance_core import io_xlsx
            io_xlsx.write_csv(path, topics.schema.OUTPUT_COLUMNS, [topics.to_output_row(r) for r in buckets[label]])
            written.append(path)
        print(f"\n=== extract: {args.topic} ===")
        print(f"rows written:   {len(out_rows) - skipped} (skipped {skipped} with no valid {date_field})")
        print(f"files written:  {len(written)}")
        for p in written:
            print(f"  {p}")
    else:
        out_path = args.out or os.path.join(tdir, f"{args.topic}.csv")
        if os.path.exists(out_path) and not args.update:
            print(f"[error] {out_path} already exists; pass --update to overwrite.")
            return 2
        from finance_core import io_xlsx
        io_xlsx.write_csv(out_path, topics.schema.OUTPUT_COLUMNS, [topics.to_output_row(r) for r in out_rows])
        print(f"\n=== extract: {args.topic} ===")
        print(f"rows written: {len(out_rows)}")
        print(f"file:         {out_path}")
    return 0


# --------------------------------------------------------------------- list

def cmd_list(args) -> int:
    project = os.path.abspath(args.project)
    tdir = topics_dir_for(project)
    names = topics.discover_topic_names(tdir)
    if not names:
        print(f"[info] no topics yet under {tdir}")
        return 0
    print(f"{'topic':<24} {'kind':<16} {'included':>9} {'pending':>8} {'excluded':>9}")
    for name in names:
        spec = topics.load_spec(tdir, name)
        print(f"{name:<24} {spec.get('kind', ''):<16} {len(spec.get('ids', [])):>9} "
              f"{len(spec.get('pending_review', [])):>8} {len(spec.get('excluded_ids', [])):>9}")
    return 0


# ---------------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Extract a topic-related subset of the finance-unify ledger.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--project", default=os.getcwd(), help="Project root (default: cwd).")
        p.add_argument("--ledger", help="Ledger CSV path (default: <project>/data/ledger.csv).")

    p_propose = sub.add_parser("propose", help="Scan the ledger and update a topic's spec + review CSV.")
    common(p_propose)
    p_propose.add_argument("--topic", required=True, help="Topic name (used for file names).")
    p_propose.add_argument("--kind", choices=topics.KINDS, help="Required the first time a topic is created.")
    p_propose.add_argument("--date1", help="Start date, ISO yyyy-mm-dd.")
    p_propose.add_argument("--date2", help="End date, ISO yyyy-mm-dd.")
    p_propose.add_argument("--date-field", dest="date_field", choices=["date_iso", "billing_date"],
                            help="Which date column to compare against (default: date_iso).")
    p_propose.add_argument("--scope", choices=["all", "expenses", "income"], help="Restrict by cash-flow direction.")
    p_propose.add_argument("--lookback-days", dest="lookback_days", type=int,
                            help="How far before date1 to look for pre-purchases (trip kinds; default 45).")
    p_propose.add_argument("--extended-lookback-days", dest="extended_lookback_days", type=int,
                            help="How far before date1 to look for long-lead bookings - flights, shows, "
                                 "attractions (trip kinds; default 120). Matches here are always pending_review.")
    p_propose.add_argument("--tail-days", dest="tail_days", type=int,
                            help="How far after date2 to look for late settlements (trip_abroad; default 14).")
    p_propose.add_argument("--destination", action="append", help="Destination keyword(s), comma-separated OK (trip_abroad).")
    p_propose.add_argument("--region", action="append", help="Region keyword(s), comma-separated OK (trip_local).")
    p_propose.add_argument("--category", action="append", help="Category (ענף) value(s) to match (custom).")
    p_propose.add_argument("--exclude-category", dest="exclude_category", action="append", help="Category value(s) to exclude (custom).")
    p_propose.add_argument("--keyword", action="append", help="Description/detail keyword(s) to match (custom).")
    p_propose.add_argument("--exclude-keyword", dest="exclude_keyword", action="append", help="Description/detail keyword(s) to exclude (custom).")
    p_propose.add_argument("--flag", action="append", help="Ledger flag value(s) to match (custom).")
    p_propose.add_argument("--source-type", dest="source_type", action="append", help="bank|fx|credit-card (custom).")
    p_propose.add_argument("--min-amount", dest="min_amount", type=float, help="Minimum amount_ils (custom).")
    p_propose.add_argument("--max-amount", dest="max_amount", type=float, help="Maximum amount_ils (custom).")
    p_propose.add_argument("--exclude-topic", dest="exclude_topic", action="append",
                            help="Topic name(s) to subtract (exclude_topics; default: auto-discover trip topics).")
    p_propose.add_argument("--signals", help="Override path to topic_signals.yaml.")
    p_propose.set_defaults(func=cmd_propose)

    p_review = sub.add_parser("review", help="Move ids between included / pending_review / excluded.")
    common(p_review)
    p_review.add_argument("--topic", required=True)
    p_review.add_argument("--include-id", dest="include_id", action="append", help="Row id(s) to approve into the output.")
    p_review.add_argument("--exclude-id", dest="exclude_id", action="append", help="Row id(s) to reject.")
    p_review.add_argument("--pending-id", dest="pending_id", action="append", help="Row id(s) to move back to pending.")
    p_review.set_defaults(func=cmd_review)

    p_extract = sub.add_parser("extract", help="Write the topic CSV(s) from the spec's included ids.")
    common(p_extract)
    p_extract.add_argument("--topic", required=True)
    p_extract.add_argument("--sort", choices=["seq", "op_seq"], default="op_seq", help="Ordering (default: op_seq).")
    p_extract.add_argument("--split-by", dest="split_by", choices=["month", "quarter", "year"], help="Write one file per period.")
    p_extract.add_argument("--date-field", dest="date_field", choices=["date_iso", "billing_date"],
                            help="Date column used for --split-by (default: the topic's stored date_field).")
    p_extract.add_argument("--out", help="Output file (or directory, with --split-by).")
    p_extract.add_argument("--update", action="store_true", help="Overwrite an existing output.")
    p_extract.add_argument("--force", action="store_true", help="Extract even if rows are still pending_review.")
    p_extract.set_defaults(func=cmd_extract)

    p_list = sub.add_parser("list", help="List topics in this project.")
    common(p_list)
    p_list.set_defaults(func=cmd_list)

    return ap


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

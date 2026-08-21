#!/usr/bin/env python3
"""finance-unify: turn a MyFinanda multi-sheet XLSX export into one unified,
chronological transaction ledger (CSV, optional XLSX).

Run: python scripts/unify.py [--source PATH] [--project DIR] [--incremental]
                             [--xlsx] [--set "id:field=value" ...]

See README.md for details. Depends only on the Python standard library for CSV
output; openpyxl/pyyaml are optional.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import sys
from collections import Counter, defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

# Make finance_core importable: it lives one level above this skill folder.
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # finance-unify/
_SKILLS_ROOT = os.path.dirname(_SKILL_DIR)
if _SKILLS_ROOT not in sys.path:
    sys.path.insert(0, _SKILLS_ROOT)

# The shared merchant->category map ships with this skill.
_MERCHANT_MAP = os.path.join(_SKILL_DIR, "merchant_categories.csv")

from finance_core import (  # noqa: E402
    classifier,
    columns,
    corrections as corr,
    fx,
    io_xlsx,
    merchants,
    reconcile,
    registry,
    schema,
)


# --------------------------------------------------------------------- config

def load_config(project: str, explicit: str | None) -> dict:
    path = explicit or os.path.join(project, "finance.config.yaml")
    if not os.path.exists(path):
        return {}
    try:
        import yaml  # type: ignore
    except ImportError:
        print(f"[warn] {path} found but PyYAML not installed; ignoring config.")
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def resolve(arg, config_key, config, default):
    if arg:
        return arg
    if config.get(config_key):
        return config[config_key]
    return default


def ensure_project_taxonomy(project: str) -> str:
    """Create an editable per-project copy of category_map.yaml on first run.
    Edit this copy for project-specific taxonomy; the master remains a fallback."""
    dest = os.path.join(project, "category_map.yaml")
    if not os.path.exists(dest):
        master = os.path.join(_SKILLS_ROOT, "finance_core", "data", "category_map.yaml")
        if os.path.exists(master):
            os.makedirs(project, exist_ok=True)
            shutil.copyfile(master, dest)
            print(f"[info] created per-project taxonomy: {dest} (edit to customize)")
    return dest


def pick_source(source_arg: str, source_dir: str) -> str:
    target = source_arg or source_dir
    if target and os.path.isfile(target):
        return target
    search_dir = target if (target and os.path.isdir(target)) else source_dir
    candidates = glob.glob(os.path.join(search_dir, "*.xlsx"))
    candidates = [c for c in candidates if not os.path.basename(c).startswith("~$")]
    if not candidates:
        raise SystemExit(f"No .xlsx source found in {search_dir!r}")
    return max(candidates, key=os.path.getmtime)


# ------------------------------------------------------------------- building

def build_records(sheets) -> tuple[list[dict], dict]:
    reg = registry.build(sheets)
    codes: dict[str, str] = {}
    used_codes: set[str] = set()

    def code_for(source_type: str, name: str) -> str:
        if name in codes:
            return codes[name]
        base = schema.source_code(source_type, name)
        code = base
        n = 2
        while code in used_codes:
            code = f"{base}_{n}"
            n += 1
        used_codes.add(code)
        codes[name] = code
        return code

    records: list[dict] = []
    per_source: Counter = Counter()
    for sheet in sheets:
        kind = classifier.classify(sheet, reg)
        if kind == "skip":
            continue
        currency = reg.account_currency.get(sheet.name, "ILS")
        code = code_for(kind, sheet.name)
        for i, raw in enumerate(sheet.rows, start=1):
            translated = columns.translate(raw, kind)
            rec = schema.build_record(translated, kind, sheet.name, currency, i, code)
            schema.flag_basic(rec)
            records.append(rec)
            per_source[sheet.name] += 1

    fx.apply_rates(records)
    kept, stats = reconcile.reconcile(records)
    stats["sources"] = dict(per_source)
    return kept, stats


# ---------------------------------------------------------- assumed category

def _norm(text: str) -> str:
    s = re.sub(r"\d+", " ", text or "")
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    return " ".join(s.lower().split())[:40]


def infer_categories(records: list[dict]) -> int:
    ref: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        cat = (r.get("category") or "").strip()
        if cat and cat != columns.UNCLASSIFIED:
            key = _norm(r.get("description", ""))
            if key:
                ref[key][cat] += 1
    applied = 0
    for r in records:
        if "unmatched_category" not in r.get("flags", []):
            continue
        key = _norm(r.get("description", ""))
        if key in ref and ref[key]:
            cat = ref[key].most_common(1)[0][0]
            r["category"] = cat
            r["flags"].remove("unmatched_category")
            schema.add_flag(r, "assumed_category")
            applied += 1
    return applied


def promotable_corrections(records, corrections, entries):
    """Project corrections that set a category for a merchant the shared map does
    NOT already cover -> candidates to add to the skill's merchant map for reuse.
    Returns a de-duplicated list of (merchant, category)."""
    by_id = {r["id"]: r for r in records}
    seen: set = set()
    out: list[tuple[str, str]] = []
    for rid, fields in corrections.items():
        cat = (fields.get("category") or "").strip()
        if not cat or cat == columns.UNCLASSIFIED:
            continue
        rec = by_id.get(rid)
        if rec is None:
            continue
        merchant = (rec.get("description") or "").strip()
        if not merchant or merchants.covers(merchant, entries):
            continue
        keytuple = (merchant, cat)
        if keytuple in seen:
            continue
        seen.add(keytuple)
        out.append((merchant, cat))
    return out


# --------------------------------------------------------------- incremental

def load_existing(path: str) -> list[dict]:
    import csv

    rows: list[dict] = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            rid = (row.get("id") or "").strip()
            if not rid:
                continue
            rec = {k: (row.get(k) or "") for k in schema.OUTPUT_COLUMNS}
            rec["flags"] = [f for f in (row.get("flags") or "").split(";") if f]
            rec["amount_ils"] = _num(row.get("amount_ils"))
            rec["amount_orig"] = _num(row.get("amount_orig"))
            rec["ils_rate"] = _num(row.get("ils_rate"))
            rec["date_serial"] = _num(row.get("date_serial"))
            src_key, _, suffix = rid.rpartition("-")
            rec["source_key"] = src_key or rid
            rec["orig_row"] = int(suffix) if suffix.isdigit() else 0
            rows.append(rec)
    return rows


def _num(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def merge_incremental(current: list[dict], existing: list[dict]) -> list[dict]:
    by_id = {r["id"]: r for r in existing}
    for rec in current:
        old = by_id.get(rec["id"])
        if old is not None:
            # Fresh export wins, but keep a real/manual category if the fresh row
            # is still unclassified.
            new_cat = (rec.get("category") or "").strip()
            old_cat = (old.get("category") or "").strip()
            if (not new_cat or new_cat == columns.UNCLASSIFIED) and old_cat and old_cat != columns.UNCLASSIFIED:
                rec["category"] = old_cat
                if "unmatched_category" in rec.get("flags", []):
                    rec["flags"].remove("unmatched_category")
                if "manual_category" in old.get("flags", []):
                    schema.add_flag(rec, "manual_category")
                else:
                    schema.add_flag(rec, "assumed_category")
        by_id[rec["id"]] = rec
    return list(by_id.values())


# -------------------------------------------------------------------- ordering

def assign_ordinals(records: list[dict]) -> list[dict]:
    seq_sorted = sorted(
        records,
        key=lambda r: ((r.get("billing_date") or "9999-99-99"), r.get("source_name", ""), r.get("orig_row", 0)),
    )
    for n, r in enumerate(seq_sorted, start=1):
        r["seq"] = n
    op_sorted = sorted(
        records,
        key=lambda r: (
            r["date_serial"] if r.get("date_serial") is not None else 1e18,
            r.get("source_name", ""),
            r.get("orig_row", 0),
        ),
    )
    for n, r in enumerate(op_sorted, start=1):
        r["op_seq"] = n
    return seq_sorted


# ------------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser(description="Unify a MyFinanda XLSX export.")
    ap.add_argument("--source", help="Source .xlsx file or directory.")
    ap.add_argument("--project", default=os.getcwd(), help="Project root (default: cwd).")
    ap.add_argument("--config", help="Path to finance.config.yaml.")
    ap.add_argument("--out", help="Output ledger CSV path.")
    ap.add_argument("--flags-out", dest="flags_out", help="Flag report CSV path.")
    ap.add_argument("--corrections", help="Corrections CSV path.")
    ap.add_argument("--incremental", action="store_true", help="Extend an existing ledger.")
    ap.add_argument("--xlsx", action="store_true", help="Also write an .xlsx mirror.")
    ap.add_argument("--set", dest="sets", action="append", default=[],
                    help='Correction id:field=value (repeatable).')
    ap.add_argument("--merchant-map", dest="merchant_map",
                    help=f"Shared merchant->category CSV (default: {_MERCHANT_MAP}).")
    ap.add_argument("--promote-corrections", dest="promote", action="store_true",
                    help="Add this run's project category corrections to the shared "
                         "merchant map for reuse across projects.")
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    config = load_config(project, args.config)
    ensure_project_taxonomy(project)

    source_dir = resolve(None, "source_dir", config, os.path.join(project, "data", "source"))
    out_path = resolve(args.out, "ledger", config, os.path.join(project, "data", "ledger.csv"))
    flags_path = resolve(args.flags_out, "flags", config, os.path.join(project, "data", "ledger.flags.csv"))
    corr_path = resolve(args.corrections, "corrections", config, os.path.join(project, "data", "corrections.csv"))
    xlsx_path = resolve(None, "ledger_xlsx", config, os.path.join(project, "data", "ledger.xlsx"))

    source = pick_source(args.source, source_dir)
    print(f"[info] source: {source}")

    sheets = io_xlsx.read_workbook(source)
    records, stats = build_records(sheets)

    if args.incremental:
        existing = load_existing(out_path)
        if existing:
            records = merge_incremental(records, existing)
            print(f"[info] incremental: merged with {len(existing)} existing rows.")

    # Shared merchant->category map: fill rows MyFinanda left unclassified.
    merchant_map_path = resolve(args.merchant_map, "merchant_map", config, _MERCHANT_MAP)
    merchant_entries = merchants.load(merchant_map_path)
    mapped = merchants.apply(records, merchant_entries)

    inferred = infer_categories(records)

    # Corrections from the prompt are appended to the corrections file, then the
    # whole file is applied.
    if args.sets:
        entries = [corr.parse_set_arg(s) for s in args.sets]
        corr.append(corr_path, entries)
        print(f"[info] appended {len(entries)} correction(s) to {corr_path}")
    corrections = corr.load(corr_path)
    applied = corr.apply(records, corrections)

    # Project corrections that the shared map doesn't cover can be promoted so
    # they are reused across every project.
    promote = promotable_corrections(records, corrections, merchant_entries)
    promoted = 0
    if promote and args.promote:
        promoted = merchants.append(merchant_map_path, [(m, c, "user") for m, c in promote])
        print(f"[info] added {promoted} merchant mapping(s) to {merchant_map_path}")

    ordered = assign_ordinals(records)

    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)

    out_rows = [schema.to_output(r) for r in ordered]
    io_xlsx.write_csv(out_path, schema.OUTPUT_COLUMNS, out_rows)

    if args.xlsx:
        if io_xlsx.write_xlsx(xlsx_path, schema.OUTPUT_COLUMNS, out_rows):
            print(f"[info] wrote xlsx mirror: {xlsx_path}")
        else:
            print("[warn] --xlsx requested but openpyxl not installed; skipped.")

    write_flag_report(flags_path, ordered)

    _summary(ordered, stats, mapped, inferred, applied, out_path, flags_path)

    if promote and not args.promote:
        print(f"\n[note] {len(promote)} project correction(s) are not in the shared "
              f"merchant map ({merchant_map_path}).")
        print("       Re-run with --promote-corrections to reuse them across projects:")
        for merchant, cat in promote[:20]:
            print(f"         {merchant}  ->  {cat}")
        if len(promote) > 20:
            print(f"         ... and {len(promote) - 20} more")
    return 0


def write_flag_report(path: str, records: list[dict]) -> None:
    rows = []
    field_for = {
        "unmatched_category": "category",
        "assumed_category": "category",
        "merchant_category": "category",
        "manual_category": "category",
        "no_fx_rate": "amount_ils",
        "assumed_fx_rate": "ils_rate",
        "unmatched_cc": "amount_ils",
        "refund": "amount_ils",
        "installment": "installment_total",
    }
    for r in records:
        for flag in r.get("flags", []):
            field = field_for.get(flag, "")
            rows.append({
                "seq": r.get("seq", ""),
                "id": r.get("id", ""),
                "flag": flag,
                "field": field,
                "value": r.get(field, "") if field else "",
                "date_iso": r.get("date_iso", ""),
                "source_name": r.get("source_name", ""),
                "description": r.get("description", ""),
            })
    cols = ["seq", "id", "flag", "field", "value", "date_iso", "source_name", "description"]
    io_xlsx.write_csv(path, cols, rows)


def _summary(records, stats, mapped, inferred, applied, out_path, flags_path) -> None:
    flag_counts: Counter = Counter()
    for r in records:
        for f in r.get("flags", []):
            flag_counts[f] += 1
    print("\n=== finance-unify summary ===")
    print(f"rows in ledger:            {len(records)}")
    print(f"cc charges replaced:       {stats['cc_replaced']}")
    print(f"cc charges unmatched:      {stats['cc_unmatched']}")
    print(f"round cc -> cash transfer: {stats.get('round_cc_reclassified', 0)}")
    print(f"transfers annihilated:     {stats['transfers_annihilated']} pairs")
    print(f"merchant categories set:   {mapped}")
    print(f"categories inferred:       {inferred}")
    print(f"corrections applied:       {applied}")
    if flag_counts:
        print("flags:")
        for flag, n in sorted(flag_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {flag:20s} {n}")
    print(f"ledger:  {out_path}")
    print(f"flags:   {flags_path}")


if __name__ == "__main__":
    raise SystemExit(main())

"""finance-analyze (Skill 3) core logic.

Aggregates a finance-topic selection - a single topic or the union of a
"suite" of topics - out of the finance-unify ledger into the structures the
.xlsx report renders (finance_core.report_xlsx):

  - rollups by coarse bucket and by category group, both reconstructed from
    `category` via category_map.yaml (the ledger does not store them);
  - rollups by period (month / quarter / year);
  - a flat drill-down row list that keeps every member record's stable `id`;
  - reconciliation notes: rows with no ILS amount (no_fx_rate) that cannot be
    summed, leftover internal-transfer legs excluded from the sums, bound
    refund<->expense pairs annihilated inside the analyzed set, stale topic
    ids, warnings.

Currency basis is always the signed `amount_ils` column produced by Skill 1.
Pure standard library: openpyxl is required only by report_xlsx.
"""

from __future__ import annotations

from . import columns, refunds, taxonomy, topics

# The ledger stores no group; transfers/CC settlements are recognized either
# through the reconstructed group or - as a fallback when category_map.yaml
# cannot be loaded (no PyYAML) - straight from the known categories.
TRANSFER_GROUP = "העברות פנימיות"
_TRANSFER_CATEGORIES = set(columns.INTERNAL_TRANSFER_CATEGORIES) | {
    columns.CC_CHARGE_CATEGORY
}

TRIP_KINDS = {"trip_abroad", "trip_local"}
_CONTENT_FILTER_KEYS = (
    "category",
    "exclude_category",
    "keyword",
    "exclude_keyword",
    "flag",
    "source_type",
    "min_amount",
    "max_amount",
)

DETAIL_COLUMNS = [
    "id",
    "date_iso",
    "billing_date",
    "source_name",
    "source_type",
    "description",
    "detail",
    "category",
    "group",
    "bucket",
    "amount_ils",
    "currency_orig",
    "amount_orig",
    "ils_rate",
    "installment_no",
    "installment_total",
    "side",
    "period",
    "topics",
    "flags",
    "explanation",
]

SCOPES = ("expenses", "income", "all")


# ------------------------------------------------------------------ loading

def load_category_map(project: str, explicit: str | None = None) -> tuple[dict, str]:
    """Per-project category_map.yaml wins; the master copy under finance_core
    is the shared fallback. Returns (mapping, path_used)."""
    import os

    if explicit and os.path.exists(explicit):
        return taxonomy.load(explicit), explicit
    here = os.path.dirname(__file__)
    for cand in (os.path.join(project, "category_map.yaml"),
                 os.path.join(here, "data", "category_map.yaml")):
        if os.path.exists(cand):
            try:
                return taxonomy.load(cand), cand
            except Exception as exc:  # malformed YAML etc.
                print(f"[warn] could not load {cand}: {exc}")
                break
    return {}, ""


def resolve_rows(ledger_rows: list[dict], ids: list[str]) -> tuple[list[dict], list[str]]:
    """Ledger records for `ids`, in seq order (then op_seq/id); ids absent from
    the ledger come back separately as stale."""
    by_id = {r["id"]: r for r in ledger_rows}
    found, stale = [], []
    for cid in ids:
        (found if cid in by_id else stale).append(cid)
    rows = [by_id[cid] for cid in found]

    def _key(r):
        seq = r.get("seq")
        op = r.get("op_seq")
        return (
            seq if isinstance(seq, int) else 1 << 30,
            op if isinstance(op, int) else 1 << 30,
            r["id"],
        )

    rows.sort(key=_key)
    return rows, stale


def load_specs(topics_dir: str, names: list[str]) -> tuple[list[dict], list[str]]:
    """Specs for `names`; missing ones are reported (with what does exist)."""
    missing = [n for n in names if topics.load_spec(topics_dir, n) is None]
    specs = [s for n in names if (s := topics.load_spec(topics_dir, n)) is not None]
    return specs, missing


# ------------------------------------------------------------- classification

def classify_row(r: dict, mapping: dict) -> tuple[str, str, str]:
    """-> (group, bucket, side); side in fx_gap|transfer|expense|income."""
    cat = (r.get("category") or "").strip()
    group = taxonomy.group_for(cat, mapping) or ""
    bucket = taxonomy.bucket_for(cat, mapping) or group or cat
    amt = r.get("amount_ils")
    if amt is None:
        side = "fx_gap"
    elif group == TRANSFER_GROUP or cat in _TRANSFER_CATEGORIES:
        side = "transfer"
    elif amt < 0:
        side = "expense"
    else:
        side = "income"
    return group, bucket, side


def primary_metric(scope: str) -> str:
    """The one number pies/columns render: expenses for `expenses` AND `all`
    (a pie of signed nets would be meaningless); income for `income`.
    Tables always carry expenses/income/net columns regardless."""
    return "income" if scope == "income" else "expenses"


# ------------------------------------------------------------------ rollups

def _blank_entry(label: str) -> dict:
    return {"label": label, "expenses": 0.0, "income": 0.0, "net": 0.0, "count": 0}


def _add(entry: dict, rec: dict) -> None:
    amt = rec.get("amount_ils") or 0.0
    entry["count"] += 1
    if amt < 0:
        entry["expenses"] += -amt
    elif amt > 0:
        entry["income"] += amt
    entry["net"] = entry["income"] - entry["expenses"]


def _month_labels(start: str, end: str) -> list[str]:
    year, month = (int(part) for part in start.split("-"))
    end_year, end_month = (int(part) for part in end.split("-"))
    labels = []
    while (year, month) <= (end_year, end_month):
        labels.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return labels


def build_monthly_matrix(rows: list[dict], month_range: tuple[str, str]) -> dict:
    """Return expense-group totals for every requested month, including zero months."""
    labels = _month_labels(*month_range)
    values: dict[str, dict[str, float]] = {label: {} for label in labels}
    group_totals: dict[str, float] = {}
    for row in rows:
        if row["side"] != "expense" or row.get("period") not in values:
            continue
        group = row.get("group") or row.get("category") or "(uncategorized)"
        amount = -(row.get("amount_ils") or 0.0)
        values[row["period"]][group] = values[row["period"]].get(group, 0.0) + amount
        group_totals[group] = group_totals.get(group, 0.0) + amount
    groups = sorted(group_totals, key=lambda group: (-group_totals[group], group))
    matrix_rows = []
    for label in labels:
        row_values = {group: round(values[label].get(group, 0.0), 2) for group in groups}
        matrix_rows.append({"label": label, "values": row_values, "total": round(sum(row_values.values()), 2)})
    divisor = len(labels) or 1
    totals = {group: round(sum(row["values"][group] for row in matrix_rows), 2) for group in groups}
    matrix_rows.append({"label": "Average", "values": {group: round(totals[group] / divisor, 2) for group in groups},
                        "total": round(sum(totals.values()) / divisor, 2)})
    matrix_rows.append({"label": "Total", "values": totals, "total": round(sum(totals.values()), 2)})
    return {"groups": groups, "rows": matrix_rows, "month_count": len(labels)}


def build_trip_comparison(reports: list[dict], specs: list[dict]) -> dict:
    """Summarize approved trip topics and their continuous transaction-month costs."""
    type_for_kind = {"trip_abroad": "Trip", "trip_local": "Vacation"}
    report_by_name = {report["label"]: report for report in reports}
    rows = []
    months: list[str] = []
    for spec in specs:
        report = report_by_name.get(spec["topic"])
        if report is None:
            continue
        dates = sorted(row.get("date_iso") or "" for row in report["rows"] if row.get("date_iso"))
        months.extend(row.get("period") or "" for row in report["rows"] if row.get("period"))
        rows.append({
            "topic": spec["topic"],
            "type": type_for_kind[spec["kind"]],
            "first_date": dates[0] if dates else "",
            "last_date": dates[-1] if dates else "",
            "expense_rows": report["totals"]["expense_rows"],
            "cost_ils": report["totals"]["expenses"],
            "periods": {entry["label"]: entry["expenses"] for entry in report["by_period"]},
        })
    labels = _month_labels(min(months), max(months)) if months else []
    for row in rows:
        periods = row.pop("periods")
        row["monthly"] = {label: periods.get(label, 0.0) for label in labels}
    trip_rows = [row for row in rows if row["type"] == "Trip"]
    vacation_rows = [row for row in rows if row["type"] == "Vacation"]
    trip_total = round(sum(row["cost_ils"] for row in trip_rows), 2)
    vacation_total = round(sum(row["cost_ils"] for row in vacation_rows), 2)
    monthly_rows = []
    for label in labels:
        values = {row["topic"]: round(row["monthly"][label], 2) for row in rows}
        monthly_rows.append({"label": label, "values": values, "total": round(sum(values.values()), 2)})
    divisor = len(labels) or 1
    totals = {row["topic"]: round(row["cost_ils"], 2) for row in rows}
    monthly_rows.extend((
        {"label": "Average", "values": {topic: round(value / divisor, 2) for topic, value in totals.items()},
         "total": round(sum(totals.values()) / divisor, 2)},
        {"label": "Total", "values": totals, "total": round(sum(totals.values()), 2)},
    ))
    return {
        "topics": [row["topic"] for row in rows],
        "summary_rows": rows,
        "monthly_rows": monthly_rows,
        "month_count": len(labels),
        "trip_count": len(trip_rows),
        "vacation_count": len(vacation_rows),
        "trip_total": trip_total,
        "vacation_total": vacation_total,
        "average_trip_cost": round(trip_total / len(trip_rows), 2) if trip_rows else 0.0,
        "average_vacation_cost": round(vacation_total / len(vacation_rows), 2) if vacation_rows else 0.0,
    }


def build_report(
    rows: list[dict],
    *,
    label: str = "",
    scope: str = "expenses",
    split_by: str = "month",
    date_field: str = "billing_date",
    split_dates: list[str] | None = None,
    phase_ranges: dict[str, tuple[str, str]] | None = None,
    chart_dim: str = "bucket",
    top_n: int = 12,
    mapping: dict | None = None,
    row_topics: dict | None = None,
    extra_notes: dict | None = None,
    force_include: set[str] | None = None,
    monthly_matrix_range: tuple[str, str] | None = None,
) -> dict:
    """Aggregate resolved ledger rows into the report structures.

    `rows` must already be resolved (resolve_rows) and refund-annihilated by
    the caller when requested; annihilation results reach the notes via
    `extra_notes` ({annihilated, annihilated_stats}).

    `date_field` picks which ledger date column drives every period split
    (month/quarter/year/week/phase/custom): `date_iso` (operation date) or
    `billing_date` (default). `split_dates` (raw ISO strings) are only used
    when `split_by == "custom"`. `phase_ranges` (row id -> (date1, date2) ISO
    strings, the owning topic's trip window) are only used when
    `split_by == "phase"`.

    `force_include` is a set of row ids that must NOT land on the transfers
    side even though their category/group says otherwise (e.g. an FX cash
    withdrawal category row that is actually trip spending) - they are
    reclassified as expense/income by sign instead.
    """
    mapping = mapping or {}
    row_topics = row_topics or {}
    force_include = force_include or set()
    split_date_objs = sorted(
        d for d in (topics.parse_date(v) for v in (split_dates or [])) if d)
    parsed_phase_ranges = {
        cid: (topics.parse_date(d1), topics.parse_date(d2))
        for cid, (d1, d2) in (phase_ranges or {}).items()
    }
    notes = {
        "fx_gap": [],
        "transfers": [],
        "stale_ids": [],
        "annihilated": [],
        "annihilated_stats": {},
        "forced_include": [],
        "warnings": [],
    }
    if extra_notes:
        notes.update(extra_notes)
    if not mapping:
        notes["warnings"].append(
            "category_map.yaml could not be loaded (missing file or PyYAML not "
            "installed) - groups/buckets fall back to the raw category text")

    enriched: list[dict] = []
    for r in rows:
        group, bucket, side = classify_row(r, mapping)
        forced = False
        if side == "transfer" and r["id"] in force_include:
            amt = r.get("amount_ils") or 0.0
            side = "expense" if amt < 0 else "income"
            forced = True
        date_val = r.get(date_field) or r.get("date_iso")
        phase_range = parsed_phase_ranges.get(r["id"]) if split_by == "phase" else None
        period = topics.period_label(
            date_val, split_by, phase_range=phase_range, split_dates=split_date_objs)
        if date_val and period is None:
            if split_by == "phase":
                notes["warnings"].append(
                    f"row {r['id']}: no trip date range available for phase split "
                    f"(topic missing date1/date2)")
            else:
                notes["warnings"].append(
                    f"row {r['id']}: unparsable {date_field} {date_val!r}")
        rec = dict(r)
        rec["group"], rec["bucket"], rec["side"] = group, bucket, side
        rec["period"] = period or ""
        rec["topics"] = ";".join(row_topics.get(r["id"], []))
        enriched.append(rec)
        if forced:
            notes["forced_include"].append(rec)
        elif side == "fx_gap":
            notes["fx_gap"].append(rec)
        elif side == "transfer":
            notes["transfers"].append(rec)

    # ---- dimension rollups (summed over expense+income sides only;
    #      fx-gap rows cannot be summed at all, transfers are not spend)
    keys = ("bucket", "group", "period")
    dims: dict[str, dict[str, dict]] = {k: {} for k in keys}
    for rec in enriched:
        if rec["side"] in ("fx_gap", "transfer"):
            continue
        for k in keys:
            lbl = rec[k] if k != "period" else rec["period"]
            if k == "period" and not lbl:
                lbl = "(no date)"
            entry = dims[k].get(lbl)
            if entry is None:
                entry = dims[k][lbl] = _blank_entry(lbl)
            _add(entry, rec)

    metric = primary_metric(scope)
    by_bucket = sorted(dims["bucket"].values(),
                       key=lambda e: (-e[metric], e["label"]))
    by_group = sorted(dims["group"].values(),
                      key=lambda e: (-e[metric], e["label"]))
    by_period = sorted(dims["period"].values(), key=lambda e: e["label"])
    for d in (by_bucket, by_group, by_period):
        for e in d:
            for f in ("expenses", "income", "net"):
                e[f] = round(e[f], 2)

    exp_rows = [r for r in enriched if r["side"] == "expense"]
    inc_rows = [r for r in enriched if r["side"] == "income"]
    totals = {
        "expenses": round(sum((r.get("amount_ils") or 0.0) for r in exp_rows) * -1, 2),
        "income": round(sum((r.get("amount_ils") or 0.0) for r in inc_rows), 2),
        "count": len(enriched),
        "expense_rows": len(exp_rows),
        "income_rows": len(inc_rows),
    }
    totals["net"] = round(totals["income"] - totals["expenses"], 2)
    totals["transfers_ils"] = round(
        sum(abs(r.get("amount_ils") or 0.0) for r in notes["transfers"]), 2)

    # A separate By-bucket sheet only pays for itself once the user actually
    # customized the map; identity buckets duplicate the groups otherwise.
    bucket_differs = any(
        r["bucket"] != r["group"] for r in enriched if r["side"] != "fx_gap"
    ) or any(e["label"] != g_e["label"]
             for e, g_e in zip(by_bucket, by_group))
    effective_dim = chart_dim if (chart_dim == "group" or bucket_differs) else "group"
    monthly_matrix = build_monthly_matrix(enriched, monthly_matrix_range) if monthly_matrix_range else None

    return {
        "label": label,
        "scope": scope,
        "split_by": split_by,
        "date_field": date_field,
        "chart_dim_requested": chart_dim,
        "chart_dim": effective_dim,
        "metric": metric,
        "top_n": max(1, int(top_n)),
        "rows": enriched,
        "by_bucket": by_bucket,
        "by_group": by_group,
        "by_period": by_period,
        "bucket_differs": bucket_differs,
        "monthly_matrix": monthly_matrix,
        "totals": totals,
        "notes": notes,
    }


# ---------------------------------------------------------------- comparison

def _is_pure_period(spec: dict) -> bool:
    """A custom topic that is nothing but a date range - i.e. a time-period
    slice comparable with its siblings."""
    p = spec.get("params") or {}
    if not (p.get("date1") and p.get("date2")):
        return False
    return not any(p.get(k) for k in _CONTENT_FILTER_KEYS)


def detect_similarity(specs: list[dict]) -> tuple[bool, str]:
    """Whether a multi-topic suite is coherent enough to be worth comparing:
    trips together, same-kind suites, or pure date-range slices."""
    if len(specs) < 2:
        return False, "single topic"
    kinds = [s.get("kind") or "?" for s in specs]
    if all(k in TRIP_KINDS for k in kinds):
        return True, "all topics are trips"
    if len(set(kinds)) == 1:
        kind = kinds[0]
        if kind == "custom":
            if all(_is_pure_period(s) for s in specs):
                return True, "all topics are plain date-range slices"
            return False, "same kind (custom) but not all are pure date-range slices"
        return True, f"all topics share kind {kind!r}"
    return False, "mixed topic kinds (" + ", ".join(sorted(set(kinds))) + ")"


def build_comparisons(reports: list[dict], dims: list[str] | None = None) -> dict:
    """Side-by-side matrices across reports: rows = dimension labels (union),
    columns = topics. Values follow each report's primary metric."""
    dims = dims or ["bucket", "period"]
    names = [r["label"] for r in reports]
    out: dict = {"topics": names}
    for dim in dims:
        key = f"by_{dim}"
        slots: dict[str, dict] = {}
        for rep in reports:
            metric = rep["metric"]
            for e in rep[key]:
                slot = slots.setdefault(
                    e["label"], {"label": e["label"], "values": {}, "total": 0.0})
                v = float(e[metric])
                slot["values"][rep["label"]] = slot["values"].get(rep["label"], 0.0) + v
                slot["total"] += v
        matrix = list(slots.values())
        if dim == "period":
            matrix.sort(key=lambda s: s["label"])
        else:
            matrix.sort(key=lambda s: (-s["total"], s["label"]))
        for s in matrix:
            s["total"] = round(s["total"], 2)
            s["values"] = {k: round(v, 2) for k, v in s["values"].items()}
        grand = {
            name: round(sum(s["values"].get(name, 0.0) for s in matrix), 2)
            for name in names
        }
        out[dim] = {"matrix": matrix, "grand_totals": grand}
    return out


# -------------------------------------------------------------------- layout

def planned_sheet_names(report: dict, comparison: dict | None = None) -> list[str]:
    """The exact sheet order the workbook will get - shared by --plan output
    and the writer so the two can never drift apart."""
    sheets = ["Overview"]
    if report["bucket_differs"]:
        sheets += ["By bucket", "By group"]
    else:  # identity buckets would just duplicate the groups
        sheets.append("By group")
    sheets.append("By period")
    if report.get("monthly_matrix"):
        sheets.append("Monthly comparison")
    if report.get("trip_comparison"):
        sheets += ["Trip summary", "Trip monthly costs"]
    sheets.append("Details")
    sheets.append("Reconciliation")
    if comparison:
        for dim in comparison:
            if dim == "topics":
                continue
            sheets.append(f"Compare {dim}")
    return sheets


def fmt_ils(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "-"
    text = f"{abs(value):,.2f}"
    if value < 0:
        text = "-" + text
    elif signed and value > 0:
        text = "+" + text
    return text + " ₪"

"""finance-topic (Skill 2) core logic.

Loads the finance-unify ledger (read-only, never modified here), loads/saves
the JSON topic "spec" that tracks a topic's parameters and decisions, and
implements the four recipe kinds: trip_abroad, trip_local, custom and
exclude_topics.

Spec files and review CSVs live under <project>/data/topics/ and are the only
things this module writes. The spec uses JSON (not YAML) on purpose: it is
managed entirely through the `topic.py` CLI (propose/review), a user is never
expected to hand-edit it, and JSON via the standard library avoids adding a
PyYAML dependency plus sidesteps the Hebrew-quoting edge cases YAML has (e.g.
`מט"ח`). `topic_signals.yaml`, by contrast, is meant to be occasionally read
or tuned by a curious user, so it stays YAML with hardcoded defaults below as
a fallback when PyYAML/the file are unavailable.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re

from . import columns, io_xlsx, schema

# --------------------------------------------------------------- signals

DEFAULT_SIGNALS: dict = {
    "pre_purchase_categories": [
        "טיסות", "לינה", "השכרת רכב", "אטרקציות",
        "פנאי, תרבות והעשרה - שונות", "הצגות, הופעות וקולנוע",
    ],
    # Subset of pre_purchase_categories eligible for the extended lookback
    # window (see extended_lookback_days) - long-lead bookings like flights
    # and show/attraction tickets, but not hotels/car rentals which are
    # usually booked closer to the trip.
    "extended_lookback_categories": [
        "טיסות", "אטרקציות", "פנאי, תרבות והעשרה - שונות", "הצגות, הופעות וקולנוע",
    ],
    "cash_withdrawal_categories": ["העברת מזומן", "העברת מט\"ח"],
    "cash_withdrawal_keywords": ["כספומט", "ATM", "סניפומט", "משיכת מזומן", "הפועלים"],
    "tail_categories": ["כבישי אגרה", "השכרת רכב"],
    "ambiguous_abroad_keywords": ["Amazon", "AMZ*", "AliExpress", "eBay", "Alibaba", "iHerb"],
    "ambiguous_local_keywords": ["TELEPHARMA", "פארם אונליין"],
    "never_trip_keywords": ["NETFLIX", "PATREON"],
}

_SIGNALS_DATA = os.path.join(os.path.dirname(__file__), "data", "topic_signals.yaml")


def load_signals(path: str | None = None) -> dict:
    """Load topic_signals.yaml, falling back to DEFAULT_SIGNALS for any key
    that is missing, or entirely if PyYAML/the file is unavailable."""
    data = {k: list(v) for k, v in DEFAULT_SIGNALS.items()}
    path = path or _SIGNALS_DATA
    if not path or not os.path.exists(path):
        return data
    try:
        import yaml  # type: ignore
    except ImportError:
        return data
    with open(path, "r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    for key, value in loaded.items():
        if value:
            data[key] = value
    return data


# ----------------------------------------------------------------- ledger

_NUMERIC_COLS = ("amount_ils", "amount_orig", "ils_rate", "date_serial")


def _num(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def load_ledger(path: str) -> list[dict]:
    """Read the ledger CSV produced by finance-unify. Read-only: this module
    never opens the ledger file for writing."""
    import csv

    if not os.path.exists(path):
        raise FileNotFoundError(path)
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for raw in csv.DictReader(fh):
            r = dict(raw)
            r["flags"] = [f for f in (raw.get("flags") or "").split(";") if f]
            for col in _NUMERIC_COLS:
                r[col] = _num(raw.get(col))
            r["seq"] = int(raw["seq"]) if (raw.get("seq") or "").isdigit() else None
            r["op_seq"] = int(raw["op_seq"]) if (raw.get("op_seq") or "").isdigit() else None
            rows.append(r)
    return rows


def to_output_row(r: dict) -> dict:
    out = {k: r.get(k, "") for k in schema.OUTPUT_COLUMNS}
    out["flags"] = ";".join(r.get("flags") or [])
    return out


# --------------------------------------------------------------- matching

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _row_text(r: dict) -> str:
    return _norm(f"{r.get('description', '')} {r.get('detail', '')}")


def _dest_text(r: dict) -> str:
    """Like _row_text but also includes merchant_country, for matching
    destination/region hints against card charges regardless of category."""
    return _norm(f"{r.get('description', '')} {r.get('detail', '')} {r.get('merchant_country', '')}")


def _dominant_foreign_currency(rows: list[dict], d1: "_dt.date", d2: "_dt.date", date_field: str, scope: str) -> str | None:
    """Most common non-ILS currency_orig among credit-card rows during the
    trip's main window - used as "the trip's currency" to catch pre-trip
    charges in the same currency regardless of their category."""
    import collections

    counts: collections.Counter = collections.Counter()
    for r in rows:
        if not _scope_ok(r, scope):
            continue
        d = _date(r.get(date_field))
        if d is None or not (d1 <= d <= d2):
            continue
        if r.get("source_type") != "credit-card":
            continue
        cur = (r.get("currency_orig") or "").strip()
        if cur and cur != "ILS":
            counts[cur] += 1
    return counts.most_common(1)[0][0] if counts else None


def _kw_match(text_norm: str, keywords: list[str]) -> str | None:
    for kw in keywords:
        needle = _norm(kw)
        if needle and needle in text_norm:
            return kw
    return None


_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")


def is_foreign_row(r: dict) -> bool:
    """A credit-card charge abroad: foreign transaction currency, or a
    merchant address that isn't an Israeli one."""
    cur = (r.get("currency_orig") or "").strip()
    if cur and cur != "ILS":
        return True
    mc = (r.get("merchant_country") or "").strip()
    if not mc:
        return False
    if mc == "IL" or mc.endswith(", IL"):
        return False
    if _HEBREW_RE.search(mc):
        return False  # Hebrew address text -> an Israeli street/city
    return True


def is_cash_withdrawal(r: dict, signals: dict) -> tuple[bool, str]:
    cat = (r.get("category") or "").strip()
    if cat in signals["cash_withdrawal_categories"]:
        return True, f"cash/FX withdrawal category ({cat})"
    kw = _kw_match(_row_text(r), signals["cash_withdrawal_keywords"])
    if kw:
        return True, f"cash withdrawal keyword match ({kw!r} in description)"
    return False, ""


def _date(value) -> _dt.date | None:
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


_NON_EXPENSE_CATEGORIES = set(columns.INTERNAL_TRANSFER_CATEGORIES) | {columns.CC_CHARGE_CATEGORY}


def _scope_ok(r: dict, scope: str) -> bool:
    if scope == "all" or not scope:
        return True
    amt = r.get("amount_ils")
    if amt is None:
        return False
    if scope == "income":
        return amt > 0
    if scope == "expenses":
        return amt < 0 and (r.get("category") or "") not in _NON_EXPENSE_CATEGORIES
    return True


# ------------------------------------------------------------------ recipes
# Each recipe returns (candidates, warnings):
#   candidates: {id: (status, reason)}, status in {"confident", "pending"}
#   warnings:   list[str] surfaced to the console (non-fatal notices)

KINDS = ("trip_abroad", "trip_local", "custom", "exclude_topics")


def default_params_for_kind(kind: str) -> dict:
    base = {"date_field": "date_iso", "scope": "all"}
    if kind == "trip_abroad":
        base.update({"lookback_days": 45, "extended_lookback_days": 120, "tail_days": 14, "destination": []})
    elif kind == "trip_local":
        base.update({"lookback_days": 45, "extended_lookback_days": 120, "region": []})
    elif kind == "exclude_topics":
        base["scope"] = "expenses"
        base["exclude_topic"] = []
    return base


def _require(params: dict, *keys) -> None:
    missing = [k for k in keys if not params.get(k)]
    if missing:
        raise ValueError(f"missing required parameter(s): {', '.join(missing)}")


def recipe_trip_abroad(rows: list[dict], params: dict, signals: dict):
    _require(params, "date1", "date2")
    d1, d2 = _date(params["date1"]), _date(params["date2"])
    if d1 is None or d2 is None:
        raise ValueError("date1/date2 must be ISO dates (yyyy-mm-dd)")
    lookback = int(params.get("lookback_days") or 45)
    extended_lookback = int(params.get("extended_lookback_days") or 120)
    tail = int(params.get("tail_days") or 14)
    date_field = params.get("date_field") or "date_iso"
    destinations = [_norm(x) for x in (params.get("destination") or [])]
    scope = params.get("scope", "all")

    pre_start = d1 - _dt.timedelta(days=lookback)
    extended_start = d1 - _dt.timedelta(days=max(extended_lookback, lookback))
    cash_start = d1 - _dt.timedelta(days=1)
    tail_end = d2 + _dt.timedelta(days=tail)
    extended_categories = set(signals.get("extended_lookback_categories", []))
    dominant_currency = _dominant_foreign_currency(rows, d1, d2, date_field, scope)

    out: dict = {}
    for r in rows:
        if not _scope_ok(r, scope):
            continue
        
        # Skip transactions matching "never trip" keywords (e.g., Netflix, Patreon)
        if _kw_match(_row_text(r), signals.get("never_trip_keywords", [])):
            continue
            
        d = _date(r.get(date_field))
        if d is None:
            continue
        cat = (r.get("category") or "").strip()
        
        if extended_start <= d < d1: # Look across the whole pre-trip window
            # IMPROVEMENT: If it's a flight, we are confident it's trip-related
            # even without a destination hint or vendor match.
            if cat == "טיסות":
                out[r["id"]] = ("confident", "Flight category (טיסות) found in pre-trip window")
                continue

        text = _row_text(r)

        # Long-lead bookings (flights, shows, attractions): always pending,
        # regardless of a destination-hint match - a hit that far ahead is
        # too easily coincidental (e.g. a different future trip).
        if extended_start <= d < pre_start and cat in extended_categories:
            days_ahead = (d1 - d).days
            out[r["id"]] = ("pending", f"{cat} booked {days_ahead} days ahead of the trip (beyond the normal {lookback}-day window) - please confirm it belongs to this trip")
            continue

        if pre_start <= d < d1 and cat in signals["pre_purchase_categories"]:
            if destinations and any(dest in text for dest in destinations):
                out[r["id"]] = ("confident", f"pre-purchase ({cat}) matching destination hint before the trip")
            else:
                out[r["id"]] = ("pending", f"pre-purchase ({cat}) before the trip - no destination hint to confirm it belongs to this trip")
            continue

        if cash_start <= d <= d2:
            ok, reason = is_cash_withdrawal(r, signals)
            if ok:
                out[r["id"]] = ("confident", reason + " during the trip window")
                continue

        # Regardless of category: a card charge before the trip that shares
        # the trip's currency, or whose merchant text matches a destination
        # hint, is worth a look even if it isn't a "typical" pre-purchase
        # category (e.g. travel clothing/gear bought from a US store).
        if extended_start <= d < d1 and r.get("source_type") == "credit-card" and r["id"] not in out:
            cur = (r.get("currency_orig") or "").strip()
            currency_hit = bool(dominant_currency) and cur == dominant_currency
            dest_hit = bool(destinations) and any(dest in _dest_text(r) for dest in destinations)
            if currency_hit or dest_hit:
                why = f"currency ({cur}) matches the trip's" if currency_hit else "description/merchant matches a destination hint"
                out[r["id"]] = ("pending", f"card charge before the trip whose {why}, but category ({cat or 'n/a'}) isn't a typical pre-purchase - please confirm")
                continue

        if d1 <= d <= d2 and r.get("source_type") == "credit-card" and is_foreign_row(r):
            kw = _kw_match(text, signals["ambiguous_abroad_keywords"])
            if kw:
                out[r["id"]] = ("pending", f"foreign-currency charge during the trip, but merchant looks like a generic online retailer ({kw!r}) - please confirm")
            else:
                out[r["id"]] = ("confident", "foreign-currency credit-card charge during the trip")
            continue

        if d2 < d <= tail_end and cat in signals["tail_categories"]:
            if destinations and any(dest in text for dest in destinations):
                out[r["id"]] = ("confident", f"post-trip settlement ({cat}) matching destination hint")
            else:
                out[r["id"]] = ("pending", f"post-trip settlement ({cat}) shortly after the trip - no destination hint to confirm relevance")

    return out, []


def recipe_trip_local(rows: list[dict], params: dict, signals: dict):
    _require(params, "date1", "date2")
    d1, d2 = _date(params["date1"]), _date(params["date2"])
    if d1 is None or d2 is None:
        raise ValueError("date1/date2 must be ISO dates (yyyy-mm-dd)")
    lookback = int(params.get("lookback_days") or 45)
    extended_lookback = int(params.get("extended_lookback_days") or 120)
    date_field = params.get("date_field") or "date_iso"
    regions = [_norm(x) for x in (params.get("region") or [])]
    scope = params.get("scope", "all")

    pre_start = d1 - _dt.timedelta(days=lookback)
    extended_start = d1 - _dt.timedelta(days=max(extended_lookback, lookback))
    extended_categories = set(signals.get("extended_lookback_categories", []))

    out: dict = {}
    for r in rows:
        if not _scope_ok(r, scope):
            continue

        # Skip transactions matching "never trip" keywords
        if _kw_match(_row_text(r), signals.get("never_trip_keywords", [])):
            continue

        d = _date(r.get(date_field))
        if d is None:
            continue
        cat = (r.get("category") or "").strip()
        text = _row_text(r)

        # Long-lead bookings (flights, shows, attractions): always pending,
        # regardless of a region-hint match.
        if extended_start <= d < pre_start and cat in extended_categories:
            days_ahead = (d1 - d).days
            out[r["id"]] = ("pending", f"{cat} booked {days_ahead} days ahead of the trip (beyond the normal {lookback}-day window) - please confirm it belongs to this trip")
            continue

        if pre_start <= d < d1 and cat in signals["pre_purchase_categories"]:
            if regions and any(rg in text for rg in regions):
                out[r["id"]] = ("confident", f"pre-purchase ({cat}) matching destination hint before the trip")
            else:
                out[r["id"]] = ("pending", f"pre-purchase ({cat}) before the trip - no destination hint to confirm it belongs to this trip")
            continue

        # Regardless of category: a card charge before the trip whose
        # merchant text matches a region hint (no currency signal for a
        # domestic trip) is worth a look even outside the usual categories.
        if extended_start <= d < d1 and r.get("source_type") == "credit-card" and regions:
            if any(rg in _dest_text(r) for rg in regions):
                out[r["id"]] = ("pending", f"card charge before the trip matching a destination hint, but category ({cat or 'n/a'}) isn't a typical pre-purchase - please confirm")
                continue

        if d1 <= d <= d2:
            ok, reason = is_cash_withdrawal(r, signals)
            if ok:
                out[r["id"]] = ("confident", reason + " during the trip window")
                continue
            if r.get("source_type") == "credit-card":
                kw = _kw_match(text, signals["ambiguous_local_keywords"])
                if kw:
                    out[r["id"]] = ("pending", f"charge during the trip window, but merchant looks unrelated to travel ({kw!r}) - please confirm")
                else:
                    out[r["id"]] = ("confident", "credit-card charge during the trip window")

    return out, []


def recipe_custom(rows: list[dict], params: dict, signals: dict):
    date_field = params.get("date_field") or "date_iso"
    d1 = _date(params.get("date1")) if params.get("date1") else None
    d2 = _date(params.get("date2")) if params.get("date2") else None
    categories = set(params.get("category") or [])
    exclude_categories = set(params.get("exclude_category") or [])
    keywords = [_norm(k) for k in (params.get("keyword") or [])]
    exclude_keywords = [_norm(k) for k in (params.get("exclude_keyword") or [])]
    flags_wanted = set(params.get("flag") or [])
    source_types = set(params.get("source_type") or [])
    min_amount = params.get("min_amount")
    max_amount = params.get("max_amount")
    scope = params.get("scope", "all")

    if not any([d1, d2, categories, exclude_categories, keywords, exclude_keywords,
                flags_wanted, source_types, min_amount is not None, max_amount is not None]):
        raise ValueError("custom needs at least one filter (date1/date2, category, keyword, flag, source-type, min/max-amount)")

    out: dict = {}
    for r in rows:
        if not _scope_ok(r, scope):
            continue
        if d1 or d2:
            d = _date(r.get(date_field))
            if d is None:
                continue
            if d1 and d < d1:
                continue
            if d2 and d > d2:
                continue
        cat = (r.get("category") or "").strip()
        if categories and cat not in categories:
            continue
        if exclude_categories and cat in exclude_categories:
            continue
        text = _row_text(r)
        if keywords and not any(kw in text for kw in keywords):
            continue
        if exclude_keywords and any(kw in text for kw in exclude_keywords):
            continue
        if flags_wanted and not (flags_wanted & set(r.get("flags") or [])):
            continue
        if source_types and r.get("source_type") not in source_types:
            continue
        amt = r.get("amount_ils")
        if min_amount is not None and (amt is None or amt < float(min_amount)):
            continue
        if max_amount is not None and (amt is None or amt > float(max_amount)):
            continue
        out[r["id"]] = ("confident", "matches custom filter")

    return out, []


def recipe_exclude_topics(rows: list[dict], params: dict, signals: dict, topics_dir: str):
    date_field = params.get("date_field") or "date_iso"
    d1 = _date(params.get("date1")) if params.get("date1") else None
    d2 = _date(params.get("date2")) if params.get("date2") else None
    scope = params.get("scope", "expenses")
    exclude_names = list(params.get("exclude_topic") or [])
    warnings: list[str] = []

    if not exclude_names:
        exclude_names = discover_topic_names(topics_dir, kinds={"trip_abroad", "trip_local"})

    excluded_ids: set[str] = set()
    for name in exclude_names:
        spec = load_spec(topics_dir, name)
        if spec is None:
            warnings.append(f"referenced topic '{name}' not found under {topics_dir}; skipped")
            continue
        excluded_ids |= set(spec.get("ids") or [])
        if spec.get("pending_review"):
            warnings.append(
                f"topic '{name}' still has {len(spec['pending_review'])} pending_review item(s) not yet "
                f"approved/rejected - they were NOT excluded (run `review` on that topic first if they belong to it)"
            )

    out: dict = {}
    for r in rows:
        if not _scope_ok(r, scope):
            continue
        if d1 or d2:
            d = _date(r.get(date_field))
            if d is None:
                continue
            if d1 and d < d1:
                continue
            if d2 and d > d2:
                continue
        if r["id"] in excluded_ids:
            continue
        out[r["id"]] = ("confident", f"not part of any excluded topic ({', '.join(exclude_names) or 'none found'})")

    return out, warnings


def run_recipe(kind: str, rows: list[dict], params: dict, signals: dict, topics_dir: str):
    if kind == "trip_abroad":
        return recipe_trip_abroad(rows, params, signals)
    if kind == "trip_local":
        return recipe_trip_local(rows, params, signals)
    if kind == "custom":
        return recipe_custom(rows, params, signals)
    if kind == "exclude_topics":
        return recipe_exclude_topics(rows, params, signals, topics_dir)
    raise ValueError(f"unknown kind {kind!r}; must be one of {', '.join(KINDS)}")


# --------------------------------------------------------------- spec i/o

def spec_path(topics_dir: str, name: str) -> str:
    return os.path.join(topics_dir, f"{name}.json")


def review_csv_path(topics_dir: str, name: str) -> str:
    return os.path.join(topics_dir, f"{name}.review.csv")


def load_spec(topics_dir: str, name: str) -> dict | None:
    path = spec_path(topics_dir, name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def new_spec(name: str, kind: str, params: dict) -> dict:
    return {
        "topic": name,
        "kind": kind,
        "params": params,
        "ids": [],
        "pending_review": [],
        "excluded_ids": [],
        "reasons": {},
    }


def save_spec(topics_dir: str, name: str, spec: dict) -> None:
    os.makedirs(topics_dir, exist_ok=True)
    with open(spec_path(topics_dir, name), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def discover_topic_names(topics_dir: str, kinds: set[str] | None = None) -> list[str]:
    if not os.path.isdir(topics_dir):
        return []
    names = []
    for fn in sorted(os.listdir(topics_dir)):
        if not fn.endswith(".json"):
            continue
        name = fn[:-5]
        if kinds:
            spec = load_spec(topics_dir, name)
            if spec is None or spec.get("kind") not in kinds:
                continue
        names.append(name)
    return names


# ------------------------------------------------------------- merge/review

def merge_candidates(spec: dict, candidates: dict) -> dict:
    """Add newly found candidates into ids/pending_review. Never touches an
    id that is already decided (in ids, pending_review or excluded_ids) - a
    re-run only adds genuinely new rows, it never re-litigates old decisions."""
    ids = set(spec["ids"])
    pending = set(spec["pending_review"])
    excluded = set(spec["excluded_ids"])
    reasons = dict(spec.get("reasons", {}))
    added_confident = added_pending = 0
    for cid, (status, reason) in candidates.items():
        reasons[cid] = reason
        if cid in ids or cid in pending or cid in excluded:
            continue
        if status == "confident":
            ids.add(cid)
            added_confident += 1
        else:
            pending.add(cid)
            added_pending += 1
    spec["ids"] = sorted(ids)
    spec["pending_review"] = sorted(pending)
    spec["excluded_ids"] = sorted(excluded)
    spec["reasons"] = reasons
    return {
        "candidates": len(candidates),
        "added_confident": added_confident,
        "added_pending": added_pending,
        "total_ids": len(ids),
        "total_pending": len(pending),
    }


def apply_review(spec: dict, include_ids=None, exclude_ids=None, pending_ids=None) -> dict:
    ids = set(spec["ids"])
    pending = set(spec["pending_review"])
    excluded = set(spec["excluded_ids"])
    reasons = dict(spec.get("reasons", {}))
    moved = {"included": 0, "excluded": 0, "pending": 0, "not_found": []}

    for cid in include_ids or []:
        pending.discard(cid)
        excluded.discard(cid)
        if cid not in ids:
            ids.add(cid)
            moved["included"] += 1
        reasons.setdefault(cid, "manually included by user")
    for cid in exclude_ids or []:
        ids.discard(cid)
        pending.discard(cid)
        if cid not in excluded:
            excluded.add(cid)
            moved["excluded"] += 1
        reasons.setdefault(cid, "manually excluded by user")
    for cid in pending_ids or []:
        ids.discard(cid)
        excluded.discard(cid)
        if cid not in pending:
            pending.add(cid)
            moved["pending"] += 1
        reasons.setdefault(cid, "manually reset to pending review")

    spec["ids"] = sorted(ids)
    spec["pending_review"] = sorted(pending)
    spec["excluded_ids"] = sorted(excluded)
    spec["reasons"] = reasons
    return moved


REVIEW_COLUMNS = [
    "status", "id", "seq", "date_iso", "billing_date", "source_name",
    "description", "category", "amount_ils", "currency_orig", "reason",
]


def write_review_csv(path: str, spec: dict, ledger_by_id: dict) -> None:
    rows = []
    for status, id_list in (
        ("pending_review", spec["pending_review"]),
        ("included", spec["ids"]),
        ("excluded", spec["excluded_ids"]),
    ):
        for cid in id_list:
            r = ledger_by_id.get(cid, {})
            rows.append({
                "status": status,
                "id": cid,
                "seq": r.get("seq", ""),
                "date_iso": r.get("date_iso", ""),
                "billing_date": r.get("billing_date", ""),
                "source_name": r.get("source_name", ""),
                "description": r.get("description", ""),
                "category": r.get("category", ""),
                "amount_ils": r.get("amount_ils", ""),
                "currency_orig": r.get("currency_orig", ""),
                "reason": spec.get("reasons", {}).get(cid, ""),
            })
    order = {"pending_review": 0, "included": 1, "excluded": 2}
    rows.sort(key=lambda r: (order.get(r["status"], 9), r["seq"] if isinstance(r["seq"], int) else 0))
    io_xlsx.write_csv(path, REVIEW_COLUMNS, rows)


# ---------------------------------------------------------------- extract

def extract_rows(ledger_rows: list[dict], spec: dict, sort: str = "seq") -> list[dict]:
    ids = set(spec["ids"])
    key = "seq" if sort not in ("seq", "op_seq") else sort
    rows = [r for r in ledger_rows if r["id"] in ids]
    rows.sort(key=lambda r: (r.get(key) if r.get(key) is not None else 0))
    return rows


def period_label(date_value: str, split_by: str) -> str | None:
    d = _date(date_value)
    if d is None:
        return None
    if split_by == "month":
        return f"{d.year:04d}-{d.month:02d}"
    if split_by == "quarter":
        return f"{d.year:04d}-Q{(d.month - 1) // 3 + 1}"
    if split_by == "year":
        return f"{d.year:04d}"
    return None

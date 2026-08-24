"""Shared merchant -> category map (a skill-level file), auto-applied to every
matching row - including ones MyFinanda already put a (possibly wrong or
inconsistent) category on.

File format (UTF-8 CSV, `<skill>/merchant_categories.csv`):

    merchant,category,source
    Ritz Paris,"בתי קפה, מסעדות ופאבים",user
    GALLERIA BORGHESE ARTE,אטרקציות,assumed

- Lines starting with `#` are comments; a header row `merchant,category,source`
  is skipped.
- `source` is `user` (explicitly confirmed) or `assumed` (heuristic) - both
  override MyFinanda's own category, since every entry here has already been
  reviewed by the user or the skill; only `flags` differ (`merchant_category`
  vs `assumed_category`) so overrides stay auditable.
- Matching: case-insensitive, with runs of whitespace collapsed to one space on
  both the merchant key and the record's `description` before comparing.
  A key is tried first as a plain substring; if that doesn't match and the key
  contains regex syntax (e.g. `FLORIST ALMATY.*`), it is also tried as a
  case-insensitive regular expression (`re.search`) against the description.
- Optional amount bounds (columns 4 and 5): `min_ils,max_ils` - inclusive
  bounds on the row's ABSOLUTE ILS amount. Empty bound = unbounded on that
  side (a trailing comma gives `300,` = "300 ILS or more"). A bounded entry
  only matches rows within its range, so the same merchant can map to two
  categories by size (e.g. life-insurance premiums of 300 ILS or more are
  mortgage insurance, smaller ones are health/life insurance). When several
  keys match, **file order decides**: the first matching row
  (top to bottom) wins and no later row is even checked. So a narrower
  exception must be placed *above* the broader rule it should override (e.g.
  `חניון`/`חניונים` -> `חנייה` are near the top so they win over a later,
  broader `פרוגרס` rule that would otherwise also match `פרוגרס חניונים`).
- A row already fixed by hand (flag `manual_category`, from a `corrections.csv`
  entry preserved across an incremental run) is never touched here - explicit
  corrections always win. Per-run `--set` corrections still win too, since
  they are applied after this map, later in the pipeline.
- When this overrides a real (non-blank, non-`ללא סיווג`) category MyFinanda
  had assigned, the row's `explanation` records what the old category was, so
  overrides stay visible in the flag report.

This map is shared across all projects. Per-project one-off fixes live in the
project's corrections.csv; `unify.py` can promote those into this file so they
are reused everywhere (see `--promote-corrections`).
"""

from __future__ import annotations

import csv
import os
import re

from . import columns, schema

HEADER = ["merchant", "category", "source"]


def _normalize_ws(text: str) -> str:
    """Collapse any run of whitespace to a single space and strip the ends."""
    return re.sub(r"\s+", " ", text or "").strip()


def load(path: str | None) -> list[tuple]:
    """Return entries as (key_norm, merchant, category, source, regex,
    min_ils, max_ils), in the same order they appear in the file - the FIRST
    matching entry wins (see `match()`), so file order is significant: put an
    entry that must take precedence over a broader/later one earlier in the
    file. `key_norm` is the merchant text lowercased with whitespace
    collapsed; `regex` is a precompiled case-insensitive pattern for
    `key_norm` when it compiles, else None. `min_ils`/`max_ils` are inclusive
    bounds on the absolute ILS amount (None = unbounded), parsed from the
    optional 4th/5th CSV columns."""
    entries: list[tuple] = []
    if not path or not os.path.exists(path):
        return entries
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.reader(fh):
            if not row:
                continue
            merchant = (row[0] or "").strip()
            if not merchant or merchant.startswith("#"):
                continue
            if merchant == "merchant" and len(row) > 1 and (row[1] or "").strip() == "category":
                continue
            if len(row) < 2:
                continue
            category = (row[1] or "").strip()
            source = ((row[2].strip() if len(row) > 2 else "") or "user").lower()
            if merchant and category:
                key_norm = _normalize_ws(merchant).lower()
                try:
                    pattern = re.compile(key_norm, re.IGNORECASE)
                except re.error:
                    pattern = None
                lo = _parse_bound(row[3] if len(row) > 3 else "")
                hi = _parse_bound(row[4] if len(row) > 4 else "")
                
                # Capture the 6th column as search_field
                search_field = (row[5].strip() if len(row) > 5 else "") or "description"
                
                # Add search_field to the tuple
                entries.append((key_norm, merchant, category, source, pattern, lo, hi, search_field))
    return entries

def _parse_bound(text: str) -> float | None:
    t = (text or "").strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def match(record: dict, entries: list[tuple], amount_ils: float | int | str | None = None):
    """Return (category, source, merchant) for the first matching key in file
    order, or None. Entries with amount bounds match only when `amount_ils`
    is given and its absolute value lies within [min_ils, max_ils]; a bounded
    entry is skipped entirely when no amount is available."""
    try:
        amt = abs(float(amount_ils)) if amount_ils not in (None, "") else None
    except (TypeError, ValueError):
        amt = None
    for key_norm, merchant, category, source, pattern, lo, hi, search_field in entries:
        if not key_norm:
            continue
        if lo is not None or hi is not None:
            if amt is None:
                continue
            if lo is not None and amt < lo:
                continue
            if hi is not None and amt > hi:
                continue

        # Select the specific field requested by the rule
        text = record.get(search_field, "")
        d = _normalize_ws(text).lower()
        if not d:
            continue

        if key_norm in d or (pattern is not None and pattern.search(d)):
            return category, source, merchant
    return None

def covers(record: dict, entries: list[tuple]) -> bool:
    for key_norm, _m, _c, _s, pattern, _lo, _hi, search_field in entries:
        if not key_norm:
            continue
        text = record.get(search_field, "")
        d = _normalize_ws(text).lower()
        if not d:
            continue
        if key_norm in d or (pattern is not None and pattern.search(d)):
            return True
    return False


def apply(records: list[dict], entries: list[tuple]) -> int:
    """Apply the shared map to every matching row, overriding MyFinanda's own
    category where it differs (every map entry has already been reviewed).
    `user` entries flag `merchant_category`; `assumed` entries flag
    `assumed_category`. A row already fixed by hand (`manual_category`) is
    left untouched. Returns the number of rows whose category actually
    changed (a match that already agrees with the existing category doesn't
    count and isn't re-flagged)."""
    if not entries:
        return 0
    applied = 0
    for r in records:
        if "manual_category" in r.get("flags", []):
            continue
        hit = match(r, entries, r.get("amount_ils"))
        if not hit:
            continue
        category, source, merchant = hit
        old_cat = (r.get("category") or "").strip()
        if old_cat == category:
            continue
        r["category"] = category
        if "unmatched_category" in r.get("flags", []):
            r["flags"].remove("unmatched_category")
        schema.add_flag(r, "merchant_category" if source == "user" else "assumed_category")
        if old_cat and old_cat != columns.UNCLASSIFIED:
            r["explanation"] = (
                f"Category overridden by merchant map entry '{merchant}': "
                f"MyFinanda had '{old_cat}'."
            )
        applied += 1
    return applied


def append(path: str, rows: list[tuple[str, str, str]]) -> int:
    """Insert new (merchant, category, source) rows into the shared map right
    after the header - ahead of every pre-existing entry - skipping exact-
    duplicate merchant keys already present. Since the first matching row in
    the file wins (see `match()`), a freshly promoted correction must be
    placed ahead of any pre-existing, potentially broader rule; appending it
    at the end would let an earlier rule silently shadow it. Returns the
    number of rows written."""
    if not rows:
        return 0
    existing = {e[1] for e in load(path)}  # merchant (original case)
    to_add: list[list[str]] = []
    seen = set()
    for merchant, category, source in rows:
        if merchant in existing or merchant in seen:
            continue
        seen.add(merchant)
        to_add.append([merchant, category, source or "user"])
    if not to_add:
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    all_rows: list[list[str]] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            all_rows = list(csv.reader(fh))

    header_idx = next(
        (i for i, row in enumerate(all_rows)
         if row and row[0].strip() == "merchant" and len(row) > 1 and (row[1] or "").strip() == "category"),
        None,
    )
    if header_idx is None:
        all_rows = [HEADER] + all_rows
        insert_at = 1
    else:
        insert_at = header_idx + 1
    all_rows[insert_at:insert_at] = to_add

    with open(path, "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(all_rows)
    return len(to_add)

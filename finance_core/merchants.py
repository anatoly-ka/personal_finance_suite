"""Shared merchant -> category map (a skill-level file), auto-applied to rows
that MyFinanda left unclassified.

File format (UTF-8 CSV, `<skill>/merchant_categories.csv`):

    merchant,category,source
    Ritz Paris,"בתי קפה, מסעדות ופאבים",user
    GALLERIA BORGHESE ARTE,אטרקציות,assumed

- Lines starting with `#` are comments; a header row `merchant,category,source`
  is skipped.
- `source` is `user` (explicitly confirmed) or `assumed` (heuristic).
- Matching: case-insensitive, with runs of whitespace collapsed to one space on
  both the merchant key and the record's `description` before comparing.
  A key is tried first as a plain substring; if that doesn't match and the key
  contains regex syntax (e.g. `FLORIST ALMATY.*`), it is also tried as a
  case-insensitive regular expression (`re.search`) against the description.
  When several keys match, the longest key wins (most specific).

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


def load(path: str | None) -> list[tuple[str, str, str, str, "re.Pattern | None"]]:
    """Return entries as (key_norm, merchant, category, source, regex), sorted so
    the longest (most specific) merchant key is tried first. `key_norm` is the
    merchant text lowercased with whitespace collapsed; `regex` is a precompiled
    case-insensitive pattern for `key_norm` when it compiles, else None."""
    entries: list[tuple[str, str, str, str, "re.Pattern | None"]] = []
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
                entries.append((key_norm, merchant, category, source, pattern))
    entries.sort(key=lambda e: len(e[0]), reverse=True)
    return entries


def match(description: str, entries: list[tuple[str, str, str, str, "re.Pattern | None"]]):
    """Return (category, source, merchant) for the longest matching key, or None."""
    d = _normalize_ws(description).lower()
    if not d:
        return None
    for key_norm, merchant, category, source, pattern in entries:
        if not key_norm:
            continue
        if key_norm in d or (pattern is not None and pattern.search(d)):
            return category, source, merchant
    return None


def covers(description: str, entries: list[tuple[str, str, str, str, "re.Pattern | None"]]) -> bool:
    return match(description, entries) is not None


def apply(records: list[dict], entries: list[tuple[str, str, str, str, "re.Pattern | None"]]) -> int:
    """Fill still-unclassified rows from the shared map. `user` entries flag
    `merchant_category`; `assumed` entries flag `assumed_category`."""
    if not entries:
        return 0
    applied = 0
    for r in records:
        cat = (r.get("category") or "").strip()
        if cat and cat != columns.UNCLASSIFIED:
            continue
        hit = match(r.get("description", ""), entries)
        if not hit:
            continue
        category, source, _merchant = hit
        r["category"] = category
        if "unmatched_category" in r.get("flags", []):
            r["flags"].remove("unmatched_category")
        schema.add_flag(r, "merchant_category" if source == "user" else "assumed_category")
        applied += 1
    return applied


def append(path: str, rows: list[tuple[str, str, str]]) -> int:
    """Append new (merchant, category, source) rows to the shared map, skipping
    exact-duplicate merchant keys already present. Returns the number written."""
    if not rows:
        return 0
    existing = {e[1] for e in load(path)}  # merchant (original case)
    to_add = []
    seen = set()
    for merchant, category, source in rows:
        if merchant in existing or merchant in seen:
            continue
        seen.add(merchant)
        to_add.append((merchant, category, source or "user"))
    if not to_add:
        return 0
    file_exists = os.path.exists(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # Plain utf-8 on append so we never inject a BOM mid-file.
    with open(path, "a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if not file_exists:
            writer.writerow(HEADER)
        for merchant, category, source in to_add:
            writer.writerow([merchant, category, source])
    return len(to_add)

"""Read, apply, and append the per-dataset corrections file.

The corrections file is a UTF-8 CSV with columns: id, field, value.
Later rows override earlier ones for the same (id, field).
"""

from __future__ import annotations

import csv
import os

from . import columns, schema

HEADER = ["id", "field", "value"]


def parse_set_arg(text: str) -> tuple[str, str, str]:
    """Parse 'id:field=value' into (id, field, value)."""
    if ":" not in text or "=" not in text:
        raise ValueError(f"Invalid --set '{text}', expected id:field=value")
    id_part, rest = text.split(":", 1)
    field, value = rest.split("=", 1)
    return id_part.strip(), field.strip(), value.strip()


def load(path: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    if not path or not os.path.exists(path):
        return result
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            rid = (row.get("id") or "").strip()
            field = (row.get("field") or "").strip()
            if not rid or not field:
                continue
            result.setdefault(rid, {})[field] = (row.get("value") or "").strip()
    return result


def append(path: str, entries: list[tuple[str, str, str]]) -> None:
    if not entries:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        if not exists:
            writer.writerow(HEADER)
        for rid, field, value in entries:
            writer.writerow([rid, field, value])


def apply(records: list[dict], corrections: dict[str, dict[str, str]]) -> int:
    count = 0
    by_id = {r["id"]: r for r in records}
    for rid, fields in corrections.items():
        rec = by_id.get(rid)
        if rec is None:
            continue
        for field, value in fields.items():
            rec[field] = value
            count += 1
            if field == "category" and value and value != columns.UNCLASSIFIED:
                flags = rec.get("flags", [])
                if "unmatched_category" in flags:
                    flags.remove("unmatched_category")
                if "assumed_category" in flags:
                    flags.remove("assumed_category")
                schema.add_flag(rec, "manual_category")
    return count

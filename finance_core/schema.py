"""Canonical ledger schema plus row construction, id/seq logic, and flags."""

from __future__ import annotations

import re

from . import columns, io_xlsx
from .registry import last4_of

OUTPUT_COLUMNS = [
    "seq",
    "op_seq",
    "id",
    "source_type",
    "source_name",
    "date_iso",
    "time",
    "date_serial",
    "billing_date",
    "amount_ils",
    "amount_orig",
    "currency_orig",
    "ils_rate",
    "description",
    "detail",
    "merchant_country",
    "category",
    "installment_no",
    "installment_total",
    "reference",
    "link_group",
    "flags",
]

# Internal-only keys kept on each record for processing but not exported:
#   billing_month, billing_currency, inst_id, billing_serial, orig_row, source_key
# Note: category_group is intentionally NOT stored; it is reconstructed from
# `category` in Skill 3 via category_map.yaml.
_TYPE_PREFIX = {"bank": "B", "fx": "F", "credit-card": "C"}


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def parse_time_fraction(time_raw: str) -> float:
    """'16:00' or 'HH:MM[:SS]' -> day fraction; numeric fraction passes through."""
    if not time_raw:
        return 0.0
    s = str(time_raw).strip()
    if ":" in s:
        parts = s.split(":")
        try:
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            sec = int(parts[2]) if len(parts) > 2 else 0
        except ValueError:
            return 0.0
        return (h * 3600 + m * 60 + sec) / 86400.0
    try:
        f = float(s)
    except ValueError:
        return 0.0
    if 0.0 <= f < 1.0:
        return f
    return 0.0


def source_code(source_type: str, source_name: str) -> str:
    prefix = _TYPE_PREFIX.get(source_type, "X")
    digits = last4_of(source_name)
    if digits:
        return f"{prefix}{digits}"
    slug = re.sub(r"\W+", "", source_name)[:6] or "SRC"
    return f"{prefix}{slug}"


def build_record(
    translated: dict[str, str],
    source_type: str,
    source_name: str,
    currency: str,
    orig_row: int,
    code: str,
) -> dict:
    date_serial_int = _to_float(translated.get("date_serial_raw"))
    time_frac = parse_time_fraction(translated.get("time_raw", ""))
    date_serial = None
    if date_serial_int is not None:
        date_serial = int(date_serial_int) + time_frac

    billing_serial = _to_float(translated.get("billing_serial_raw"))
    amount = _to_float(translated.get("amount"))

    detail = " | ".join(
        p for p in (translated.get("detail1", ""), translated.get("detail2", "")) if p
    )

    rec: dict = {
        "id": f"{code}-{orig_row:06d}",
        "source_type": source_type,
        "source_name": source_name,
        "date_iso": io_xlsx.serial_to_iso(date_serial_int),
        "time": translated.get("time_raw", ""),
        "date_serial": date_serial,
        "billing_date": io_xlsx.serial_to_iso(billing_serial),
        "description": translated.get("description", ""),
        "detail": detail,
        "merchant_country": translated.get("merchant_country", ""),
        "category": translated.get("category", ""),
        "installment_no": translated.get("installment_no", ""),
        "installment_total": translated.get("installment_total", ""),
        "reference": translated.get("reference", ""),
        "link_group": "",
        "flags": [],
        # internal
        "billing_month": translated.get("billing_month", ""),
        "billing_currency": translated.get("billing_currency", ""),
        "inst_id": translated.get("inst_id", ""),
        "billing_serial": billing_serial,
        "orig_row": orig_row,
        "source_key": code,
    }

    # Amounts / currency / rate per source type.
    if source_type == "credit-card":
        rec["amount_ils"] = amount  # 'סכום חיוב' is already ILS
        amt_orig = _to_float(translated.get("amount_orig"))
        cur = (translated.get("currency_orig") or "").strip() or "ILS"
        if amt_orig is not None and amount is not None:
            # Align sign of the transaction amount with the (signed) charge.
            amt_orig = -abs(amt_orig) if amount < 0 else abs(amt_orig)
        rec["amount_orig"] = amt_orig if amt_orig is not None else amount
        rec["currency_orig"] = cur
        if cur != "ILS" and amt_orig not in (None, 0) and amount is not None:
            rec["ils_rate"] = round(abs(amount) / abs(amt_orig), 6)
        else:
            rec["ils_rate"] = 1.0
    elif source_type == "fx":
        rec["amount_orig"] = amount
        rec["currency_orig"] = currency
        rec["amount_ils"] = None  # filled by fx.apply_rates
        rec["ils_rate"] = None
    else:  # bank (ILS)
        rec["amount_ils"] = amount
        rec["amount_orig"] = amount
        rec["currency_orig"] = currency or "ILS"
        rec["ils_rate"] = 1.0

    return rec


def add_flag(rec: dict, flag: str) -> None:
    flags = rec.setdefault("flags", [])
    if flag not in flags:
        flags.append(flag)


def flag_basic(rec: dict) -> None:
    """Flags that depend only on a single record."""
    if (rec.get("category") or "").strip() == columns.UNCLASSIFIED:
        add_flag(rec, "unmatched_category")
    total = _to_float(rec.get("installment_total"))
    if total is not None and total > 1:
        add_flag(rec, "installment")
    if rec["source_type"] == "credit-card":
        amt = rec.get("amount_ils")
        if amt is not None and amt > 0:
            add_flag(rec, "refund")


def to_output(rec: dict) -> dict:
    out = {k: rec.get(k, "") for k in OUTPUT_COLUMNS}
    out["flags"] = ";".join(rec.get("flags", []))
    return out

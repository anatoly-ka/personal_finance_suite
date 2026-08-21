"""Build a registry of accounts and cards from the workbook's summary sheets.

Summary sheets provide metadata that transaction sheets lack:
  - 'סיכום חשבונות עוש שיקליים'  -> ILS account names (currency = ILS)
  - 'סיכום חשבונות עוש מטח'      -> FX account names + currency (column 'מטבע')
  - 'סיכום כרטיסי אשראי'         -> card last-4, issuer, main account
The 'קישור' column in each summary equals the corresponding sheet name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import io_xlsx


@dataclass
class Registry:
    # sheet name -> currency (ILS/USD/EUR/...)
    account_currency: dict[str, str] = field(default_factory=dict)
    # card last-4 -> {"issuer": str, "main_account": str}
    cards: dict[str, dict[str, str]] = field(default_factory=dict)
    # sheet names that are FX (non-ILS) accounts
    fx_accounts: set[str] = field(default_factory=set)


_SUMMARY_ILS = "סיכום חשבונות עוש שיקליים"
_SUMMARY_FX = "סיכום חשבונות עוש מטח"
_SUMMARY_CARDS = "סיכום כרטיסי אשראי"


def build(sheets: list[io_xlsx.Sheet]) -> Registry:
    reg = Registry()
    by_name = {s.name: s for s in sheets}

    ils = by_name.get(_SUMMARY_ILS)
    if ils:
        for row in ils.rows:
            name = (row.get("קישור") or "").strip()
            if name:
                reg.account_currency[name] = "ILS"

    fx = by_name.get(_SUMMARY_FX)
    if fx:
        for row in fx.rows:
            name = (row.get("קישור") or "").strip()
            cur = (row.get("מטבע") or "").strip() or "USD"
            if name:
                reg.account_currency[name] = cur
                reg.fx_accounts.add(name)

    cards = by_name.get(_SUMMARY_CARDS)
    if cards:
        for row in cards.rows:
            last4 = (row.get("מספר הכרטיס") or "").strip()
            if not last4:
                continue
            reg.cards[last4] = {
                "issuer": (row.get("מוסד פיננסי") or "").strip(),
                "main_account": (row.get("חשבון ראשי") or "").strip(),
                "link": (row.get("קישור") or "").strip(),
            }
    return reg


def last4_of(name: str) -> str:
    """Return the first digit group of length >= 3 in a sheet name."""
    m = re.search(r"\d{3,}", name)
    return m.group(0) if m else ""

"""Classify each sheet by its header signature (not by its Hebrew name).

Returns one of: 'bank', 'fx', 'credit-card', or 'skip'. FX vs bank is decided
by the account currency from the registry; the cash wallet and any empty sheet
are skipped.
"""

from __future__ import annotations

from . import columns, io_xlsx
from .registry import Registry

# Sheets that look like transaction sheets but must be skipped.
_CASH_WALLET_NAMES = {"הארנק שלי"}


def classify(sheet: io_xlsx.Sheet, reg: Registry) -> str:
    if not sheet.rows or not sheet.headers:
        return "skip"

    name = sheet.name.strip()
    if name in _CASH_WALLET_NAMES:
        return "skip"

    header_set = {h.strip() for h in sheet.headers}

    if columns.CC_SIGNATURE.issubset(header_set):
        return "credit-card"

    if columns.BANK_SIGNATURE.issubset(header_set):
        currency = reg.account_currency.get(name, "ILS")
        if name in reg.fx_accounts or currency != "ILS":
            return "fx"
        # A bank-like sheet carrying its own currency column is the cash wallet
        # style; if it's not a known account, still treat as bank (ILS).
        return "bank"

    return "skip"

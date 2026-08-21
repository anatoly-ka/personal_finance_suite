"""Derive foreign-currency -> ILS rates from the file itself and apply them to
FX bank-account rows.

Rate observations come from credit-card foreign transactions, which carry both
the transaction amount (foreign) and the billing amount (ILS) on the same row -
i.e. a real market conversion performed by the issuer. Observations are trimmed
of outliers, then for an FX bank row we use the *median* rate of same-currency
observations within +/-1 month (robust to noisy single points):
  - an observation on the exact same day exists -> use it, no flag
  - only nearby observations (within +/-1 mo)    -> use median, flag 'assumed_fx_rate'
  - nothing within +/-1 month                    -> leave amount_ils blank, flag 'no_fx_rate'
"""

from __future__ import annotations

import statistics

from . import schema

_ONE_MONTH_DAYS = 31


def collect_observations(records: list[dict]) -> dict[str, list[tuple[float, float]]]:
    """currency -> sorted list of (date_serial, rate), outliers trimmed."""
    raw: dict[str, list[tuple[float, float]]] = {}
    for rec in records:
        if rec["source_type"] != "credit-card":
            continue
        cur = (rec.get("currency_orig") or "ILS").strip()
        if cur == "ILS":
            continue
        rate = rec.get("ils_rate")
        ds = rec.get("date_serial")
        if rate and ds is not None and rate > 0:
            raw.setdefault(cur, []).append((float(ds), float(rate)))

    obs: dict[str, list[tuple[float, float]]] = {}
    for cur, points in raw.items():
        rates = [r for _, r in points]
        med = statistics.median(rates)
        lo, hi = med / 3.0, med * 3.0
        kept = [(d, r) for (d, r) in points if lo <= r <= hi]
        kept.sort()
        obs[cur] = kept
    return obs


def _window_rate(points: list[tuple[float, float]], target: float) -> tuple[float, bool] | None:
    """Return (median_rate, exact_same_day) for observations within +/-1 month."""
    in_window = [(d, r) for (d, r) in points if abs(d - target) <= _ONE_MONTH_DAYS]
    if not in_window:
        return None
    rates = [r for _, r in in_window]
    exact = any(abs(d - target) < 1.0 for d, _ in in_window)
    return statistics.median(rates), exact


def apply_rates(records: list[dict]) -> None:
    obs = collect_observations(records)
    for rec in records:
        if rec["source_type"] != "fx":
            continue
        if rec.get("amount_ils") is not None:
            continue
        cur = (rec.get("currency_orig") or "").strip()
        amount = rec.get("amount_orig")
        ds = rec.get("date_serial")
        points = obs.get(cur, [])
        result = _window_rate(points, float(ds)) if ds is not None else None
        if result is None or amount is None:
            rec["amount_ils"] = None
            rec["ils_rate"] = None
            schema.add_flag(rec, "no_fx_rate")
            continue
        rate, exact = result
        rec["ils_rate"] = round(rate, 6)
        rec["amount_ils"] = round(amount * rate, 2)
        if not exact:
            schema.add_flag(rec, "assumed_fx_rate")

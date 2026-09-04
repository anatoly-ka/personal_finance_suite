"""Bind `refund`-flagged credit-card rows to the expense they reverse.

For every row flagged `refund` (a positive-amount credit-card row - see
schema.flag_basic), look for a prior expense (negative-amount credit-card row)
from the same vendor and link them:

- **Obvious** binding (same vendor, exact same amount in the *original*
  transaction currency - so a foreign-currency amount that matches exactly
  even though the ILS amount differs due to FX movement still counts): the
  `refund` flag is kept. A same-batch, same-category, exact-opposite-ILS-amount
  fallback handles pairs where the vendor text itself differs entirely (e.g. a
  card fee immediately waived).
- **Non-obvious** binding (vendor matches but the amount doesn't line up
  exactly - a partial refund, a retained fee, a cross-currency adjustment):
  the flag becomes `assumed_refund`.
- **No match found** at all within the lookback window: the flag becomes
  `unmatched_refund` (no `explanation`/`reference` change - there is nothing
  to point at).

Every bound pair gets a bidirectional link and a one-line `explanation` each,
kept deliberately different on each side so neither repeats the other:
- The **refund** row's `explanation` is the specific note about the match
  (the FX difference, the partial-refund percentage, the batch/fee-waiver
  note, ...); when nothing more specific applies (an exact obvious match),
  it falls back to `Refund for <expense id>`.
- The **expense** row's `explanation` is always just `Refunded by <refund
  id>` - the detail already lives on the refund row, so it isn't duplicated.
- Each row's `reference` is set to the other's `id`. If a row already carries
  a `reference` (e.g. from a manual correction, or a prior run) it is not
  overwritten - the new id is appended with `;`.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections import Counter, defaultdict

from .schema import add_flag

_LOOKBACK_DAYS = 400  # how far back an expense can be to still match a refund


def _vnorm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _vtokens(text: str) -> tuple[str, ...]:
    norm = _vnorm(text)
    return tuple(norm.split(" ")) if norm else tuple()


def _vkey(text: str, n: int = 2) -> tuple[str, ...]:
    toks = _vtokens(text)
    return toks[: min(n, len(toks))]


def _vendor_match(a: str, b: str) -> bool:
    na, nb = _vnorm(a), _vnorm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ka, kb = _vkey(a), _vkey(b)
    return bool(ka) and ka == kb


def _date(r: dict) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat((r.get("date_iso") or "")[:10])
    except ValueError:
        return None


def _amt_orig(r: dict) -> float:
    v = r.get("amount_orig")
    if v is None:
        v = r.get("amount_ils")
    return v or 0.0


def _cur(r: dict) -> str:
    return (r.get("currency_orig") or "ILS").strip() or "ILS"


def _fx_explanation(ref: dict, expense: dict) -> str:
    r_ils, e_ils = ref.get("amount_ils") or 0.0, expense.get("amount_ils") or 0.0
    diff = abs(r_ils) - abs(e_ils)
    if abs(diff) < 0.02:
        return ""
    cur = _cur(ref)
    amt = abs(ref.get("amount_orig") if ref.get("amount_orig") is not None else r_ils)
    return (
        f"Refund matches the original {cur} amount ({amt:.2f} {cur}) exactly, but the ILS "
        f"amount differs by {diff:+.2f} ILS due to the exchange rate on the settlement date."
    )


def _partial_explanation(ref: dict, expense: dict) -> str:
    r_ils, e_ils = ref.get("amount_ils") or 0.0, expense.get("amount_ils") or 0.0
    rcur, ecur = _cur(ref), _cur(expense)
    if rcur == ecur and _amt_orig(expense):
        r_amt, e_amt = abs(_amt_orig(ref)), abs(_amt_orig(expense))
        pct = (r_amt / e_amt * 100) if e_amt else 0.0
        return (
            f"Approximate/partial refund: {r_amt:.2f} {rcur} of the original {e_amt:.2f} {rcur} "
            f"charge ({pct:.0f}%) - vendor matches but the amount isn't exact; please verify."
        )
    pct = (abs(r_ils) / abs(e_ils) * 100) if e_ils else 0.0
    return (
        f"Approximate/partial refund: {abs(r_ils):.2f} ILS of the original {abs(e_ils):.2f} ILS "
        f"charge ({pct:.0f}%) - vendor matches but the amount/currency differs; please verify."
    )


def _add_reference(rec: dict, other_id: str) -> None:
    """Set `reference` to `other_id`; if it already holds something else
    (manual correction, earlier run, ...), append rather than overwrite."""
    cur = (rec.get("reference") or "").strip()
    if not cur:
        rec["reference"] = other_id
        return
    parts = [p.strip() for p in cur.split(";") if p.strip()]
    if other_id not in parts:
        rec["reference"] = ";".join(parts + [other_id])


def _link(ref: dict, expense: dict, explanation: str) -> None:
    _add_reference(ref, expense["id"])
    _add_reference(expense, ref["id"])
    ref["explanation"] = explanation or f"Refund for {expense['id']}"
    expense["explanation"] = f"Refunded by {ref['id']}"


def bind_refunds(records: list[dict]) -> dict:
    expenses = [
        r for r in records
        if r.get("source_type") == "credit-card" and (r.get("amount_ils") or 0) < 0
    ]
    by_link_group: dict[str, list[dict]] = defaultdict(list)
    for e in expenses:
        lg = e.get("link_group") or ""
        if lg:
            by_link_group[lg].append(e)

    refunds = [
        r for r in records
        if r.get("source_type") == "credit-card"
        and "refund" in (r.get("flags") or [])
        and not (r.get("reference") or "").strip()  # already resolved/corrected -> leave alone
    ]

    def gap_days(ref, e) -> int:
        rd, ed = _date(ref), _date(e)
        return (rd - ed).days if (rd and ed) else 9999

    def same_source(ref, e) -> int:
        return 0 if e.get("source_name") == ref.get("source_name") else 1

    def in_window(ref, e) -> bool:
        rd, ed = _date(ref), _date(e)
        return bool(rd and ed and ed <= rd and (rd - ed).days <= _LOOKBACK_DAYS)

    unresolved = {r["id"]: r for r in refunds}
    stats: Counter = Counter()

    def run_pass(build_pairs, apply_pair, tier_name: str) -> None:
        """One matching tier: score every still-possible (refund, expense) pair,
        then greedily accept from best score down, so an early but weak match
        on one refund can't block a later, better-fitting refund from the
        same expense (or vice versa)."""
        if not unresolved:
            return
        pairs = build_pairs()
        pairs.sort(key=lambda p: p[0])
        for _score, ref_id, exp_id in pairs:
            if ref_id not in unresolved or exp_id in consumed:
                continue
            apply_pair(unresolved[ref_id], consumed_lookup[exp_id])
            consumed.add(exp_id)
            del unresolved[ref_id]
            stats[tier_name] += 1

    consumed: set[str] = set()
    consumed_lookup = {e["id"]: e for e in expenses}

    # Tier 1: same vendor, exact amount in the original currency.
    def build_tier1():
        pairs = []
        for ref in unresolved.values():
            rcur, ramt_orig, ramt_ils = _cur(ref), _amt_orig(ref), ref.get("amount_ils") or 0.0
            for e in expenses:
                if not in_window(ref, e):
                    continue
                if not _vendor_match(e.get("description", ""), ref.get("description", "")):
                    continue
                if _cur(e) != rcur or abs(abs(_amt_orig(e)) - abs(ramt_orig)) >= 0.01:
                    continue
                # Prefer a candidate whose ILS amount also roughly lines up (a small
                # FX-driven gap is expected/fine); a huge ILS mismatch despite an
                # exact original-currency match usually means an unrelated
                # coincidence or a source-data glitch on one of the two rows.
                ils_diff = round(abs(abs(e.get("amount_ils") or 0) - abs(ramt_ils)), 2)
                score = (same_source(ref, e), ils_diff, gap_days(ref, e))
                pairs.append((score, ref["id"], e["id"]))
        return pairs

    def apply_tier1(ref, e):
        _link(ref, e, _fx_explanation(ref, e))

    run_pass(build_tier1, apply_tier1, "obvious")

    # Tier 1b: vendor text doesn't match, but the row is in the same
    # card-statement batch, same category, and the ILS amount exactly
    # offsets - e.g. a fee immediately waived.
    def build_tier1b():
        pairs = []
        for ref in unresolved.values():
            lg = ref.get("link_group") or ""
            if not lg:
                continue
            ramt_ils = ref.get("amount_ils") or 0.0
            for e in by_link_group.get(lg, []):
                if e.get("category") != ref.get("category"):
                    continue
                if abs(abs(e.get("amount_ils") or 0) - abs(ramt_ils)) >= 0.01:
                    continue
                pairs.append(((0, 0, 0), ref["id"], e["id"]))
        return pairs

    def apply_tier1b(ref, e):
        _link(ref, e, "Matched via the same card-statement batch and an exact offsetting "
                       "amount in the same category (vendor text differs) - e.g. a fee "
                       "immediately waived.")

    run_pass(build_tier1b, apply_tier1b, "obvious")

    # Tier 2: same vendor, amount doesn't line up exactly -> best-effort guess.
    def build_tier2():
        pairs = []
        for ref in unresolved.values():
            rcur, ramt_orig, ramt_ils = _cur(ref), _amt_orig(ref), ref.get("amount_ils") or 0.0
            for e in expenses:
                if not in_window(ref, e):
                    continue
                if not _vendor_match(e.get("description", ""), ref.get("description", "")):
                    continue
                amt_diff = (
                    abs(abs(_amt_orig(e)) - abs(ramt_orig)) if _cur(e) == rcur
                    else abs(abs(e.get("amount_ils") or 0) - abs(ramt_ils))
                )
                score = (same_source(ref, e), round(amt_diff, 2), gap_days(ref, e))
                pairs.append((score, ref["id"], e["id"]))
        return pairs

    def apply_tier2(ref, e):
        _link(ref, e, _partial_explanation(ref, e))
        if "refund" in ref["flags"]:
            ref["flags"].remove("refund")
        add_flag(ref, "assumed_refund")

    run_pass(build_tier2, apply_tier2, "assumed")

        # Whatever is left has no candidate at all within the lookback window.
    for ref in unresolved.values():
        if "refund" in ref["flags"]:
            ref["flags"].remove("refund")
        add_flag(ref, "unmatched_refund")
        stats["unmatched"] += 1

    return dict(stats)


# ---------------------------------------------------------- pair annihilation

_REFUND_FLAGS = {"refund", "assumed_refund"}


def annihilate_bound_pairs(rows: list[dict], ledger_rows: list[dict] | None = None) -> tuple[list[dict], list[dict], dict]:
    """Drop selected legs of valid bound refund<->expense pairs.

    Skill 1 binds each matched refund to the expense it reverses via a
    bidirectional `reference` link (the refund leg carries flag `refund` or
    `assumed_refund`). By default, only pairs whose both legs occur in `rows`
    are removed. Passing `ledger_rows` resolves partners against the full
    ledger, allowing a selected expense or refund to be removed even if its
    counterpart is outside the selected period. Nothing is ever removed from
    the ledger itself; this is an analysis-time view only.

    Returns (kept_rows, dropped_rows, stats). The totals in stats describe
    selected rows actually removed; `external_partners` records counterparts
    outside that selection for reconciliation reporting.
    """
    universe = ledger_rows if ledger_rows is not None else rows
    by_id = {r["id"]: r for r in universe if r.get("id")}
    selected_by_id = {r["id"]: r for r in rows if r.get("id")}
    dropped: set[str] = set()
    stats = {
        "pairs": 0,
        "cross_scope_pairs": 0,
        "refunds_ils": 0.0,
        "expenses_ils": 0.0,
        "external_partners": [],
    }

    for r in universe:
        rid = r.get("id")
        if not rid:
            continue
        if r.get("source_type") != "credit-card":
            continue
        if not _REFUND_FLAGS.intersection(r.get("flags") or []):
            continue
        for part in (r.get("reference") or "").split(";"):
            partner = by_id.get(part.strip())
            if partner is None or partner is r or partner["id"] in dropped:
                continue
            exp_ils = partner.get("amount_ils")
            if exp_ils is None or exp_ils >= 0:
                continue  # linked row is not an expense leg
            back = {p.strip() for p in (partner.get("reference") or "").split(";")}
            if rid not in back:
                continue  # reference points elsewhere / one-directional
            selected_legs = [rec for rec in (r, partner) if rec["id"] in selected_by_id]
            if not selected_legs:
                continue
            for rec in selected_legs:
                if rec["id"] in dropped:
                    continue
                dropped.add(rec["id"])
                amount = float(rec.get("amount_ils") or 0.0)
                if amount > 0:
                    stats["refunds_ils"] += amount
                else:
                    stats["expenses_ils"] += amount
            stats["pairs"] += 1
            if len(selected_legs) == 1:
                stats["cross_scope_pairs"] += 1
                stats["external_partners"].append(
                    partner if selected_legs[0] is r else r)
            break

    kept = [r for r in rows if r.get("id") not in dropped]
    out = [r for r in rows if r.get("id") in dropped]
    return kept, out, stats

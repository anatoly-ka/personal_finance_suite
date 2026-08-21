"""Reconciliation passes for the unified ledger.

Two kinds of records represent money that only *moves between your own
accounts* rather than real income/expense, and are collapsed so they are not
double-counted. Only *obviously mirrored* operations are removed:

1. CC monthly-charge replacement
   A bank/FX row whose category (`ענף`) is "העברה לחברת כרטיסי אשראי" is the
   cumulative charge of one credit card for one billing cycle. Matching:
     - Take the bank row's `billing_date` (its value date, תאריך ערך).
     - Identify the card: the bank row's `reference` (אסמכתה) ends with the
       card's last-4, which also appears in the card's sheet name
       (e.g. reference 38117 -> card 8117 -> sheet "לאומי קארד 8117").
     - Among that card's credit-card rows with the *same* `billing_date`
       (תאריך חיוב), sum the signed ILS amounts; if it equals the charge (within
       a small tolerance), it is a match.
   On a match the bank charge row is dropped and those credit-card rows are
   linked via `link_group` (`cc:<charge id>`). Fallbacks when the reference does
   not resolve a card: the same card within a few days of the billing date, or
   (if no card is identified at all) a single card whose group sums to the charge
   on the same date, else within a few days (the bank often posts the settlement
   a day after the card's charge date). No match -> the charge is kept and
   flagged `unmatched_cc`.

   Before matching, a CC-charge row whose ILS amount is a whole multiple of 1000
   (e.g. 4000, 8000) is reclassified as a cash transfer (`העברת מזומן`,
   `assumed_category`): those are manual round advances to the card, not cycle
   settlements, so they are never hunted as CC charges.

2. Mirrored transfer annihilation
   A transfer out of account A (-X) that has a matching transfer into account B
   (+X) around the same date is a self-transfer. Both legs are dropped and
   linked (`xfer:<n>`). A pair is annihilated only when it is unambiguous:
     - both rows are internal-transfer categories (`ענף` in a known set),
     - equal magnitude in ILS,
     - dates within a couple of days,
     - AND either the two rows are in different accounts OR they share a
       reference number.
   Anything without such a mirror is left untouched (no flag) - it may be a
   transfer to an account outside this file.
"""

from __future__ import annotations

import re
from collections import defaultdict

from . import columns, schema

_CC_DATE_TOL_DAYS = 5    # fallback: card charge posted a few days off the value date
_XFER_DATE_TOL_DAYS = 3   # "same date or the next couple of days"


def _amount_tol(charge: float) -> float:
    return max(2.0, 0.005 * abs(charge))


def reconcile(records: list[dict]) -> tuple[list[dict], dict]:
    stats = {
        "cc_replaced": 0,
        "cc_unmatched": 0,
        "transfers_annihilated": 0,
        "round_cc_reclassified": 0,
    }
    dropped: set[int] = set()

    _reclassify_round_cc_transfers(records, stats)
    _replace_cc_charges(records, dropped, stats)
    _annihilate_mirrors(records, dropped, stats)

    kept = [r for i, r in enumerate(records) if i not in dropped]
    return kept, stats


def _is_thousands_multiple(amt: float) -> bool:
    if amt is None or abs(amt) < 1000 - 0.005:
        return False
    rem = abs(amt) % 1000
    return rem < 0.005 or rem > 999.995


def _reclassify_round_cc_transfers(records, stats) -> None:
    """A bank "העברה לחברת כרטיסי אשראי" whose ILS amount is a whole multiple of
    1000 (e.g. 4000, 8000) is a manual round advance/cash transfer to the card,
    not a billing-cycle settlement. Reclassify it as a cash transfer so it is not
    hunted as a CC charge (and may mirror-annihilate against its counterpart)."""
    for r in records:
        if r["source_type"] not in ("bank", "fx"):
            continue
        if (r.get("category") or "").strip() != columns.CC_CHARGE_CATEGORY:
            continue
        if _is_thousands_multiple(r.get("amount_ils")):
            r["category"] = columns.CASH_TRANSFER_CATEGORY
            schema.add_flag(r, "assumed_category")
            stats["round_cc_reclassified"] += 1


# ------------------------------------------------------------------- CC charges

def _replace_cc_charges(records, dropped, stats) -> None:
    # A credit-card billing cycle = all of a card's rows sharing one charge date
    # (תאריך חיוב). Group by (card source_key, billing_date): that group is
    # exactly what one bank "העברה לחברת כרטיסי אשראי" row settles.
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    group_sum: dict[tuple[str, str], float] = defaultdict(float)
    group_serial: dict[tuple[str, str], float] = {}
    card_digits: dict[str, str] = {}

    for i, r in enumerate(records):
        if r["source_type"] != "credit-card":
            continue
        sk = r["source_key"]
        key = (sk, r.get("billing_date", ""))
        groups[key].append(i)
        if r.get("amount_ils") is not None:
            group_sum[key] += r["amount_ils"]
        if r.get("billing_serial") is not None:
            group_serial[key] = r["billing_serial"]
        card_digits.setdefault(sk, re.sub(r"\D", "", sk))

    consumed: set[tuple[str, str]] = set()

    for i, r in enumerate(records):
        if r["source_type"] not in ("bank", "fx"):
            continue
        if (r.get("category") or "").strip() != columns.CC_CHARGE_CATEGORY:
            continue
        charge = r.get("amount_ils")
        if charge is None:
            schema.add_flag(r, "unmatched_cc")
            stats["cc_unmatched"] += 1
            continue

        bill_date = r.get("billing_date", "")
        # The card's last-4 usually ends the bank row's reference (אסמכתה); it can
        # also appear inside the description (e.g. "כרטיסי אשראי לי - 8334").
        ref_digits = re.sub(r"\D", "", r.get("reference", "") or "")
        desc_digits = re.sub(r"\D", "", r.get("description", "") or "")
        cand_keys = [
            sk for sk, d in card_digits.items()
            if len(d) >= 3 and ((ref_digits and ref_digits.endswith(d)) or d in desc_digits)
        ]

        match_key = _find_cc_group(
            charge, bill_date, r.get("billing_serial"),
            cand_keys, group_sum, group_serial, consumed,
        )
        if match_key is None:
            schema.add_flag(r, "unmatched_cc")
            stats["cc_unmatched"] += 1
            continue

        consumed.add(match_key)
        link = f"cc:{r['id']}"
        for idx in groups[match_key]:
            records[idx]["link_group"] = link
        dropped.add(i)
        stats["cc_replaced"] += 1


def _find_cc_group(charge, bill_date, bill_serial, cand_keys,
                   group_sum, group_serial, consumed):
    tol = _amount_tol(charge)
    # 1) identified card(s), exact same billing date.
    for sk in cand_keys:
        key = (sk, bill_date)
        if key not in consumed and key in group_sum and abs(group_sum[key] - charge) <= tol:
            return key
    # 2) identified card(s), nearest billing date within a few days.
    if bill_serial is not None and cand_keys:
        best = None
        cand_set = set(cand_keys)
        for key, total in group_sum.items():
            if key[0] not in cand_set or key in consumed:
                continue
            gs = group_serial.get(key)
            if gs is None or abs(gs - bill_serial) > _CC_DATE_TOL_DAYS:
                continue
            if abs(total - charge) <= tol:
                gap = abs(gs - bill_serial)
                if best is None or gap < best[0]:
                    best = (gap, key)
        if best is not None:
            return best[1]
    # 3) no card identified: a single card whose group sums to the charge, on the
    #    same billing date, else within a few days (the bank often posts the
    #    settlement a day after the card's charge date).
    if not cand_keys:
        hits = [
            k for k, total in group_sum.items()
            if k[1] == bill_date and k not in consumed and abs(total - charge) <= tol
        ]
        if len(hits) == 1:
            return hits[0]
        if bill_serial is not None:
            near = []
            for key, total in group_sum.items():
                if key in consumed:
                    continue
                gs = group_serial.get(key)
                if gs is None or abs(gs - bill_serial) > _CC_DATE_TOL_DAYS:
                    continue
                if abs(total - charge) <= tol:
                    near.append((abs(gs - bill_serial), key))
            # Accept only if a single card matches within the window (unambiguous).
            if near and len({key[0] for _, key in near}) == 1:
                near.sort(key=lambda x: x[0])
                return near[0][1]
    return None


# ------------------------------------------------------------ mirrored transfers

def _annihilate_mirrors(records, dropped, stats) -> None:
    buckets: dict[float, list[int]] = defaultdict(list)
    for i, r in enumerate(records):
        if i in dropped:
            continue
        if (r.get("category") or "").strip() not in columns.INTERNAL_TRANSFER_CATEGORIES:
            continue
        amt = r.get("amount_ils")
        if amt is not None and round(abs(amt), 2) != 0:
            buckets[round(abs(amt), 2)].append(i)

    matched: set[int] = set()
    counter = 0
    for amt_abs, idxs in buckets.items():
        neg = [i for i in idxs if records[i]["amount_ils"] < 0]
        pos = [i for i in idxs if records[i]["amount_ils"] > 0]
        for ni in neg:
            if ni in matched:
                continue
            nr = records[ni]
            best = None  # (score, pi)
            for pi in pos:
                if pi in matched:
                    continue
                pr = records[pi]
                gap = _date_gap(nr, pr)
                if gap > _XFER_DATE_TOL_DAYS:
                    continue
                shared_ref = bool(nr.get("reference")) and nr.get("reference") == pr.get("reference")
                diff_account = nr.get("source_name") != pr.get("source_name")
                if not (shared_ref or diff_account):
                    continue  # not an obvious mirror
                score = (0 if shared_ref else 1, 0 if diff_account else 1, gap)
                if best is None or score < best[0]:
                    best = (score, pi)
            if best is not None:
                pi = best[1]
                counter += 1
                link = f"xfer:{counter}"
                nr["link_group"] = link
                records[pi]["link_group"] = link
                matched.add(ni)
                matched.add(pi)
                dropped.add(ni)
                dropped.add(pi)
                stats["transfers_annihilated"] += 1


def _date_gap(a: dict, b: dict) -> float:
    da, db = a.get("date_serial"), b.get("date_serial")
    if da is None or db is None:
        return 9999
    return abs(int(da) - int(db))

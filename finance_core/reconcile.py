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

1b. Split ILS/FX settlement
   Some cards settle one billing cycle across *two* rows instead of one: a bank
   row for the cycle's ILS-only transactions plus an fx row for the rest (seen
   historically on one FX-linked card). Neither leg alone equals the full cycle
   total, so after every whole-cycle match above is exhausted, still-unresolved
   bank/fx charge rows are paired by billing_date: a candidate card is one whose
   ILS-only-transactions subtotal matches an unresolved bank leg, and whose
   remaining (non-ILS) subtotal matches an unresolved fx leg's original-currency
   amount. Only accepted when both legs are unambiguous (exactly one candidate
   card, exactly one opposite-type leg that date); both legs are then dropped
   and the card's transactions linked, same as a whole-cycle match.

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
        "cc_split_replaced": 0,
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


def reconcile_incremental(records: list[dict], appended: list[dict]) -> tuple[list[dict], dict]:
    """Cross-boundary reconciliation after an incremental merge.

    Within one export file, `reconcile()` sees both sides of every internal
    movement. Across an incremental merge the two sides often arrive in
    *different* runs - e.g. a partial export carries the bank's cumulative
    card charge while the card's own transactions were loaded months earlier
    (or vice versa). This pass re-runs the same matching rules scoped to rows
    that crossed the boundary, hunting partners in the WHOLE merged ledger:

    1. Cumulative CC charges: candidates are freshly appended charge rows plus
       legacy rows still flagged `unmatched_cc` (a retry is harmless - the
       ledger never stores successfully-reconciled charges, since those are
       dropped, so any stored/appended charge row is genuinely unsettled).
       Card groups already carrying a `cc:` link keep it; only empty links are
       filled with the settling charge's id.
    2. Mirrored transfers: annihilated pairs never survive in the ledger (both
       legs are dropped), so every stored internal-transfer leg is genuinely
       unmatched and may pair with a newly appended counterpart. At least one
       leg of every pair must be appended, so pure history is never touched.
    3. Duplicate credit-card detail rows: a transaction can be stored twice
       under two different ids when its merchant text changed between export
       snapshots (so the incremental content fingerprint - which includes
       description - no longer matches), one copy staying at its provisional
       billing date with no cumulative-charge link while the other went on to
       settle normally. Unambiguous pairs (same card + date + amount, exactly
       one unsettled side and one settled side) are folded together; see
       `_merge_duplicate_cc_rows`.

    Returns (kept_records, stats)."""
    stats = {"cc_replaced": 0, "transfers_annihilated": 0, "cc_split_replaced": 0,
             "cc_duplicate_merged": 0}
    appended_ids = {id(r) for r in appended}
    is_appended = lambda i: id(records[i]) in appended_ids
    dropped: set[int] = set()

    # ------------------------------------------------ 1) cumulative CC charges
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    group_sum: dict[tuple[str, str], float] = defaultdict(float)
    group_ils_sum: dict[tuple[str, str], float] = defaultdict(float)
    group_nonils_sum: dict[tuple[str, str], float] = defaultdict(float)
    group_serial: dict[tuple[str, str], float] = {}
    card_digits: dict[str, str] = {}
    for i, r in enumerate(records):
        if r.get("source_type") != "credit-card":
            continue
        sk = r["source_key"]
        key = (sk, r.get("billing_date", ""))
        groups[key].append(i)
        amt = r.get("amount_ils")
        if amt is not None:
            group_sum[key] += amt
            if (r.get("currency_orig") or "ILS").strip() == "ILS":
                group_ils_sum[key] += amt
            else:
                group_nonils_sum[key] += amt
        if r.get("billing_serial") is not None:
            group_serial[key] = r["billing_serial"]
        card_digits.setdefault(sk, re.sub(r"\D", "", sk))

    charge_idxs = [
        i for i, r in enumerate(records)
        if r.get("source_type") in ("bank", "fx")
        and (r.get("category") or "").strip() == columns.CC_CHARGE_CATEGORY
        and (is_appended(i) or "unmatched_cc" in (r.get("flags") or []))
    ]
    # appended charges first: freshest information wins a contested group
    charge_idxs.sort(key=lambda i: not is_appended(i))

    consumed: set[tuple[str, str]] = set()
    for i in charge_idxs:
        r = records[i]
        charge = r.get("amount_ils")
        if charge is None:
            continue  # stays flagged unmatched_cc from the build
        ref_digits = re.sub(r"\D", "", r.get("reference", "") or "")
        desc_digits = re.sub(r"\D", "", r.get("description", "") or "")
        cand_keys = [
            sk for sk, d in card_digits.items()
            if len(d) >= 3 and ((ref_digits and ref_digits.endswith(d)) or d in desc_digits)
        ]
        match_key = _find_cc_group(
            charge, r.get("billing_date", ""), r.get("billing_serial"),
            cand_keys, group_sum, group_serial, consumed,
        )
        if match_key is None:
            continue  # stays flagged unmatched_cc
        consumed.add(match_key)
        link = f"cc:{r['id']}"
        for idx in groups[match_key]:
            if not records[idx].get("link_group"):
                records[idx]["link_group"] = link
        dropped.add(i)
        stats["cc_replaced"] += 1

    # 1b) split ILS/FX settlement (rule 1b): retry with whatever is still
    # unresolved, same as the single-leg retry above - harmless since an
    # unmatched_cc row never previously resolved.
    still_unresolved = [i for i in charge_idxs if i not in dropped]
    _match_split_cc_settlements(
        records, still_unresolved, dropped, stats, groups, group_ils_sum, group_nonils_sum, consumed,
    )

    # ---------------------------------------- 3) duplicate CC detail rows
    _merge_duplicate_cc_rows(records, dropped, stats)

    # -------------------------------------------------- 2) mirrored transfers
    eligible = [
        i for i, r in enumerate(records)
        if i not in dropped
        and (r.get("category") or "").strip() in columns.INTERNAL_TRANSFER_CATEGORIES
        and r.get("amount_ils") is not None and round(abs(r["amount_ils"]), 2) != 0
    ]
    buckets: dict[float, list[int]] = defaultdict(list)
    for i in eligible:
        buckets[round(abs(records[i]["amount_ils"]), 2)].append(i)

    matched: set[int] = set()
    counter = 0
    for ni in [i for i in eligible if is_appended(i)]:
        if ni in matched:
            continue
        nr = records[ni]
        same_amt = buckets.get(round(abs(nr["amount_ils"]), 2), [])
        opposites = [i for i in same_amt
                     if records[i]["amount_ils"] > 0] if nr["amount_ils"] < 0 else \
                    [i for i in same_amt
                     if records[i]["amount_ils"] < 0]
        best = None  # (score, pi)
        for pi in opposites:
            if pi in matched or pi == ni:
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

    kept = [r for i, r in enumerate(records) if i not in dropped]
    return kept, stats


def _merge_duplicate_cc_rows(records: list[dict], dropped: set[int], stats: dict) -> None:
    """Fold a still-provisional credit-card row into its settled sibling when
    the two are clearly the same physical transaction stored twice. This
    happens when a merchant's reported text changes between export snapshots
    (e.g. a truncated name becomes the full one, or a branch/country suffix is
    added once the charge settles) - the incremental content fingerprint
    (source + date + description + amount) then no longer matches, so the
    newer snapshot's row is appended instead of updating the stored one.

    Only folds a bucket (same card, date_iso, ILS amount) that is unambiguous:
    exactly one row still sitting at its provisional billing date
    (`billing_date == date_iso`) with no cumulative-charge link, and exactly
    one other row in that same bucket that already carries a `link_group`
    (confirmed settled). The provisional row's id is kept (first-seen wins,
    per the incremental-merge id-stability rule); the settled row's billing
    date, link, and any blank fields it fills in are adopted, and the settled
    row is dropped. The settled row's description replaces the provisional
    one unless it is simply the provisional text reversed (a bidi-rendering
    artifact some exports introduce, which is strictly less readable, not a
    correction)."""
    buckets: dict[tuple, list[int]] = defaultdict(list)
    for i, r in enumerate(records):
        if i in dropped or r.get("source_type") != "credit-card":
            continue
        amt = r.get("amount_ils")
        if amt is None:
            continue
        key = (r.get("source_key"), r.get("date_iso", ""), round(amt, 2))
        buckets[key].append(i)

    for idxs in buckets.values():
        if len(idxs) < 2:
            continue
        orphans = [i for i in idxs
                   if records[i].get("billing_date") == records[i].get("date_iso")
                   and not records[i].get("link_group")]
        settled = [i for i in idxs if records[i].get("link_group")]
        if len(orphans) != 1 or len(settled) != 1:
            continue  # only fold unambiguous 1:1 pairs
        oi, si = orphans[0], settled[0]
        orphan, settle = records[oi], records[si]
        if (orphan.get("currency_orig") or "ILS") != (settle.get("currency_orig") or "ILS"):
            continue

        orphan["billing_date"] = settle.get("billing_date") or orphan["billing_date"]
        orphan["link_group"] = settle.get("link_group")
        for field in ("merchant_country", "time", "detail"):
            if not orphan.get(field) and settle.get(field):
                orphan[field] = settle[field]
        old_desc, new_desc = orphan.get("description", ""), settle.get("description", "")
        if new_desc and new_desc != old_desc and new_desc[::-1].strip() != old_desc.strip():
            orphan["description"] = new_desc
        if (not orphan.get("category") or orphan["category"] == columns.UNCLASSIFIED) and settle.get("category"):
            orphan["category"] = settle["category"]
        schema.add_flag(orphan, "assumed_duplicate_merge")
        note = f"Merged duplicate {settle['id']} (same card/date/amount; provisional vs settled export)."
        orphan["explanation"] = f"{orphan['explanation']} {note}".strip() if orphan.get("explanation") else note
        dropped.add(si)
        stats["cc_duplicate_merged"] += 1


# ------------------------------------------------------------------- CC charges

def _build_cc_groups(records):
    """Group credit-card rows by (card source_key, billing_date): each group is
    exactly what settles one billing cycle. Alongside the group's full ILS sum,
    also split it into an ILS-only-transactions subtotal and the rest (for rule
    1b's split-settlement matching)."""
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    group_sum: dict[tuple[str, str], float] = defaultdict(float)
    group_ils_sum: dict[tuple[str, str], float] = defaultdict(float)
    group_nonils_sum: dict[tuple[str, str], float] = defaultdict(float)
    group_serial: dict[tuple[str, str], float] = {}
    card_digits: dict[str, str] = {}

    for i, r in enumerate(records):
        if r["source_type"] != "credit-card":
            continue
        sk = r["source_key"]
        key = (sk, r.get("billing_date", ""))
        groups[key].append(i)
        amt = r.get("amount_ils")
        if amt is not None:
            group_sum[key] += amt
            if (r.get("currency_orig") or "ILS").strip() == "ILS":
                group_ils_sum[key] += amt
            else:
                group_nonils_sum[key] += amt
        if r.get("billing_serial") is not None:
            group_serial[key] = r["billing_serial"]
        card_digits.setdefault(sk, re.sub(r"\D", "", sk))

    return groups, group_sum, group_serial, card_digits, group_ils_sum, group_nonils_sum


def _replace_cc_charges(records, dropped, stats) -> None:
    # A credit-card billing cycle = all of a card's rows sharing one charge date
    # (תאריך חיוב). Group by (card source_key, billing_date): that group is
    # exactly what one bank "העברה לחברת כרטיסי אשראי" row settles.
    groups, group_sum, group_serial, card_digits, group_ils_sum, group_nonils_sum = _build_cc_groups(records)

    consumed: set[tuple[str, str]] = set()
    unresolved: list[int] = []

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
            unresolved.append(i)
            continue

        consumed.add(match_key)
        link = f"cc:{r['id']}"
        for idx in groups[match_key]:
            records[idx]["link_group"] = link
        dropped.add(i)
        stats["cc_replaced"] += 1

    _match_split_cc_settlements(
        records, unresolved, dropped, stats, groups, group_ils_sum, group_nonils_sum, consumed,
    )

    for i in unresolved:
        if i in dropped:
            continue
        schema.add_flag(records[i], "unmatched_cc")
        stats["cc_unmatched"] += 1


def _match_split_cc_settlements(records, unresolved, dropped, stats,
                                 groups, group_ils_sum, group_nonils_sum, consumed) -> None:
    """Rule 1b: pair an unresolved bank leg with an unresolved fx leg on the same
    billing_date when a single card's ILS-only subtotal matches the bank leg and
    its remaining (non-ILS) subtotal matches the fx leg's original-currency
    amount. Only accepted when unambiguous (one candidate card, one opposite-leg
    candidate that date)."""
    by_date: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"bank": [], "fx": []})
    for i in unresolved:
        bill_date = records[i].get("billing_date", "")
        if bill_date:
            by_date[bill_date][records[i]["source_type"]].append(i)

    matched: set[int] = set()
    for bill_date, legs in by_date.items():
        bank_idxs, fx_idxs = legs["bank"], legs["fx"]
        if not bank_idxs or not fx_idxs:
            continue
        for bi in bank_idxs:
            if bi in matched:
                continue
            bank_amt = records[bi].get("amount_ils")
            if bank_amt is None:
                continue
            cand = [
                key for key, total in group_ils_sum.items()
                if key[1] == bill_date and key not in consumed
                and abs(total - bank_amt) <= _amount_tol(bank_amt)
            ]
            if len(cand) != 1:
                continue
            key = cand[0]
            nonils_total = group_nonils_sum.get(key, 0.0)
            fx_hits = [
                fi for fi in fx_idxs
                if fi not in matched
                and abs((records[fi].get("amount_orig") or 0.0) - nonils_total) <= _amount_tol(nonils_total or bank_amt)
            ]
            if len(fx_hits) != 1:
                continue
            fi = fx_hits[0]

            consumed.add(key)
            link = f"cc:{records[bi]['id']}+{records[fi]['id']}"
            for idx in groups[key]:
                records[idx]["link_group"] = link
            dropped.update((bi, fi))
            matched.update((bi, fi))
            stats["cc_split_replaced"] += 1


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

---
name: finance-topic
description: Extract a topic-related subset (a trip, a recurring bill, a custom filter, or "everything except other topics") from the transaction ledger built by finance-unify, into a new CSV. Use when the user wants records for a specific trip abroad or local vacation, mortgage/insurance/utility payments, refunds, uncategorized rows, or any other filtered slice of their unified personal-finance ledger - without touching the ledger itself.
disable-model-invocation: true
---

# finance-topic (Skill 2)

> Part of [personal-finance-suite](../SKILL.md). The invocable suite router
> reads this procedure document before handling topic-extraction work.

Pull a topic-related subset of rows out of the ledger `finance-unify` built,
into its own new CSV, keeping the original row ids untouched. Never modifies
`data/ledger.csv` (or any other finance-unify output). See
[README.md](README.md) for the full guide, including a step-by-step,
non-technical walkthrough of a fuzzy request.

## When to use
- The user wants all records for a trip (abroad or local/domestic).
- The user wants a recurring topic pulled out (mortgage, Arnona, refunds,
  still-uncategorized rows, income, car expenses, school costs, ...).
- The user wants "everything in period X that ISN'T part of some other topic"
  (e.g. "all 2024 spending that isn't trips/vacations").
- The user wants to adjust a previous topic's row list (add/remove specific
  transactions) and re-generate its output file.

## First, make sure the ledger exists
If `<project>/data/ledger.csv` is missing, `propose`/`review`/`extract` all
print `[error] ledger not found: ...` and exit non-zero. In that case, tell
the user and offer to run **finance-unify** first (see its `SKILL.md`).

## Workflow: propose -> (review) -> extract
1. **`propose`** scans the ledger for the given topic and writes/updates
   `data/topics/<name>.json` (the spec) and `data/topics/<name>.review.csv`
   (human-readable). Rows it's confident about go straight into the output
   set; genuinely ambiguous rows (see "Fuzzy topics" below) land in
   `pending_review` and are never silently included.
2. If anything is `pending_review`, **open `<name>.review.csv`** (or read it
   yourself) and decide each row with the `review` subcommand - or just ask
   the user "include this or not?" and run `review` on their behalf.
3. **`extract`** writes the final CSV(s) from the spec's approved rows. It
   never overwrites an existing output unless `--update` is passed, and it
   refuses to run while rows are still `pending_review` (unless `--force`).

```bash
# Direct topics (no ambiguity) - propose goes straight to 0 pending:
python scripts/topic.py propose --project <DIR> --topic mortgage-2017-2025 \
    --kind custom --date1 2017-01-01 --date2 2025-12-31 \
    --keyword "בינלאומי-משכנתא,ביטוח ישיר-חיים"
python scripts/topic.py extract --project <DIR> --topic mortgage-2017-2025

# Fuzzy topics (trips) - propose, resolve pending, then extract:
python scripts/topic.py propose --project <DIR> --topic usa-2026 \
    --kind trip_abroad --date1 2026-07-22 --date2 2026-08-07 \
    --destination "Portland,Charleston,New York,Seattle"
python scripts/topic.py review --project <DIR> --topic usa-2026 \
    --include-id "C8324-000064,C8324-000088" --exclude-id "C3687-001452"
python scripts/topic.py extract --project <DIR> --topic usa-2026

# List existing topics in a project
python scripts/topic.py list --project <DIR>
```

## Kinds (`--kind`)
- `trip_abroad` - `--date1/--date2` (+ optional `--destination`, `--lookback-days`
  default 45, `--extended-lookback-days` default 120, `--tail-days` default 14):
  pre-purchases before the trip (flights/hotels/car-rentals/attractions/shows),
  a further-back search (up to `--extended-lookback-days`) for long-lead flight
  and show/attraction bookings - always `pending_review` even on a destination
  match, a regardless-of-category check for pre-trip card charges in the trip's
  own currency or matching a destination hint, cash/FX withdrawal from
  `date1-1`, an ILS home-airport (Ben-Gurion/Lod) purchase on the departure
  date, foreign-currency card charges during the trip, toll/car-rental
  settlements shortly after.
- `trip_local` - `--date1/--date2` (+ optional `--region`, `--lookback-days`,
  `--extended-lookback-days`): same pre-purchase/extended-lookback/
  regardless-of-category logic (no currency check - domestic trips are ILS),
  all card charges + cash withdrawals during the trip window, no tail window.
- `custom` - direct filter: any combination of `--category`, `--exclude-category`,
  `--keyword`, `--exclude-keyword`, `--flag`, `--source-type`, `--min-amount`,
  `--max-amount`, `--date1/--date2`. Multiple values of the *same* flag are OR'd;
  *different* flags are AND'd. Always fully confident (no `pending_review`).
- `exclude_topics` - `--date1/--date2` (+ optional `--exclude-topic`, repeatable):
  every ledger row in range minus the approved rows of every referenced topic
  (default: auto-discovers every `trip_abroad`/`trip_local` topic in the
  project). Default `--scope expenses`. Always fully confident.

## Fuzzy topics: what lands in `pending_review`
- A pre-purchase (flight/hotel/car/attraction/show) before a trip that
  doesn't match any `--destination`/`--region` hint text.
- A flight or show/attraction ticket found only in the extended lookback
  window (46-120 days back by default) - **always** pending, even if a
  destination/region hint matches (too easy to be a different trip).
- A card charge before the trip that shares the trip's currency (abroad) or
  matches a destination/region hint in its text, but isn't in a typical
  pre-purchase category - surfaced regardless of category.
- A foreign-currency charge during a trip abroad that looks like a generic
  online retailer (Amazon, AliExpress, ...).
- An ILS purchase at the home airport (Ben-Gurion/Lod) on the trip's
  departure date (`date1` or `date1+1`) - duty-free, food, shopping paid in
  ILS before/after the foreign-currency leg begins.
- A toll/car-rental settlement shortly after a trip that doesn't match a
  destination hint.

Never guess on these - always surface them via the review CSV and let the
user (or their explicit instruction) decide with `review --include-id` /
`--exclude-id`.

## Other arguments
- `--ledger PATH` override the ledger path (default `<project>/data/ledger.csv`).
- `--date-field date_iso|billing_date` which date column to compare (default
  `date_iso`, per the ledger's own convention - only switch if the user
  explicitly asks for billing-date semantics).
- `--scope all|expenses|income` (default `all`, except `exclude_topics` which
  defaults to `expenses`).
- `extract --sort seq|op_seq` (default `op_seq`), `--split-by month|quarter|year`,
  `--out PATH`, `--update`, `--force`.

## After running
Report `propose`'s console summary (candidates scanned, newly confident vs.
pending) and point at the review CSV path if anything needs a decision.
After `extract`, report the output path(s) and row count. Never edit
`data/ledger.csv` or any finance-unify output from this skill.

## Notes
- Uses the Python standard library only for its normal workflow. PyYAML is
  only used if a project's optional `topic_signals.yaml` is edited to tune
  heuristics; without it, the built-in defaults apply.
- On first use in a project, writes an editable `<project>/topic_signals.yaml`
  (optional to edit; see README.md).
- Topic specs (`data/topics/<name>.json`) are managed entirely through this
  CLI - there is no need for anyone to hand-edit them.

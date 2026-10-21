---
name: finance-analyze
description: Aggregate a finance-topic selection (a single topic or a suite of topics) from the unified ledger into an .xlsx workbook with summary sheets by bucket/category-group and by period, native pie/column charts, 3D comparison charts across similar topics, a flat drill-down details sheet of record ids, and a reconciliation sheet surfacing no_fx_rate rows, annihilated refunds and excluded transfers. Use when the user wants expense or income analysis, breakdowns by category/bucket/month, cross-topic comparisons (e.g. trips vs trips), or a shareable spreadsheet of their personal-finance data.
disable-model-invocation: true
---

# finance-analyze (Skill 3)

> Part of [personal-finance-suite](../SKILL.md). The invocable suite router
> reads this procedure document before handling analysis-workbook work.

Analyze a `finance-topic` selection - one topic, or a suite analyzed as their
union - into a chart-bearing `.xlsx` workbook. Read-only: never touches
`data/ledger.csv` or any topic file. See [README.md](README.md) for the full
guide.

## ALWAYS two steps: plan first, generate only after approval

1. **Plan** (generates nothing):

   ```powershell
   python "<skills>\finance-analyze\scripts\analyze.py" --project "<project>" --topic <name> --plan
   ```

2. Show the `[plan]` output to the user. Only after they approve it, re-run
   **without** `--plan` to build the workbook.

Never skip step 1, even for follow-up runs with changed flags - each changed
analysis gets its own plan first. Since a topic's ids were already curated by
`finance-topic`, the plan lists every excluded transfer/no_fx_rate row (id,
date, amount, category, description) so a miscategorized or wrongly-excluded
row is caught before generating - not just a count.

## Input modes

- `--topic NAME` - analyze one topic's approved ids.
- `--topics A,B,C` - union of several topics; if they look comparable (all
  trips, same kind, or plain date-range slices) comparison sheets with 3D
  charts are added automatically (`--compare auto|always|never`).

There are deliberately **no date-range flags**: a plain period slice is a
topic extraction - create a `custom` topic with `finance-topic propose`
(`--date1/--date2`) first, then analyze it here.

## Common options

- `--scope expenses|income|all` (default `expenses`) - positive rows are
  always income (including unmatched refunds); bound refund<->expense pairs
  are annihilated inside the analyzed set unless `--refunds keep`.
- `--split-by month|quarter|year|week|phase|custom` (default `month`) -
  `phase` splits before/during/after each row's own trip (every selected
  topic needs `date1`/`date2`); `custom` splits on arbitrary
  `--split-dates D1,D2,...` boundaries.
- `--split-date-field date_iso|billing_date` (default `billing_date`) - which
  ledger date column drives every period split above.
- `--refund-scope selected|ledger` (default `selected`) - with `ledger`, remove
  a selected refund or expense whose bound counterpart lies outside the topic or
  date range; use this for period reports that must not count later-refunded spend.
- `--monthly-matrix --month-range YYYY-MM,YYYY-MM` - for an expense/month
  report, add a month-by-group table with `Average` and `Total` rows, including
  zero-spend months, plus comparison charts.
- `--trip-comparison` - for a suite of approved `trip_abroad`/`trip_local`
  topics, add per-topic trip/vacation costs, type averages, and a continuous
  `date_iso` monthly cost comparison. Every selected row with an ILS amount is
  included, even if its ledger category ordinarily denotes an internal transfer.
  Add `--month-range YYYY-MM,YYYY-MM` to fix the monthly table to a calendar
  range (e.g. all months of two full years) instead of the default continuous
  transaction-month span; zero-fills months without a transaction while
  per-trip totals/averages still reflect every selected row's full cost. Both
  `Trip summary` and `Trip monthly costs` also break each trip/month down into
  Flights/Lodging/Cash withdrawal/Other sub-costs (by category: `טיסות`,
  `לינה`, `העברת מזומן`/`העברת מט"ח`, everything else), each with its own
  separate chart from the total-cost charts. Use
  `--subcost-split 'ID1|ID2:Label=fraction,Label=fraction;ID3:Label=fraction'`
  to override specific row(s) that actually cover more than one sub-cost (e.g.
  a combined flight+lodging payment); any unassigned fraction per row falls to
  Other.
- `--force-include ID,ID,...` - count specific row(s) as expense/income even
  though their category/group would normally exclude them as an internal
  transfer (e.g. an FX cash withdrawal that was actually trip spending).
- `--chart-dim bucket|group`, `--top-n N`, `--out PATH`, `--update`, `--force`.

## After running

Summarize headline numbers (expenses / income / net, annihilated refund
pairs), then point at the workbook: summary sheets with native pie/column
charts, `Details` (one row per record, filterable, stable `id`s),
`Reconciliation` (no_fx_rate rows that could not be summed, excluded
transfers, removed pairs and any out-of-range refund counterparts, stale ids).
Mention Google Sheets import
(File > Import) keeps tables and charts.

For topic-based trip analysis, approved topic membership is authoritative:
never discard a selected row because of category, sign, merchant, or another
heuristic unless the user explicitly asks to exclude it. Rows without an ILS
amount cannot be summed and remain visible as `no_fx_rate` reconciliation gaps.

## Notes

- Requires the ledger (`finance-unify`) and at least one reviewed topic
  (`finance-topic`); refuses topics with pending_review unless `--force`.
- Buckets come from the per-project `category_map.yaml` (`group_to_bucket` /
  `category_to_bucket` right-hand sides); while buckets stay at identity the
  pie falls back to category groups automatically.
- openpyxl is auto-installed on first generation; if that fails you get exact
  manual instructions and nothing is written.

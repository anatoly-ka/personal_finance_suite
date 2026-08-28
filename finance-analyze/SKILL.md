---
name: finance-analyze
description: Aggregate a finance-topic selection (a single topic or a suite of topics) from the unified ledger into an .xlsx workbook with summary sheets by bucket/category-group and by period, native pie/column charts, 3D comparison charts across similar topics, a flat drill-down details sheet of record ids, and a reconciliation sheet surfacing no_fx_rate rows, annihilated refunds and excluded transfers. Use when the user wants expense or income analysis, breakdowns by category/bucket/month, cross-topic comparisons (e.g. trips vs trips), or a shareable spreadsheet of their personal-finance data.
disable-model-invocation: true
---

# finance-analyze (Skill 3)

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
- `--force-include ID,ID,...` - count specific row(s) as expense/income even
  though their category/group would normally exclude them as an internal
  transfer (e.g. an FX cash withdrawal that was actually trip spending).
- `--chart-dim bucket|group`, `--top-n N`, `--out PATH`, `--update`, `--force`.

## After running

Summarize headline numbers (expenses / income / net, annihilated refund
pairs), then point at the workbook: summary sheets with native pie/column
charts, `Details` (one row per record, filterable, stable `id`s),
`Reconciliation` (no_fx_rate rows that could not be summed, excluded
transfers, removed pairs, stale ids). Mention Google Sheets import
(File > Import) keeps tables and charts.

## Notes

- Requires the ledger (`finance-unify`) and at least one reviewed topic
  (`finance-topic`); refuses topics with pending_review unless `--force`.
- Buckets come from the per-project `category_map.yaml` (`group_to_bucket` /
  `category_to_bucket` right-hand sides); while buckets stay at identity the
  pie falls back to category groups automatically.
- openpyxl is auto-installed on first generation; if that fails you get exact
  manual instructions and nothing is written.

# finance-analyze (Skill 3)

Aggregate a `finance-topic` selection - a single topic or a **suite** of
topics - into one `.xlsx` workbook with native Excel/Google-Sheets charts.
Skill 3 is the read-only consumer at the end of the pipeline (see the
[suite README](../README.md) for the full pipeline diagram and how the three
skills fit together). It never modifies the ledger or topic files.

## Workflow: plan → approve → generate

Every analysis starts with a printed plan and generates nothing:

```powershell
python "<skills>\finance-analyze\scripts\analyze.py" --project <project> --topic usa-2026 --plan
```

```text
[plan] input      : usa-2026 (trip_abroad, 117 ids)
[plan] resolution : 117 row(s) resolved
[plan] refunds    : 3 bound pair(s) will annihilate (487.20 ₪ of refunds)
[plan] transfers  : 1 leftover internal-transfer leg(s) excluded from sums (1,200.00 ₪)
[plan]     C4412-000042     2026-03-05      -1,200.00 ₪  העברת מזומן | כספומט
[plan] fx gaps    : 2 no_fx_rate row(s) cannot be summed -> Reconciliation sheet
[plan]     C4412-000091     2026-03-11         -55.00 ₪  קניות שונות | Foreign Store
[plan]     C4412-000097     2026-03-14         -12.30 ₪  קניות שונות | Another Store
[plan] scope      : expenses (expenses 38,204.11 ₪ / income 350.00 ₪ / net -37,854.11 ₪)
[plan] breakdown  : by bucket (pie, top 12 + אחר), by month on billing_date (column chart)
[plan] comparison : DETECTED - all topics are trips -> Compare sheets + clustered BarChart3D / PieChart3D
[plan] output     : <project>\data\analysis\usa-2026.xlsx
[plan] sheets     : Overview | By bucket | By group | By period | Details | Reconciliation | Compare bucket | Compare period
```

After the user approves, re-run without `--plan`. The Overview sheet embeds
the executed parameters as a permanent audit record. Existing outputs are
never overwritten without `--update`.

Because a topic's ids were already curated by `finance-topic`, every row that
the plan proposes to exclude (internal transfers, no_fx_rate rows) is listed
individually - not just counted - so a miscategorized row is easy to spot
before generating. If a row is being excluded for the wrong reason (e.g. an
FX cash-withdrawal category that was actually trip spending), use
`--force-include ID,ID,...` to count it as expense/income instead; the
override shows up in its own `[plan] overrides` line and in the workbook's
`Reconciliation` sheet.

## Input modes

| Mode | Flag | Behavior |
|---|---|---|
| single topic | `--topic usa-2026` | that topic's approved ids |
| suite | `--topics usa-2026,eilat-2023` | union of approved ids; duplicate ids across topics counted once; each row remembers its topic(s) in Details |
| plain period slice | *(none)* | deliberately unsupported here - create a `custom` topic with `finance-topic propose --date1/--date2` first, then analyze it |

Topics still holding `pending_review` rows are refused unless `--force`.

## What the workbook contains

| Sheet | Contents |
|---|---|
| `Overview` | parameters used, headline totals, gap counts |
| `By bucket`, `By group` | totals / counts / share per reconstructed group & bucket + **native pie chart** (top-N + "אחר"); both sheets appear once the project customizes `category_map.yaml`, otherwise a single identity sheet |
| `By period` | rollup by the chosen `--split-by` granularity (month/quarter/year/week/phase/custom) + **native column chart** |
| `Details` | one row per member record - stable `id` first, then dates, description, category/group/bucket, amounts, flags, explanation, contributing topics; autofilter on, Google Sheets-friendly |
| `Reconciliation` | annihilated refund pairs, excluded internal-transfer legs, **no_fx_rate rows** (excluded from every sum - fix upstream in finance-unify), stale topic ids, warnings |
| `Compare <dim>`, `Compare period` | multi-topic matrices + **clustered BarChart3D** and **PieChart3D of grand totals** (auto when topics are comparable: all trips, same kind, or pure date-range slices; `--compare always` forces, `--compare never` disables) |
| `Trip summary`, `Trip monthly costs` | with `--trip-comparison`: per-topic costs classified as Trip/Vacation, average cost by type, and a continuous `date_iso` month table with `Average`/`Total` rows plus native bar, stacked-column, and line charts |

## Money semantics

- Everything is summed from signed `amount_ils`.
- Positive rows are income (salary, dividends, unmatched refunds); negative
  rows are expenses; rows whose group reconstructs to העברות פנימיות are
  internal transfers - excluded from sums but listed.
- **Refunds**: pairs bound by Skill 1 cancel inside the analyzed set
  (`--refunds keep` to keep both legs). A pair straddling two topics only
  cancels when you analyze the suite containing both legs; analyzing one leg's
  topic alone keeps it visible. Use `--refund-scope ledger` to instead remove
  the selected leg even when the bound counterpart is outside the selected
  topic or period; the counterpart remains visible for audit in `Reconciliation`.
- `no_fx_rate` rows have no ILS amount at all - they can't be summed, so they
  are surfaced instead of guessed.
- Buckets/groups come from the per-project `category_map.yaml`
  (`category_to_group` / `group_to_bucket` right-hand sides), falling back to
  the shared master under `finance_core/data/`; edit the project copy to
  reshape the pie.
- `--force-include ID,ID,...` overrides a specific row's category-driven
  transfer classification for this analysis, counting it as expense/income
  instead (e.g. an FX cash withdrawal that was actually spent on the trip,
  rather than moved between the user's own accounts). It never changes the
  ledger or `category_map.yaml` - only this run's totals/sheets.
- **Approved trip topics are authoritative** with `--trip-comparison`: every
  selected row with an ILS amount is included in the trip cost, including rows
  normally classified as internal transfers. Only explicit user exclusions,
  refund handling selected for the run, and unsummable `no_fx_rate` rows change
  that membership.

## Splitting the "By period" sheet

`--split-by` controls how rows are bucketed on the `By period` sheet (and the
`Compare period` sheet for suites):

| `--split-by` | Buckets |
|---|---|
| `month` (default) | `YYYY-MM` |
| `quarter` | `YYYY-Qn` |
| `year` | `YYYY` |
| `week` | ISO week, `YYYY-Www` |
| `phase` | `1 - before trip` / `2 - during trip` / `3 - after trip`, relative to **each row's own topic** trip window (`date1`/`date2`). Every selected topic must carry `date1`/`date2` (trip topics always do; a `custom` topic only if it was created with `--date1/--date2`) - otherwise the run is refused, naming the offending topic(s). In a multi-topic suite each topic keeps its own window, so "during trip" lines up per-trip even when trips don't overlap in time. |
| `custom` | Arbitrary boundaries from `--split-dates D1[,D2,...]` (ISO `yyyy-mm-dd`, any count/order - sorted automatically). N dates make N+1 buckets: before D1, [D1,D2), ..., on/after the last date. Example: `--split-dates 2026-08-01` makes a simple before/after-2026-08-01 split. |

`--split-date-field date_iso|billing_date` (default `billing_date`) picks which
ledger date column feeds *every* split above (including `month`/`quarter`/
`year`/`week`): the operation date (`date_iso`) or the billing/statement date
(`billing_date`, e.g. when a credit-card charge posts a month after the
purchase). The chosen field is recorded on the `Overview` sheet.

### Monthly group comparison

For a month-by-month expense comparison table, add `--monthly-matrix` and an
inclusive `--month-range YYYY-MM,YYYY-MM`. This requires `--scope expenses` and
`--split-by month`. The `Monthly comparison` sheet has a row for every requested
month (including zero-spend months), columns for reconstructed expense groups
and `Total`, then `Average` and `Total` rows. It also contains stacked monthly
columns by group, a total-monthly-expenses trend, and total spending by group.

## Options reference

```text
--project DIR              project root (default: cwd)
--ledger PATH              default <project>/data/ledger.csv
--topic N | --topics A,B   exactly one of the two
--scope expenses|income|all        default expenses
--split-by month|quarter|year|week|phase|custom      default month
--split-date-field date_iso|billing_date              default billing_date
--split-dates D1,D2,...    boundary dates, required when --split-by custom
--force-include ID,ID,...  reclassify id(s) out of transfers into expense/income
--chart-dim bucket|group           default bucket (falls back while identity)
--top-n N                  pie slices before "אחר" (default 12)
--refunds annihilate|keep          default annihilate
--refund-scope selected|ledger     default selected
--monthly-matrix                   add monthly expense-group comparison sheet/charts
--month-range YYYY-MM,YYYY-MM      required with --monthly-matrix
--trip-comparison                  trip/vacation summary and date_iso monthly comparison
--compare auto|always|never        default auto
--category-map PATH       override map path
--out PATH                default data/analysis/<label>.xlsx
--update --force          overwrite guards / pending_review bypass
--plan                    step 1: print the plan, write nothing
```

## Example prompts

- "Analyze the USA trip topic and show me the plan."
- "The plan for the USA trip looks good, generate the workbook."
- "Show me an analysis plan for both the USA-2026 and Eilat-2023 trips together, including comparison charts."
- "Analyze all mortgage payments from 2017 to 2025; show the breakdown by year."
- "Analyze all income and expenses for the 'whole-2024' topic; show the plan for the full scope."
- "Analyze the 'all-trips-suite' topic, but keep refund pairs visible instead of annihilating them."
- "Regenerate the USA analysis workbook with the top 20 pie slices instead of the default 12."
- "Analyze my 2024 expenses split by quarter."
- "Analyze the USA trip split by week."
- "Plan a comparison of all approved trips and vacations by transaction month,
  including every selected cash/FX withdrawal, with per-trip totals and average
  trip versus vacation cost."
- "Analyze the USA trip split into before/during/after the trip."
- "Analyze the USA-2026 and Italy-2025 trips together, comparing before/during/after each trip."
- "Analyze the whole-2024 topic split at 2024-07-01 and 2024-10-01 - three custom periods."
- "Analyze the USA trip by month, but split on the operation date instead of the billing date."
- "That C3687-001541 transfer was actually cash I withdrew for the trip - include it as an expense."

## Requirements

Python 3.9+. Standard library only for planning; generating the workbook
needs `openpyxl`, which is installed automatically on first use (manual
instructions if that fails). PyYAML is needed only if a category map exists
to load.

## Editing this skill

See the [suite README](../README.md#editing-these-skills) for how these
skills are laid out and edited.

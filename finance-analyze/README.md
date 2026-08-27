# finance-analyze (Skill 3)

Aggregate a `finance-topic` selection - a single topic or a **suite** of
topics - into one `.xlsx` workbook with native Excel/Google-Sheets charts.
Skill 3 is the read-only consumer at the end of the pipeline:

```
MyFinanda .xlsx ──finance-unify──▶ data/ledger.csv
                                      │
                          finance-topic (specs, review)
                                      │
                       finance-analyze ──▶ data/analysis/<label>.xlsx
```

It never modifies the ledger or topic files.

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
[plan] fx gaps    : 2 no_fx_rate row(s) cannot be summed -> Reconciliation sheet
[plan] scope      : expenses (expenses 38,204.11 ₪ / income 350.00 ₪ / net -37,854.11 ₪)
[plan] breakdown  : by bucket (pie, top 12 + אחר), by month (column chart)
[plan] comparison : DETECTED - all topics are trips -> Compare sheets + clustered BarChart3D / PieChart3D
[plan] output     : <project>\data\analysis\usa-2026.xlsx
[plan] sheets     : Overview | By bucket | By group | By period | Details | Reconciliation | Compare bucket | Compare period
```

After the user approves, re-run without `--plan`. The Overview sheet embeds
the executed parameters as a permanent audit record. Existing outputs are
never overwritten without `--update`.

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
| `By period` | month/quarter/year rollup + **native column chart** |
| `Details` | one row per member record - stable `id` first, then dates, description, category/group/bucket, amounts, flags, explanation, contributing topics; autofilter on, Google Sheets-friendly |
| `Reconciliation` | annihilated refund pairs, excluded internal-transfer legs, **no_fx_rate rows** (excluded from every sum - fix upstream in finance-unify), stale topic ids, warnings |
| `Compare <dim>`, `Compare period` | multi-topic matrices + **clustered BarChart3D** and **PieChart3D of grand totals** (auto when topics are comparable: all trips, same kind, or pure date-range slices; `--compare always` forces, `--compare never` disables) |

## Money semantics

- Everything is summed from signed `amount_ils`.
- Positive rows are income (salary, dividends, unmatched refunds); negative
  rows are expenses; rows whose group reconstructs to העברות פנימיות are
  internal transfers - excluded from sums but listed.
- **Refunds**: pairs bound by Skill 1 cancel inside the analyzed set
  (`--refunds keep` to keep both legs). A pair straddling two topics only
  cancels when you analyze the suite containing both legs; analyzing one leg's
  topic alone keeps it visible.
- `no_fx_rate` rows have no ILS amount at all - they can't be summed, so they
  are surfaced instead of guessed.
- Buckets/groups come from the per-project `category_map.yaml`
  (`category_to_group` / `group_to_bucket` right-hand sides), falling back to
  the shared master under `finance_core/data/`; edit the project copy to
  reshape the pie.

## Options reference

```text
--project DIR              project root (default: cwd)
--ledger PATH              default <project>/data/ledger.csv
--topic N | --topics A,B   exactly one of the two
--scope expenses|income|all        default expenses
--split-by month|quarter|year      default month
--chart-dim bucket|group           default bucket (falls back while identity)
--top-n N                  pie slices before "אחר" (default 12)
--refunds annihilate|keep          default annihilate
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

## Requirements

Python 3.10+. Standard library only for planning; generating the workbook
needs `openpyxl`, which is installed automatically on first use (manual
instructions if that fails). PyYAML is needed only if a category map exists
to load.

## Deploy note

Like the other skills, edit sources in this repo and re-deploy to the live
skills folder with the project's `deploy.ps1` after changes.

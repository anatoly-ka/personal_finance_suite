# Personal Finance Skills Suite

Three cooperating skills that turn a MyFinanda (Hebrew) export into a unified
ledger, curated topics, and chart-bearing analysis workbooks - plus
`finance_core/`, the shared library code they all import (not a skill you
invoke directly).

## Pipeline

```
MyFinanda .xlsx ──finance-unify──▶ data/ledger.csv
                                      │
                          finance-topic (specs, review)
                                      │
                       finance-analyze ──▶ data/analysis/<label>.xlsx
```

Each stage only *reads* the previous stage's output and never modifies it:
finance-unify owns `data/ledger.csv`; finance-topic reads the ledger
read-only and owns `data/topics/`; finance-analyze reads the ledger and
topics read-only and owns `data/analysis/`.

## The skills

| Skill | What it does | Docs |
|---|---|---|
| **finance-unify** (Skill 1) | Consolidates a multi-sheet MyFinanda XLSX export into one chronological ledger (CSV, optional XLSX): replaces cumulative credit-card charges with per-transaction rows, annihilates obviously-mirrored internal transfers, converts FX, auto-categorizes via a shared merchant map, and binds refunds to the expense they reverse. | [finance-unify/README.md](finance-unify/README.md) |
| **finance-topic** (Skill 2) | Extracts a topic-related subset of the ledger - a trip, a recurring bill, "everything except my trips", or any custom filter - into its own CSV, without ever touching the ledger. Fuzzy topics (trips) surface genuinely ambiguous rows for you to confirm instead of guessing. | [finance-topic/README.md](finance-topic/README.md) |
| **finance-analyze** (Skill 3) | Aggregates one topic - or a suite of topics - into an `.xlsx` workbook with native pie/column charts, a details drill-down, a reconciliation sheet, and 3D comparison charts across comparable topics. Always plans before it generates anything. | [finance-analyze/README.md](finance-analyze/README.md) |
| `finance_core/` | Shared Python library the three skills import (ledger schema, reconciliation, taxonomy, topic recipes, report writer). Not invoked directly. | - |

## Project & environment conventions

All three skills operate inside a **project** - one folder per dataset (e.g.
per household) holding your exports, the generated ledger, topic files,
analysis workbooks, and your per-project taxonomy/heuristics overrides. Keep
it separate from wherever the skills themselves live, e.g.
`%USERPROFILE%\Projects\<name>-finance\` (Windows) or
`~/Projects/<name>-finance/` (Linux/macOS). If the data is sensitive, keep it
local; no git repository is created by these skills.

**Where the skills themselves live** depends on your agent environment - this
suite is deployed under `~/.agents/skills/` here (Cursor, for example, would
use `~/.cursor/skills/` instead). Whenever a per-skill README shows a path
rooted at the skills folder (the shared `finance_core/`,
`finance-unify/merchant_categories.csv`, etc.), substitute your own.

You never need to open, view, or hand-edit any of these files yourself -
everything happens through plain-English requests to the assistant, which
runs the commands, reads the files, and reports back in the chat. The rest of
each skill's README explains the files it creates for anyone curious or
troubleshooting.

## Editing these skills

These skills run directly from their skills folder - there is no separate
build or deploy step. Edit files under `finance_core/`, `finance-unify/`,
`finance-topic/`, or `finance-analyze/` in place; changes take effect on the
very next run.

## Requirements at a glance

| Skill | Python | Extra packages |
|---|---|---|
| finance-unify | 3.9+ | none required (optional `openpyxl` only for the `--xlsx` mirror) |
| finance-topic | 3.9+ | none required (optional PyYAML only to edit `topic_signals.yaml`) |
| finance-analyze | 3.9+ | `openpyxl` (auto-installed on first use); PyYAML only if a `category_map.yaml` exists to load |

## Step-by-step walkthrough

A single end-to-end example, from a fresh project through an incremental
update. Each step shows a sample prompt and the result you should expect in
the chat - the exact commands the assistant runs are shown for reference,
but you never need to type them yourself.

### 1. Create the project
> "Create a personal-finance project called home-finance."

**Expected result:** the assistant creates the project folder (e.g.
`~/Projects/home-finance/`) with an empty `data/source/` subfolder ready to
receive exports. Nothing else exists yet - no ledger, no `category_map.yaml`
or `topic_signals.yaml` (those appear on each skill's first run in this
project).

### 2. Copy the full export into the project
> "Copy my MyFinanda export from `~/Downloads/MyFinandaExport.xlsx` into the
> home-finance project."

**Expected result:** the assistant copies the file into
`home-finance/data/source/MyFinandaExport.xlsx`. finance-unify always uses
the newest `.xlsx` under `data/source/` by default, so this file becomes the
source for the next step.

### 3. Load the export into the ledger
> "Load the export into the ledger."

**Expected result:** the assistant runs finance-unify (standalone mode). It
writes `data/ledger.csv` and `data/ledger.flags.csv`, and - since this is the
first run in this project - also copies an editable `category_map.yaml` into
it. It reports a summary (rows in ledger, CC charges replaced/unmatched,
transfers annihilated, merchant categories set, categories inferred, refunds
bound) and offers to fix any flagged rows.

### 4. Fix a categorization and regenerate the ledger
> "Set the category of record C3069-000123 to 'מסעדות', and regenerate the
> ledger."

**Expected result:** the assistant runs finance-unify again with
`--set "C3069-000123:category=מסעדות"`. The fix is appended to
`data/corrections.csv` (so it survives future rebuilds) and `data/ledger.csv`
is rewritten with the correction applied - that row now carries flag
`manual_category`. If the merchant isn't already in the shared merchant map,
the assistant also offers to promote the fix so future exports/projects
benefit automatically.

### 5. Create a trip topic
> "Extract all records related to my trip to the USA, 22 July - 7 August
> 2026, into a topic called usa-2026."

**Expected result:** the assistant runs finance-topic's `propose` for a
`trip_abroad` topic. It reports the confident matches plus a short table of
genuinely ambiguous rows (early bookings with no destination clue, generic
online-retailer charges during the trip, etc.) that need your decision,
writing `data/topics/usa-2026.json` and `.review.csv`. Nothing is extracted
yet.

### 6. Resolve ambiguous rows and extract the topic
> "The two flight bookings and the Space Needle tickets are part of the USA
> trip; the Paris charge isn't - drop it. Then extract the topic."

**Expected result:** the assistant moves those rows with `review
--include-id ... --exclude-id ...`, then runs `extract` once nothing is left
pending. It writes `data/topics/usa-2026.csv` (same columns as the ledger,
original ids/`seq`/`op_seq` untouched) and reports the final row count.

### 7. Create monthly expense topics, excluding trips
> "Create one expense topic per calendar month for 2026, excluding anything
> already part of a trip topic like usa-2026."

**Expected result:** the assistant creates twelve `exclude_topics` topics
(e.g. `exp-2026-01` ... `exp-2026-12`), one per month, each subtracting every
approved row from any trip topic in the project (auto-discovered - no need to
name `usa-2026` explicitly). Twelve files land under `data/topics/`, each
holding that month's non-trip expenses.

### 8. Plan a month-by-month, per-category comparison
> "Plan a comparative analysis of the 2026 monthly expense topics, broken
> down by category, comparing month over month."

**Expected result:** the assistant runs finance-analyze with all twelve
monthly topics as a suite and `--plan`. Because they all share the same
topic kind, comparison is auto-detected; the printed plan lists all twelve
inputs resolved, refund/transfer/fx-gap counts, totals, and the sheet list
`Overview | By bucket | By group | By period | Details | Reconciliation |
Compare bucket | Compare period` - `Compare bucket` is the month-by-category
matrix you asked for. Nothing is written to disk yet.

### 9. Fix the plan
> "Actually, split on the billing date instead of the transaction date, and
> keep refund pairs visible instead of cancelling them."

**Expected result:** the assistant re-plans with `--split-date-field
billing_date --refunds keep` added, and prints an updated plan reflecting
both changes - still nothing written - so you can confirm before generating.

### 10. Approve the plan and generate the analysis
> "The plan looks good now - generate the workbook."

**Expected result:** the assistant re-runs the same command without
`--plan`. It writes `data/analysis/<label>.xlsx` with every sheet from the
plan (native pie/column/3D charts included) and reports headline
expenses/income/net, refund pairs annihilated, and any `no_fx_rate` rows to
check on the `Reconciliation` sheet.

### 11. Fix a grouping and regenerate the analysis
> "A few grocery-store charges are landing under 'shopping' instead of
> 'groceries' in the breakdown - fix the grouping and regenerate the
> analysis."

**Expected result:** the assistant edits the project's `category_map.yaml`
(moving the relevant category to the right bucket), then re-runs the same
finance-analyze command with `--update`. No topic needs re-extracting -
buckets are reconstructed from `category_map.yaml` at analysis time, not
stored in the topic files - so the regenerated workbook immediately reflects
the fix.

### 12. Copy an incremental export into the project
> "Copy my new MyFinanda export from
> `~/Downloads/MyFinandaExport 2026.09.01.xlsx` into the home-finance
> project."

**Expected result:** the assistant copies it into `data/source/`, alongside
the original full export. Being the newest file there, it becomes the
default source for the next incremental run.

### 13. Update the ledger incrementally
> "Update the ledger with the new export."

**Expected result:** the assistant runs finance-unify with `--incremental`.
Rows are matched against the existing ledger by content fingerprint; stored
rows are kept as-is (ids, manual categories, refund links) while
export-corrected facts (e.g. a billing date finalized after the previous
snapshot) are adopted, genuinely new rows are appended, and a cross-boundary
pass resolves CC cycles/transfers split across the two exports. It reports
how many rows were matched/kept vs. newly appended.

### 14. Check and update the trip topic
> "Check whether the USA trip topic needs updating with the new data, and
> update it if so."

**Expected result:** the assistant re-runs `propose` for `usa-2026` (reusing
its stored kind/dates) against the refreshed ledger. If nothing new turns up
it reports the topic is already up to date; if new candidates appear (e.g. a
late toll charge that just settled) it confirms any newly-pending ones with
you, then runs `extract --update` to refresh `usa-2026.csv`.

### 15. Update the monthly topics
> "Update the monthly expense topics with the new data."

**Expected result:** the assistant re-runs `propose`/`extract --update` for
each of the twelve `exp-2026-*` topics, picking up any new transactions from
the incremental export and correctly excluding anything just confirmed part
of the USA trip in step 14.

### 16. Update the analysis
> "Update the monthly expense analysis with the latest data."

**Expected result:** the assistant re-runs the same finance-analyze command
from steps 10/11 with `--update`, regenerating `data/analysis/<label>.xlsx`
from the now-current ledger and topics, and reports the refreshed headline
totals.

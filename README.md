# Personal Finance Skills Suite

Three cooperating skills that turn a MyFinanda (Hebrew) export into a unified
ledger, curated topics, and chart-bearing analysis workbooks - plus
`finance_core/`, the shared library code they all import (not a skill you
invoke directly).

## Install

Install the suite in a directory named exactly `personal-finance-suite`,
directly under your agent's skills root. Supported examples include
`~/.agents/skills`, `~/.claude/skills`, `~/.config/opencode/skills`, and
`~/.cursor/skills`.

```bash
git clone https://github.com/anatoly-ka/personal_finance_suite.git ~/.agents/skills/personal-finance-suite
```

```powershell
git clone https://github.com/anatoly-ka/personal_finance_suite.git "$env:USERPROFILE\.agents\skills\personal-finance-suite"
```

For a ZIP download, GitHub extracts a nested `<repo>-main/` directory. Create
or rename the destination to `personal-finance-suite`, then move the contents
of the extracted directory into it. `SKILL.md` must sit directly inside
`personal-finance-suite`, not inside the archive's extra directory.

Restart your agent environment and ask, "what skills are available?" Confirm
that `personal-finance-suite` appears in the response.

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

## Suite layout

```
personal-finance-suite/
    SKILL.md             invocable router for the three-stage pipeline
    finance_core/        shared Python library and default data
    finance-unify/       ledger-building procedure and merchant map
    finance-topic/       topic extraction procedure
    finance-analyze/     analysis-workbook procedure
```

Keeping the related skills in one suite folder prevents top-level `README.md`
and skill-name collisions. The root router is the discovery entry point; it
reads the relevant child `SKILL.md` and delegates the requested pipeline stage.

## Project & environment conventions

All three skills operate inside a **project** - one folder per dataset (e.g.
per household) holding your exports, the generated ledger, topic files,
analysis workbooks, and your per-project taxonomy/heuristics overrides. Keep
it separate from wherever the skills themselves live, e.g.
`%USERPROFILE%\Projects\<name>-finance\` (Windows) or
`~/Projects/<name>-finance/` (Linux/macOS). If the data is sensitive, keep it
local; no git repository is created by these skills.

**Where the suite lives** depends on your agent environment. Here its root is
`~/.agents/skills/personal-finance-suite/`; Cursor, for example, could use
`~/.cursor/skills/personal-finance-suite/`. Whenever a per-skill README shows
a suite path (the shared `finance_core/`,
`finance-unify/merchant_categories.csv`, etc.), substitute your own suite root.

You never need to open, view, or hand-edit any of these files yourself -
everything happens through plain-English requests to the assistant, which
runs the commands, reads the files, and reports back in the chat. The rest of
each skill's README explains the files it creates for anyone curious or
troubleshooting.

## Editing these skills

These skills run directly from the suite folder - there is no separate build
or deploy step. Edit `SKILL.md`, `finance_core/`, `finance-unify/`,
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

### 7. Create a monthly-expense analysis topic
> "Create an expense topic for January through December 2026, excluding my
> Mortgage topic and all approved trip and vacation topics. Split it by billing
> month later in the analysis."

**Expected result:** the assistant creates one `exclude_topics` topic covering
the requested billing-date range. It subtracts the approved rows from Mortgage
and every trip topic, leaving a reproducible definition of ordinary expenses
without maintaining a separate topic file for every month.

### 8. Plan a billing-date monthly group comparison
> "Plan a comparative analysis of ordinary expenses from January through
> December 2026. Split on billing date, exclude refunded items even when their
> counterpart is outside the period, and add a monthly expense-group table with
> Average and Total rows plus comparison charts."

**Expected result:** the assistant runs finance-analyze with `--plan`,
`--split-date-field billing_date`, `--refund-scope ledger`, and
`--monthly-matrix --month-range 2026-01,2026-12`. The printed plan lists the
input topic, refund/transfer/fx-gap counts, and the `Monthly comparison` sheet
alongside the standard report sheets. Nothing is written to disk yet.

### 9. Refine the plan
> "Actually, keep refund pairs visible instead of cancelling them, and show
> the period on transaction date instead."

**Expected result:** the assistant re-plans with `--split-date-field date_iso
--refunds keep` and prints an updated plan reflecting both changes - still
nothing is written - so you can confirm before generating.

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

### 15. Update the monthly-expense analysis topic
> "Update the ordinary-expenses topic with the new data."

**Expected result:** the assistant re-runs `propose`/`extract --update` for
the one date-range topic, picking up new ledger rows while continuing to
exclude Mortgage and anything just confirmed as part of a trip.

### 16. Update the analysis
> "Update the monthly expense analysis with the latest data."

**Expected result:** the assistant re-runs the same finance-analyze command
from steps 10/11 with `--update`, regenerating `data/analysis/<label>.xlsx`
from the now-current ledger and topics, and reports the refreshed headline
totals.

### 17. Compare approved trips and vacations
> "Plan a comparison of all approved trips and vacations by transaction month.
> Include every selected topic item, show each trip's cost, average trip and
> vacation costs, monthly averages, and comparison charts."

**Expected result:** the assistant uses finance-analyze's trip-comparison mode.
It treats the reviewed topic rows as authoritative, including any cash/FX
withdrawal already approved into a trip topic. The plan describes the `Trip
summary` and `Trip monthly costs` sheets, which show costs per topic, averages
for trips and vacations, continuous `date_iso` months (including zero months),
and native comparison charts before a workbook is generated.

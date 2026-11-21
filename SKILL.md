---
name: personal-finance-suite
description: Route personal-finance work for MyFinanda exports through a safe three-stage workflow. Use when the user wants to import or update a MyFinanda XLSX export, create a unified transaction ledger, correct ledger rows, extract a trip or other filtered topic, review ambiguous topic matches, or generate an XLSX analysis workbook with category, period, and comparison charts.
---

# Personal Finance Suite

Use this suite as a pipeline: `unify -> topic -> analyze`. Work in the user's
finance project, not in this suite directory. Each stage owns only its outputs:
the ledger, topic files, or analysis workbooks.

## Unify

For a MyFinanda XLSX export, ledger build or incremental update, reconciliation,
FX conversion, merchant categorization, or ledger correction, first read
[finance-unify/SKILL.md](finance-unify/SKILL.md). Follow its procedure to create
or update `data/ledger.csv` and report flags without modifying later-stage
outputs.

## Topic

For a trip, recurring expense, custom filter, or a request to exclude other
topics from a ledger slice, first read [finance-topic/SKILL.md](finance-topic/SKILL.md).
Follow its propose, review, and extract workflow. It reads the ledger but never
modifies it, and ambiguous matches require explicit review before extraction.

## Analyze

For spending or income totals, category/bucket and period breakdowns, topic
comparisons, or a chart-bearing XLSX workbook, first read
[finance-analyze/SKILL.md](finance-analyze/SKILL.md). Always create and show the
analysis plan first; generate the workbook only after the user approves it.

## Shared Suite Defaults

| Location | Purpose | Project behavior |
| --- | --- | --- |
| `finance_core/data/category_map.yaml` | Master category, group, and bucket taxonomy | finance-unify copies it to `<project>/category_map.yaml` on first use. |
| `finance_core/data/topic_signals.yaml` | Default topic-detection signals | finance-topic copies it to `<project>/topic_signals.yaml` on first use. |
| `finance-unify/merchant_categories.csv` | Shared merchant-to-category mappings | Used across projects; project corrections can be promoted here. |

## Guidance: Including ILS Virtual-Card Charges in Abroad-Trip Topics

The `trip_abroad` recipe is designed to detect foreign-currency charges (the 
primary indicator of spending abroad). Virtual debit cards like `PASSPORTCARD 
FINANCIAL NETANYA` can be used abroad but charge in ILS if linked to an Israeli 
account - so they are invisible to the foreign-currency pattern.

If you use a PassportCard abroad and want to include those charges in an 
abroad-trip topic:

1. **Manual include by ID:** When reviewing ambiguous rows in the topic's 
   `.review.csv`, explicitly include PassportCard transactions belonging to 
   that trip by id.
2. **Or create a custom filter:** Use `recipe_custom` with a category filter 
   (`--category "עושים קניות - שונות"`) combined with a date range, then manually 
   exclude unrelated ones.

The category mapping is maintained in 
`finance-unify/merchant_categories.csv`.

# finance-unify

Unifies a multi-sheet MyFinanda XLSX export (Hebrew) into a single, chronological
transaction table (CSV/XLSX) that other finance skills can analyze.

## Project

This skill operates inside a **project** - one folder per dataset (e.g. per
household). The project holds your exports, the generated ledger, the flag
report, your corrections, and your per-project taxonomy, so each dataset keeps
its own state and history. The skill itself lives in `~/.cursor/skills/`; your
financial data does not.

**Where it should reside:** anywhere you keep your work. Keep it outside the
skills folder (`%USERPROFILE%\.cursor\` on Windows, `~/.cursor/` on Linux/macOS).
- Windows: `%USERPROFILE%\Projects\<name>-finance\`
- Linux/macOS: `~/Projects/<name>-finance/` (or `~/Developer/`, `~/repos/`)

If the data is sensitive, keep the project local.

**How to create it (by prompt):** just ask, for example:

Windows:
> "Create a personal-finance project called home-finance and load
> `%USERPROFILE%\Downloads\MyFinandaExport.xlsx` into it."

Linux/macOS:
> "Create a personal-finance project called home-finance and load
> `~/Downloads/MyFinandaExport.xlsx` into it."

The agent will create the project folder (with a `data/source/` subfolder),
move into it, copy your export into `data/source/`, and run finance-unify. On
the first run it also writes an editable `category_map.yaml` into the project.
No git repository is created (none is needed) - so there is no `.git` or
`.gitignore`.

You can also do it manually.

Windows (PowerShell):

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\Projects\home-finance\data\source" | Out-Null
Copy-Item "$env:USERPROFILE\Downloads\MyFinandaExport.xlsx" "$env:USERPROFILE\Projects\home-finance\data\source\"
python "$env:USERPROFILE\.cursor\skills\finance-unify\scripts\unify.py" --project "$env:USERPROFILE\Projects\home-finance"
```

Linux/macOS (bash):

```bash
mkdir -p ~/Projects/home-finance/data/source
cp ~/Downloads/MyFinandaExport.xlsx ~/Projects/home-finance/data/source/
python ~/.cursor/skills/finance-unify/scripts/unify.py --project ~/Projects/home-finance
```

## What it does
- Reads only the transaction sheets (ILS bank accounts, FX accounts, credit
  cards); skips summaries, savings/loans/investments, budget, the cash wallet,
  and empty sheets.
- Maps each source's Hebrew columns into one unified schema (see the field
  mapping below).
- Replaces each cumulative monthly credit-card charge in a bank account with the
  underlying per-transaction credit-card rows (linked via `link_group`).
- Annihilates only *obviously mirrored* self-transfers (e.g. -X out of account A
  and +X into account B within a couple of days). See "Reconciliation algorithm".
- Converts foreign-currency bank rows to ILS using rates derived from the file's
  own conversion transactions (median within +/-1 month, else flagged).
- Auto-categorizes every matching row - overriding MyFinanda's own category,
  not just an unclassified one - using a shared merchant->category map
  (`merchant_categories.csv`); see "Merchant map".
- Numbers rows two ways: `seq` (account-status order by billing date) and
  `op_seq` (operation-time order), with a stable `id` per source.
- Stores `category` (ענף) but not the group (`קבוצת ענפים`): the group is a
  strict function of the category and is reconstructed later by Skill 3 from
  `category_map.yaml`.
- Flags anything uncertain (uncategorized, assumed category/FX rate, unmatched
  credit-card charge, refund, installment) and writes a flag report.
- Binds each `refund` row to the expense it reverses (same vendor, matching
  amount), so the pair can be excluded together in later analysis. See
  "Refund binding".

## Files

All paths below are **relative to the project directory** (`--project`,
default: current directory):

- Input:    `data/source/MyFinandaExport*.xlsx`  (newest is used by default)
- Output:   `data/ledger.csv` (+ optional `data/ledger.xlsx` with `--xlsx`)
- Report:   `data/ledger.flags.csv`
- Fixes:    `data/corrections.csv`  (auto-created/updated from `--set`)
- Taxonomy: `category_map.yaml`  (per-project copy, auto-created on first run)
- Config:   `finance.config.yaml` (optional path overrides)

The **merchant map** (`merchant_categories.csv`) is *not* per-project - it ships
with the skill and is shared across all projects:
- Windows: `%USERPROFILE%\.cursor\skills\finance-unify\merchant_categories.csv`
- Linux/macOS: `~/.cursor/skills/finance-unify/merchant_categories.csv`

Any path can be overridden on the command line. Path resolution order:
command-line argument > `finance.config.yaml` > the defaults above.

## Modes
- **Standalone**: build a fresh ledger from a source file.
- **Incremental** (`--incremental`): extend an existing `data/ledger.csv` with a
  newer export, preserving existing IDs and inferring categories for new
  uncategorized rows from similar past records.

## Reconciliation algorithm

Money that only moves between your own accounts is collapsed so it is not
counted as income/expense. Only *unambiguous* cases are removed; anything that
doesn't clearly reconcile is kept as-is.

1. **Credit-card monthly charge -> per-transaction rows.**
   A bank/FX row whose category (`ענף`) is `העברה לחברת כרטיסי אשראי` is one
   card's cumulative charge for a billing cycle. Matching:
   - Take the charge row's `billing_date` (its value date, `תאריך ערך`).
   - **Identify the card.** The bank row's `reference` (`אסמכתה`) ends with the
     card's last-4, which also appears in the card's sheet name - e.g. reference
     `38117` -> card `8117` -> sheet `לאומי קארד 8117`. The last-4 sometimes
     appears in the description instead (e.g. `כרטיסי אשראי לי - 8334`), so both
     are checked.
   - **Sum the cycle.** Among that card's credit-card rows with the *same*
     `billing_date` (`תאריך חיוב`), sum the signed ILS amounts. If it equals the
     charge (within `max(2 ILS, 0.5%)`), it is a match.

   Example: `לאומי ויזה` in `בנק לאומי 1923211` with `billing_date = 10/08/2026`
   and `amount_ils = -16334.23`, reference `38117`; the rows dated `10/08/2026`
   in `לאומי קארד 8117` sum to `-16334.23`, so those rows replace the charge.

   On a match the bank charge row is **dropped** and the credit-card rows are
   tagged `link_group = cc:<charge id>`. Fallbacks when the reference/description
   doesn't resolve a card: the identified card within a few days of the billing
   date, or (no card identified at all) a single card whose group sums to the
   charge - on the same date, else within a few days, since the bank often posts
   the settlement a day after the card's charge date (e.g. a `13309.8` charge on
   `16/11/2025` matches the `כאל 3687` cycle dated `15/11/2025`). No match -> the
   charge is **kept** and flagged `unmatched_cc` (e.g. charges predating the
   credit-card sheets, or standalone card-company credits with no matching
   cycle). The per-transaction rows are always present.

   *Round advances:* a CC-charge row whose ILS amount is a whole multiple of
   1000 (e.g. 4000, 8000) is a manual round advance/cash payment to the card,
   not a cycle settlement. It is reclassified to `העברת מזומן` (flag
   `assumed_category`) up front, so it is never hunted as a CC charge - it then
   behaves like any internal transfer (mirror-annihilated if its counterpart is
   in the file).

2. **Mirrored self-transfers.**
   A pair is annihilated only when it is *obviously* mirrored:
   - both rows are internal-transfer categories (`ענף` in a known set:
     `העברת מזומן`, `העברות פנימיות - שונות`, `העברת מט"ח`, `העברת חיסכון`),
   - equal magnitude in ILS,
   - dates within a couple of days, and
   - **either** the two rows are in different accounts **or** they share a
     reference number.

   Both legs are then **dropped** and tagged `link_group = xfer:<n>`. A transfer
   without such a counterpart in the file (e.g. a withdrawal to cash, or a
   transfer to an external account) is **left untouched and not flagged** - it
   is a normal row.

Because the group is not stored, Skill 3 recognizes transfers/CC settlements by
reconstructing the group from `category` (they map to `העברות פנימיות`).

## Refund binding

Every row flagged `refund` (a positive-amount credit-card row) is matched
against the earlier expense it reverses, so the pair can be dropped together
in later spending analysis instead of counting the refund as new income.

Matching looks back up to ~13 months for a credit-card expense from the same
vendor (same merchant text, or - for names that carry a trailing phone number
or country code, e.g. `PAYPAL *X 12345 LU` vs `...IT` - the same first couple
of words):

- **Obvious** (flag stays `refund`): the vendor matches and the amount is
  *exactly* the same in the original transaction currency - even a foreign-
  currency charge whose ILS refund differs because the exchange rate moved
  between the two dates still counts as obvious. A same-statement fallback
  also catches pairs where the vendor text itself doesn't match at all (e.g. a
  card fee immediately offset by a waiver): same card-statement batch, same
  category, exact opposite ILS amount.
- **Non-obvious / assumed** (flag becomes `assumed_refund`): the vendor
  matches but the amount doesn't line up exactly - a partial refund, a
  retained fee, or a cross-currency adjustment. The best available match (by
  amount closeness, preferring the same card and the closest date) is used.
- **No match found** within the window (flag becomes `unmatched_refund`): the
  refund is left as an ordinary row.

Either way, both the refund and the expense it was matched to get their
`reference` field set to each other's `id` (a bidirectional link) - if a row
already carries a `reference` (e.g. a manual correction), the new id is
appended with `;` rather than overwriting it. Both rows also get a one-line
`explanation`, deliberately different on each side so nothing is duplicated:
- The **refund** row explains the match itself - the FX difference, the
  refunded percentage, the fee-waiver note, etc. - or, for a plain exact
  match with nothing more to say, `Refund for <expense id>`.
- The **expense** row just says `Refunded by <refund id>`; the detail already
  lives on the refund row.

Rows you've already corrected by hand (e.g. `--set ID:reference=OTHER_ID`) are
left untouched.

## Customizing the taxonomy (per project)

On the first run the skill copies the master taxonomy to
`<project>/category_map.yaml`. It has three sections:

```yaml
category_to_group:          # the export's own hierarchy (ענף -> קבוצת ענפים)
  "בתי קפה, מסעדות ופאבים": "אוכלים בחוץ"
  "טיסות": "חופשות וטיולים"

group_to_bucket:            # YOUR coarse buckets - edit the right-hand side
  "אוכלים בחוץ": "אוכלים בחוץ"
  "חופשות וטיולים": "חופשות וטיולים"

category_to_bucket: {}      # optional per-category overrides
```

`category_to_group` reproduces the export taxonomy and is how the group is
rebuilt (finance-unify does not store it). To build your own analysis buckets
for Skill 3, edit the **right-hand side** of `group_to_bucket` (or add
`category_to_bucket` overrides) to point at coarse labels like `eat`, `sleep`,
`fly`, `drive`, `show`, `shop`:

```yaml
group_to_bucket:
  "אוכלים בחוץ": eat
  "חופשות וטיולים": fly
category_to_bucket:
  "לינה": sleep
```

Guidelines:
- Edit the **per-project** `category_map.yaml`, not the master (that one is the
  shared fallback):
  - Windows: `%USERPROFILE%\.cursor\skills\finance_core\data\category_map.yaml`
  - Linux/macOS: `~/.cursor/skills/finance_core/data/category_map.yaml`
- A `category_to_bucket` override wins over `group_to_bucket` for the same row.
- Leave `group_to_bucket` at identity to keep a group as its own bucket.
- Usually leave `category_to_group` alone (it mirrors the export).
- The map is consumed by the analysis skill (Skill 3); finance-unify only
  creates and preserves it.

## Merchant map (shared auto-categorization)

MyFinanda's own categorization is sometimes missing (`ללא סיווג`) and sometimes
just wrong or inconsistent for a given vendor (the same merchant can land in
different categories across statements). The skill fixes both from a shared
`merchant_categories.csv` that ships with the skill (not per-project), so a
merchant you categorize once is recognized - and corrected - in every project
and every future export.

Format (UTF-8 CSV; `#` lines are comments):

```csv
merchant,category,source
Ritz Paris,"בתי קפה, מסעדות ופאבים",user
GALLERIA BORGHESE ARTE,אטרקציות,assumed
```

- **Matching**: case-insensitive, with runs of whitespace collapsed to one
  space on both the merchant key and the row's `description`. A key is tried
  first as a plain substring; if that misses and the key contains regex syntax
  (e.g. `FLORIST ALMATY.*`), it is also tried as a regular expression.
- **File order decides, not key length.** When several keys match, the
  **first matching row in the file (top to bottom) wins** and no later row is
  even checked. So a narrow exception must be placed *above* the broader rule
  it needs to override - e.g. `חניון`/`חניונים` -> `חנייה` sit near the top of
  the file so they win over a later, broader `פרוגרס` rule that would
  otherwise also match `פרוגרס חניונים`.
- **Overrides, always**: a match here wins over whatever MyFinanda put in
  `ענף` for that row, not just over a blank/unclassified one - every entry in
  this file has already been reviewed (by you or the skill), so it's trusted
  over the raw export. The only things that outrank it are a manual fix
  (flag `manual_category`, from a `corrections.csv`/`--set` entry) and any
  `--set` correction given in the same run (corrections are applied after
  this map). When an override actually changes a real category MyFinanda had
  assigned, the row's `explanation` records what the old category was, so you
  can audit overrides from `ledger.flags.csv`.
- **source**: `user` (you confirmed it) sets flag `merchant_category`;
  `assumed` (a heuristic) sets flag `assumed_category`. Both override equally
  - the distinction is only for auditing which entries are confirmed.
- **Beware overly short/generic keys.** A key like `ENI` or `BG` can match as
  a coincidental substring of an unrelated description (e.g. `ENI` inside
  `PAYPAL *GENI` or `PETITE VENISE`) and mis-categorize it - this actually
  happened and both were fixed/removed. Prefer longer, more specific keys, or
  anchor a regex (e.g. `\bENI\b`, `^ACME(\s|$)`, `^STAY$`) when the vendor
  name is short or could be a prefix/suffix of something else.

### Reusing project fixes (promote to the shared map)

Per-project one-off fixes go in that project's `corrections.csv` (via `--set`).
After a run, any correction whose merchant is **not** already in the shared map
is reported:

```
[note] 3 project correction(s) are not in the shared merchant map (...).
       Re-run with --promote-corrections to reuse them across projects:
         SISSI UND FRANZ E5 SAT  ->  אטרקציות
```

Re-run with `--promote-corrections` to add those merchant->category pairs (as
`user`) to `merchant_categories.csv`. They are inserted right after the
header - ahead of every pre-existing entry - so a freshly promoted, specific
correction is guaranteed to be checked (and win) before any broader rule
already in the file; they then auto-apply everywhere. Because the map lives
inside the skill, promoting edits the deployed copy; in this dev setup, also
keep the source copy in sync (or run against the source skill folder) and
redeploy so the change is preserved.

## Output columns
`seq, op_seq, id, source_type, source_name, date_iso, time, date_serial,
billing_date, amount_ils, amount_orig, currency_orig, ils_rate, description,
detail, merchant_country, category, installment_no, installment_total,
reference, link_group, flags, explanation`

The group (`קבוצת ענפים`) is intentionally not a column - reconstruct it from
`category` via `category_map.yaml` (see "Customizing the taxonomy").

### Field mapping (source header -> column)
ILS and FX bank sheets share one layout; credit cards use another. `-` = no
source column; "derived" = computed by the skill.

| Column | Bank (ILS/FX) | Credit card | Notes |
|---|---|---|---|
| `seq` | derived | derived | order by `billing_date` |
| `op_seq` | derived | derived | order by `date_serial` |
| `id` | derived | derived | `<source>-<row>` (stable) |
| `source_type` | derived | derived | bank / fx / credit-card |
| `source_name` | derived | derived | sheet name |
| `date_iso` | `תאריך הפעולה` | `תאריך עסקה` | |
| `time` | - | `שעת הרכישה` | |
| `date_serial` | `תאריך הפעולה` | `תאריך עסקה` + `שעת הרכישה` | XLS numeric + time fraction |
| `billing_date` | `תאריך ערך` | `תאריך חיוב` | |
| `amount_ils` | `סכום` (FX: derived) | `סכום חיוב` | signed ILS |
| `amount_orig` | `סכום` | `סכום עסקה` | native currency |
| `currency_orig` | account currency | `מטבע עסקה` | |
| `ils_rate` | derived | derived | 1 for ILS |
| `description` | `תיאור הפעולה` | `שם בית עסק` | |
| `detail` | `פרטים נוספים` + `הערה שלי` | `פרטים נוספים` + `הערה שלי` | |
| `merchant_country` | - | `כתובת בית העסק` | |
| `category` | `ענף` | `ענף` | group reconstructed from this |
| `installment_no` | - | `מספר תשלום נוכחי` | |
| `installment_total` | - | `סך הכל תשלומים` | |
| `reference` | `אסמכתה` (bank) | derived (refund binding) | see "Refund binding"; appended with `;` if already set |
| `link_group` | derived | derived | ties reconciled/annihilated rows |
| `flags` | derived | derived | see below |
| `explanation` | derived | derived | refund-binding note, or a merchant-map category-override note |

### Flags
`unmatched_category` (`ענף = ללא סיווג`), `merchant_category` (from the shared
merchant map), `assumed_category` (in-file guess or `assumed` map entry),
`manual_category` (from a correction), `no_fx_rate`, `assumed_fx_rate`,
`unmatched_cc`, `installment`, `refund` (obvious binding found - see "Refund
binding"), `assumed_refund` (non-obvious binding found), `unmatched_refund`
(no binding found).

## Example prompts
- "Create a personal-finance project called home-finance and load
  `%USERPROFILE%\Downloads\MyFinandaExport.xlsx` into it." (Windows)
- "Create a personal-finance project called home-finance and load
  `~/Downloads/MyFinandaExport.xlsx` into it." (Linux/macOS)
- "Unify my latest MyFinanda export into the ledger."
- "Extend the ledger with data/source/MyFinandaExport 2026.08.13.xlsx."
- "Rebuild the ledger from `%USERPROFILE%\Downloads\export.xlsx` and write it to
  out/ledger.xlsx." (Windows) / from `~/Downloads/export.xlsx` (Linux/macOS)
- "Show me the flagged records from the last run."
- "Set category of record C3069-000123 to 'מסעדות' and save it to corrections."

## Requirements
Python 3.9+ only - finance-unify uses the **standard library alone** (no
third-party packages to install). It reads `.xlsx`, writes CSV, and manages the
per-project taxonomy without `openpyxl`/`pyyaml`/`pandas`. Those packages are
used only by the analysis skill (Skill 3), which installs them automatically if
you ask for chart output. The optional `--xlsx` mirror here is a convenience: it
is written only if `openpyxl` happens to be present, and skipped with a warning
otherwise.

Once you have a ledger, see **finance-topic** (Skill 2) to pull out
trip/topic-related subsets (e.g. "extract everything from my USA trip"). Skill
3 (expense analysis) is designed but not built yet.

## Deploying changes
The skill is edited in a source project and deployed into the skills folder
(`%USERPROFILE%\.cursor\skills\` on Windows, `~/.cursor/skills/` on Linux/macOS).
After changing anything under `finance_core/` or `finance-unify/`, redeploy:

```powershell
# Windows
powershell -File deploy.ps1
```

```bash
# Linux/macOS (PowerShell 7+)
pwsh -File deploy.ps1
```

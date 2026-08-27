---
name: finance-unify
description: Unify a multi-sheet MyFinanda XLSX export (Hebrew) into a single chronological transaction ledger (CSV/XLSX). Use when the user wants to consolidate MyFinanda account/credit-card exports, build or extend a unified transaction table, replace cumulative credit-card charges with per-transaction rows, or normalize personal-finance transactions across accounts and currencies.
disable-model-invocation: true
---

# finance-unify (Skill 1)

Consolidate a MyFinanda `.xlsx` export into one unified transaction ledger. See
[README.md](README.md) for the full overview and field mapping.

## When to use
- The user points at a MyFinanda export and wants a single unified table.
- The user wants to extend an existing ledger with a newer export (incremental).
- The user wants to correct flagged rows (wrong/missing categories, etc.).

## Run
The skill runs inside a project (one project per dataset). Defaults are relative
to the project root; any path can be overridden.

```bash
# Standalone: build data/ledger.csv from the newest export in data/source/
python scripts/unify.py --project <PROJECT_DIR>

# From a specific file, also writing an .xlsx mirror
python scripts/unify.py --project <PROJECT_DIR> --source "<PATH>.xlsx" --xlsx

# Incremental: extend an existing ledger
python scripts/unify.py --project <PROJECT_DIR> --incremental

# Record a correction for a flagged row (appends to data/corrections.csv)
python scripts/unify.py --project <PROJECT_DIR> --set "C3069-000123:category=מסעדות"
```

## Arguments
- `--project DIR`   project root (default: current directory)
- `--source PATH`   `.xlsx` file or a directory (default: newest in `data/source/`)
- `--out PATH`      ledger CSV (default: `data/ledger.csv`)
- `--flags-out PATH` flag report (default: `data/ledger.flags.csv`)
- `--corrections PATH` corrections CSV (default: `data/corrections.csv`)
- `--incremental`   merge into an existing ledger by content fingerprint
  (source+date+description+sum, multiset); matched rows keep their stored IDs
  (adopting only facts the newer export corrects, e.g. a provisional billing date
  finalized into the real credit-card cycle date), brand-new rows are appended
  (export-ID collisions get fresh unique IDs); afterwards a cross-boundary pass
  settles cumulative CC charges and mirrored transfers whose two legs arrived in
  different runs
- `--xlsx`          also write `data/ledger.xlsx` (needs `openpyxl`)
- `--set "id:field=value"` add a correction (repeatable)
- `--merchant-map PATH` shared merchant->category CSV (default: the skill's
  `merchant_categories.csv`)
- `--promote-corrections` add this run's project category corrections to the
  shared merchant map for reuse across projects

## After running
Report the summary (rows, replaced/unmatched CC charges, annihilated transfers,
merchant categories set, refunds bound, flags). If rows are flagged
(`unmatched_category`, `no_fx_rate`, `unmatched_cc`, `assumed_category`,
`assumed_fx_rate`, `assumed_refund`, `unmatched_refund`, etc.), summarize them
from `data/ledger.flags.csv` (which now also has an `explanation` column) and
offer to fix specific rows via `--set`.

If the summary prints a `[note] ... not in the shared merchant map`, **offer to
promote** those corrections: re-run with `--promote-corrections` so the
merchant->category pairs are added to `merchant_categories.csv` and reused in
every project. (In this dev setup, keep the source copy in sync and redeploy so
the promotion survives the next deploy.)

## Notes
- Runs inside a project (one per dataset); all data files live under
  `--project`. See [README.md](README.md) for creating a project.
- On first run it writes an editable `<project>/category_map.yaml` (full
  MyFinanda taxonomy, identity-mapped) for later per-project customization.
- A shared `merchant_categories.csv` (in the skill folder, not per-project)
  auto-categorizes every matching row - overriding MyFinanda's own category,
  not just filling a blank one. The first matching row in the file wins, so
  order matters. Project corrections can be promoted into it with
  `--promote-corrections`. See [README.md](README.md).
- Each `refund` row is matched to the expense it reverses (bidirectional
  `reference` + an `explanation` note); see "Refund binding" in
  [README.md](README.md).
- Uses the Python standard library only - no packages to install. (`openpyxl`
  for the optional `--xlsx` mirror is used only if already present.)
- Skill 2 (topic extraction) is implemented - see `finance-topic`. Skill 3
  (expense analysis) is implemented too - see `finance-analyze`.
- After editing the skill source, redeploy with `powershell -File deploy.ps1`.

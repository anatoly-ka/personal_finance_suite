# finance-topic

Pulls the records related to one "topic" - a trip, a recurring bill, or any
other filter - out of the ledger that **finance-unify** (Skill 1) built, into
its own new file. It never changes the ledger itself, and it never renumbers
or re-IDs a row: whatever `id` a transaction had in the ledger, it keeps in
the topic file too.

> Part of a 3-skill suite - see the [suite README](../README.md) for the full
> pipeline overview, project/environment conventions, and an end-to-end
> walkthrough covering all three skills.

**You do not need to open, view, or edit any file yourself to use this
skill.** Everything happens through plain-English requests to the assistant;
it runs the commands, reads the files, and reports back to you in the chat.
The rest of this document also explains the files it creates, for anyone
curious or troubleshooting - but skip straight to "Step-by-step example" if
you just want to see it in action.

## What this skill needs first

It reads `data/ledger.csv` inside your finance project - the file
**finance-unify** produces. If that file doesn't exist yet, just ask the
assistant to build it first, e.g.:

> "Load my latest MyFinanda export into the home-finance project, then
> extract the USA trip."

The assistant will run finance-unify, then this skill, in one go.

## The two kinds of requests

- **Direct requests** - no ambiguity, e.g. "all mortgage payments", "all
  refunds", "everything still uncategorized". The assistant runs one command
  and hands you the finished file.
- **Fuzzy requests** - trips (abroad or local), where some transactions are
  obviously related (a hotel booked for those dates, a restaurant charge
  abroad during the trip) and some are genuinely ambiguous (a flight booked
  two months earlier with no clue which trip it's for, an Amazon order that
  happened to post while you were away). For these, the assistant always
  shows you the unclear ones and waits for your answer before writing the
  final file - it never guesses on your behalf.

## Step-by-step example (fuzzy request)

This is a real run against a sample ledger, showing exactly what you would
see in the chat.

**You:**
> Extract all records related to my trip to the USA, 22 July - 7 August
> 2026, into a topic called "usa-2026".

**Assistant** (runs `propose`, then reports):
> I scanned the ledger and found 119 candidate rows for the USA trip:
> - **108 confident matches** - hotel bookings, flights, restaurants and
>   shops charged in USD during the trip window, parking, etc.
> - **11 rows I'm not sure about** - I need you to confirm these:
>
> | date | description | amount | why I'm unsure |
> |---|---|---|---|
> | 2026-06-08 | TRAVOMINT USD (flight booking) | -1,346.63 | booked ~6 weeks before the trip; no destination clue in the text |
> | 2026-06-09 | SOUTHWES ... 800-435-9792 | -1,863.52 | same - and the airline name is truncated in the export |
> | 2026-06-09 | TM *THE PHANTOM OF THE ... | -2,066.97 | a show ticket booked early; could be for this trip or unrelated |
> | 2026-06-11 | MAGNOLIA PLANTATION CO | -287.44 | booked ahead; this venue is actually in Charleston, which you did visit |
> | 2026-06-27 | SPACE NEEDLE TICKETS | -483.27 | booked ahead; Space Needle is in Seattle, which you did visit |
> | 2026-07-10 | RECREATION.GOV | -6.23 | a small federal-park booking fee, unclear which trip |
> | 2026-06-12 | SAS Temperle (Paris, FR) | +322.92 | this one's in **Paris**, not the US - probably unrelated |
> | 2026-08-02 | AMZ*m1echjkgl ... | -67.14 | an Amazon-style charge during the trip - could be trip shopping or just a delivery to home |
> | 2026-08-02 | AMZ*d2ijzv0fv ... | -24.35 | same |
> | ... (2 more) | | | |
>
> Which of these are part of the USA trip?

**You:**
> The flight, the Southwest flight, the Phantom of the Opera tickets,
> Magnolia Plantation, Space Needle, and the Recreation.gov fee are all part
> of the trip. The two Amazon charges were things we bought while there -
> include them too. The Paris charge is unrelated, drop it.

**Assistant** (runs `review --include-id ... --exclude-id ...`, then `extract`):
> Done - `usa-2026.csv` has 117 rows. The Paris charge was excluded, and it
> won't come back if you re-run `propose` later (e.g. after widening the
> search window).

That's the whole loop. You never had to open a spreadsheet or a YAML file -
you just answered in plain English about the handful of genuinely unclear
rows, out of 119 total.

### If you want to double-check the pending list yourself
Everything the assistant showed you above also lives in a plain CSV
(`data/topics/usa-2026.review.csv`) that opens fine in Excel/Numbers/Sheets,
with columns `status, id, date_iso, ..., reason`. You are welcome to open it,
but you never have to - re-stating your answer to the assistant in the chat
is enough.

## Direct-request examples (one round trip)

> "Extract all mortgage-related records from 2017 to 2025."

> "Pull out every record that's still uncategorized in 2025 so I can go fix
> it in finance-unify."

> "Extract all refunds from the last 12 months."

> "Extract all Arnona and utility payments for 2023."

For these, the assistant runs `propose` (which reports 0 pending) and then
`extract` right away - no back-and-forth needed.

## What each trip type looks for

**Trip abroad** (`--date1`/`--date2`, e.g. "trip to the USA 22.07-7.08.2026"):
- Flights, hotels, car rentals, attraction/show tickets and other
  leisure/culture spending bought **before** the trip, up to ~45 days back by
  default.
- **Long-lead bookings** - flights, attraction tickets, shows/performances -
  are also searched up to ~120 days back (hotels/car rentals aren't, since
  those are rarely booked that far ahead). Anything found only in this
  extended window is **always** flagged for your confirmation, even if it
  matches a destination hint - that far ahead a match is too easily a
  different trip (e.g. concert tickets bought 3 months earlier).
- Any card charge **before** the trip that happens to be in the trip's own
  currency (e.g. USD for a US trip), or whose description/merchant matches a
  destination hint, is flagged for confirmation too - **regardless of its
  category** (e.g. travel clothing/gear bought from a US website, or a
  purchase mentioning an airport code like "PDX"). This catches things a
  plain category list would otherwise miss entirely.
- Cash or foreign-currency withdrawals from the day before you left through
  the last day of the trip (including airport currency-exchange counters).
- Any foreign-currency card charge **during** the trip.
- Toll-road/rental-car charges that post up to ~2 weeks **after** you're back
  (these often bill late).
- Generic online-retailer charges during the trip (Amazon, AliExpress, ...)
  are always flagged for your confirmation rather than auto-included, since
  those often continue regardless of where you are.
- Give a `--destination` hint (city/airline/venue names that actually show up
  in your statements, e.g. "Portland, Charleston, New York, Seattle, Delta,
  United") so more of the ahead-of-time bookings are confidently matched
  instead of landing in the pending list. Just describe your destination(s)
  in the prompt and the assistant will turn it into this hint.

**Local trip / vacation** (e.g. "vacation in Eilat, 1-4 Feb 2023"): the same
idea, minus the tail window (no toll-processing delay expected) and minus the
currency check (a domestic trip is all ILS), plus **all** card charges during
the trip window count (not just foreign-currency ones) - give a `--region`
hint the same way if you know it.

**Heads-up on active households:** the long-lead and regardless-of-category
checks above mean more borderline rows can land in `pending_review` -
especially if you regularly buy show/concert tickets or travel abroad often
(e.g. a Lufthansa flight or Vilnius theater ticket bought months before an
unrelated Eilat weekend will still show up, asking you to confirm or reject
it). That's intentional: it would rather ask about a few extra rows than
silently miss - or wrongly include - a real one. Rejecting the irrelevant
ones is a single `review --exclude-id ...` per topic.

## What "everything except my trips" looks for

> "Extract all expenses in 2024 that are unrelated to the trips and
> vacations and put them in one file per month."

This runs the `exclude_topics` kind: it takes every expense row in 2024, and
removes anything already approved as part of any trip topic you've built
(auto-discovered - you don't need to name them). If you have an unfinished
trip topic with rows still pending review, the assistant will tell you
instead of silently guessing whether those rows should also be excluded.

## Files this skill creates

All under `<project>/data/topics/` (never inside the ledger's own folder
structure that finance-unify owns):

- `<name>.json` - the topic's saved state (its parameters, and which rows are
  included / still pending / excluded). Managed entirely by the assistant -
  there's no need to open or edit this.
- `<name>.review.csv` - the human-readable list shown in the chat above.
  Optional to open; useful if you want to see everything in a spreadsheet.
- `<name>.csv` - the final output, in the exact same column layout as
  `data/ledger.csv`, with the original row `id`/`seq`/`op_seq` untouched.
- `<name>/<period>.csv` - instead of a single `<name>.csv`, when you ask for
  output split by month/quarter/year (e.g. "one file per month").

Also, the first time this skill runs in a project, it writes an editable
`<project>/topic_signals.yaml` - the lists of categories/keywords it uses to
recognize cash withdrawals, pre-trip bookings, etc. You never need to touch
this either; see "Advanced: tuning the heuristics" if you're curious.

## Updating a topic later

> "For the USA trip, also include the taxi charge on August 5th, and drop
> that Amazon order after all."

The assistant re-runs `review` with the right include/exclude ids, then
`extract --update` to refresh `usa-2026.csv`. Nothing about your earlier
decisions is lost or re-asked.

> "What's still pending for the Eilat trip?"

The assistant just re-opens `eilat-2023.review.csv` (or re-runs `propose`,
which is safe to repeat) and reports back - no new decisions are made until
you answer.

## Other things you can ask for

> "List all the topics you've built for this project so far."

The assistant reports each topic's name, kind, and how many rows are
included / pending / excluded - handy once you've built a few and lost track
of what's already been extracted.

> "Extract the mortgage topic split by billing date instead of the
> transaction date."

By default rows are matched and split on the **transaction date**
(`date_iso`). If the billing/statement date matters more for a particular
topic (e.g. a credit-card charge that posts a month after the purchase you
care about), just ask for it explicitly and the assistant switches to
`billing_date` for that run.

## Advanced: tuning the heuristics (optional)

If matches consistently feel off for your data, you (or the assistant, on
your instruction) can edit `<project>/topic_signals.yaml`. It's a plain text
file with short lists, e.g.:

```yaml
cash_withdrawal_keywords:
  - "כספומט"
  - "ATM"
  - "הפועלים"      # Bank Hapoalim's airport currency-exchange desk
ambiguous_abroad_keywords:
  - "Amazon"
  - "AMZ*"
never_trip_keywords:        # Automatically ignored during trip scans
  - "NETFLIX"
  - "PATREON"
extended_lookback_categories:   # gets the ~120-day (not just ~45-day) pre-trip search
  - "טיסות"
  - "אטרקציות"
```

This is entirely optional - the built-in defaults (baked into the skill
itself) are used automatically if you never touch this file.

## Example prompts
- "Extract all records related to the trip to USA on 22.07-7.08.2026."
- "Extract all expenses spent on local vacation in Eilat Feb 1-4, 2023."
- "Extract all expenses in 2024 that are unrelated to the trips and
  vacations and put them in one file per month, based on the actual
  transaction date."
- "Extract all mortgage-related records for 2017-2025."
- "Extract all Arnona/utility payments for 2023."
- "Pull all car-related spending for 2024 (fuel, tolls, insurance,
  parking)."
- "Extract everything still uncategorized in 2025 so I can fix it."
- "Extract all refunds from the last 12 months."
- "Show me what's still pending for the USA trip topic."
- "Update the USA-2026 topic: exclude the Amazon order, include the taxi
  charge on August 5th."
- "List all topics I've extracted so far in this project."
- "Extract the mortgage topic split on the billing date instead of the
  transaction date."

## Requirements
Python 3.9+ only - the normal workflow (`propose`/`review`/`extract`) uses
the **standard library alone**. PyYAML is needed only if you choose to edit
the optional `topic_signals.yaml`; without it, the built-in defaults apply
and everything else still works.

## Editing this skill
See the [suite README](../README.md#editing-these-skills) for how these
skills are laid out and edited.

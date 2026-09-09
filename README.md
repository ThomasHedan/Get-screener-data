# Warrior Trading Screener — research archive

This branch is **data only**. It shares no history with the code: it is an
orphan branch, so cloning the application never drags the dataset along and the
site's build time stays flat however large this grows.

The code that writes it lives on the default branch, in
`warrior_screener/archive.py`.

## Layout

```
history/YYYY/MM/DD/<slot>.csv
```

One file per measurement, four per trading day, all times Eastern:

| slot | time | what it captures |
|------|------|------------------|
| `pre_open` | 09:25 | the state five minutes before the bell |
| `t_plus_5` | 09:35 | five minutes into the session |
| `t_plus_10` | 09:40 | ten minutes in — this is the one the dashboard publishes |
| `close` | 15:55 | five minutes before the bell, to label how the day ended |

A file may also carry the slot `manual`, from a hand-triggered run.

## Columns

| column | meaning |
|--------|---------|
| `slot` | which measurement this was *meant* to be |
| `captured_at` | when it actually ran, ISO 8601 with Eastern offset |
| `trade_date` | the session date |
| `ticker`, `exchange`, `security_type`, `sector` | identity |
| `open`, `high`, `low`, `close`, `prev_close` | prices at capture time |
| `change_pct` | percent move against `prev_close` |
| `gap_pct` | today's open against `prev_close` — see the caveat below |
| `range_pct` | `(high − low) / low`, in percent |
| `volume`, `avg_volume`, `relative_volume`, `dollar_volume` | volume context |
| `float_shares`, `market_cap` | share structure |
| `score` | composite rank in `[0, 1]`, blank on carried rows |
| `qualification` | `strict`, `relaxed`, `rejected` or `carried` |
| `rejected_by` | pipe-separated failed criteria |
| `in_play` | whether it made the published board at this slot |

Columns are only ever appended, never reordered or renamed — files already
written cannot be migrated.

## Four things to know before modelling on this

**`slot` and `captured_at` are different facts.** GitHub's scheduler runs late
under load, sometimes past ten minutes. The slot is derived from the cron entry
that fired, so it is always the intended measurement; `captured_at` is the
truth about when it happened. Filter on `captured_at` when precision matters.

**`carried` rows are not candidates.** A ticker that qualified earlier in the
day is re-recorded at every later slot even once it no longer passes the
price/change/volume gate, so that a morning setup has a closing figure to be
labelled against. Those rows carry `qualification = carried` and no score.
Reconstruct "was live at this instant" from `qualification`, never from a row's
presence in the file.

**`gap_pct` is meaningless in `pre_open`.** Before 09:30 the upstream `open`
field is not yet today's, so the pre-open gap describes a gap that has not
happened. It is real from `t_plus_5` onward.

**`relative_volume` is time-of-day normalized.** It compares volume so far
today against the average traded *by this same clock time* over the past 10
sessions — not a full-day figure. A 900× reading at 09:35 is not comparable to
a 900× reading at 15:55, and neither is comparable to a conventional full-
session RVOL. Treat the slot as part of the feature, not as a nuisance.

## Selection bias, stated plainly

Only tickers that cleared the screen's coarse gate — price $1–$20, up 10%+,
500k+ shares — are archived, plus carried names. There is no record of the
~6,000 tickers that did not. That is enough to study how already-selected
momentum names behave through the session, and **not** enough to learn the
selection itself: there are no negative examples. Widening the archive to the
full universe is a one-line change to the workflow if that becomes the goal.

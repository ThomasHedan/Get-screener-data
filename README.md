# Warrior Trading Screener

A dashboard of the low-float momentum stocks that are **in play** — the Ross
Cameron / Warrior Trading gap-and-go profile — screened from the whole US
market in a single request, refreshed two hours before the open, and deployed
as a static page on Vercel.

The screen is Python (`warrior_screener/`). The board is a Next.js page
(`app/`). Between them is one committed file, `data/today.json`: the screener
writes it, a GitHub Actions workflow commits it, and that push is what
redeploys the site.

---

## The screen

| # | Criterion | Default | Config key |
|---|-----------|---------|------------|
| 1 | Up on the day | **≥ 10%** vs. previous close | `min_change_pct` |
| 2 | Price | **$1 – $20** | `min_price` / `max_price` |
| 3 | Relative volume | **≥ 5×** | `min_relative_volume` |
| 4 | Float | **< 10M shares** | `max_float_shares` |
| 5 | News catalyst | **≥ 1 headline** | `require_news_catalyst` |

Plus universe hygiene: common stock and ADRs only, major exchanges only, and a
`min_day_volume` floor (500k shares) so untradeable names never reach the list.

### Ranking: which 5–10?

Every candidate that clears the filters gets a composite score in `[0, 1]`:

```
score = 0.40 · rank(relative volume)
      + 0.30 · rank(% change)
      + 0.20 · rank(−float)          # smaller float ranks higher
      + 0.10 · min(news, 3) / 3
```

The components are **percentile ranks within that day's own candidate pool**,
not absolute values. That keeps scores comparable between a sleepy Tuesday and
a small-cap frenzy, and stops one 400×-RVOL outlier from flattening everything
else. All weights live in `config/criteria.yml`.

### Strict vs. relaxed

Some sessions genuinely do not have five stocks with a sub-10M float running 5×
volume on a catalyst. Rather than return two names or invent three, the selector
tops the list up to `min_in_play` with the best names that fail *only* the
relaxable criteria (float, RVOL, news) and tags them `relaxed`. The
price/change/volume/exchange core is never relaxed. The board shows the tag and
which criteria each relaxed name missed.

---

## Two things this data is not

**Relative volume is time-of-day normalized.** TradingView compares volume so
far today against the average traded *by this same clock time* over the past 10
sessions — not today's total against an N-day average total. Before the open it
is comparing two thin pre-market windows, so it reads high and noisy, and only
converges toward a full-day RVOL near the close.

**Nothing checks for news.** There is no free bulk news source behind this
screen, so the catalyst criterion never actually runs: every row carries
`news_checked: false` and lands as `relaxed` rather than `strict`. That is the
honest state of things — "unknown", not "no catalyst". Pass `--no-news` to drop
the criterion and see the structural qualifiers as `strict`.

**And the pre-open board is partly stale by design.** TradingView has no "no
trades yet" signal: it always answers with the most recent session. At 07:30 ET
the names already gapping on real pre-market volume are live, and the quieter
ones still show yesterday's numbers. The scan detects this
(`warrior_screener/market_calendar.py`), writes the caveat into
`data/today.json`, and the page prints it as a banner.

---

## Running it yourself

Requires **Python 3.10+** and **Node 18.18+**. No API key: the screen calls
TradingView's public scanner endpoint (`scanner.tradingview.com`), the same
JSON call `tradingview.com/screener` makes.

```bash
pip install -r requirements.txt

python -m warrior_screener snapshot                    # print the board
python -m warrior_screener snapshot --no-news          # structural qualifiers as strict
python -m warrior_screener snapshot --json             # write data/today.json

# one archive slot, carrying forward anything already recorded today
python -m warrior_screener snapshot \
  --slot t_plus_10 --archive history/2026/09/09/t_plus_10.csv \
  --carry-from history/2026/09/09
```

Then the dashboard:

```bash
npm install
npm run screen     # refresh data/today.json
npm run dev        # http://localhost:3000
```

`npm run build` produces a static export in `out/`.

> **That endpoint is unofficial and undocumented.** It is reverse-engineered,
> with no published contract or SLA, and could change or start blocking
> non-browser traffic without notice. Treat it as a convenience, not
> infrastructure. When it is unreachable the screen exits non-zero and nothing
> is committed, so the board keeps showing the last good scan rather than
> going blank.

### Tuning the screen

Every threshold is in `config/criteria.yml`, and the ones worth changing often
are also CLI flags:

```bash
python -m warrior_screener snapshot --max-float 20000000 --min-change 20
python -m warrior_screener snapshot --max-float 0        # disable the float filter
python -m warrior_screener snapshot --strict-only        # never pad to min_in_play
```

---

## The daily refresh

`.github/workflows/refresh.yml` runs the screen four times a trading day, all
times Eastern:

| slot | time | what it is for |
|------|------|----------------|
| `pre_open` | 09:25 | the watchlist five minutes before the bell |
| `t_plus_5` | 09:35 | five minutes in, where the momentum declares itself |
| `t_plus_10` | 09:40 | ten minutes in; the view the page opens on |
| `close` | 15:55 | five minutes before the bell, to label how the day ended |

All four publish to the dashboard as **separate views**, switchable from the
tab row. They are kept apart rather than merged because their numbers are not
comparable: before 09:30 the gap column is meaningless (TradingView's `open` is
not yet today's) and thinly traded names still carry yesterday's figures, while
a 900× relative volume at 09:35 means something quite different from 900× at
15:55. Each view carries a caption saying what it is, and the pre-open one says
plainly that it is a watchlist rather than prices.

The page opens on `t_plus_10`, falling back to the most recent slot available —
so before 09:40 you land on the pre-open board rather than on an empty tab.

### Why the schedule looks the way it does

GitHub cron is UTC-only, so each slot needs two entries — one for EDT, one for
EST — of which exactly one may run. And GitHub's scheduler runs late under
load, sometimes past ten minutes, which matters when slots sit five minutes
apart: reading the clock would relabel a delayed 09:35 run as the 09:40 one.

So the slot comes from `github.event.schedule` — the cron expression that
fired — and is exact regardless of delay. The clock is used only to reject the
wrong-timezone twin, which is always a full hour out. That logic lives in
`warrior_screener/slots.py` rather than in the YAML precisely so it can be
tested: `tests/test_slots.py` asserts that every slot fires exactly once per
day in both halves of the year, which is the property that would otherwise
break silently at each daylight-saving switch.

Market holidays are skipped via `market_calendar.is_trading_day`. **Update
`US_MARKET_HOLIDAYS` every January** — there is no free unauthenticated bulk
calendar API, so the list is hardcoded, and a date past its coverage falls back
to "any weekday is a trading day".

Scheduled workflows only run from the repository's default branch. To trigger a
slot by hand, use the Actions tab: `workflow_dispatch` takes the slot name and
skips every schedule check.

---

## The research archive

Every slot also writes a CSV to the **`data` branch** — an orphan branch with
no shared history, so Vercel never clones it and the site's build time stays
flat as the dataset grows.

```
history/YYYY/MM/DD/<slot>.csv
```

The branch's own README documents the full column contract. Three properties
matter if you are going to model on it:

**Tickers are carried across slots.** Each file holds the candidates found at
that instant *plus* every ticker that qualified earlier the same day, even once
it no longer passes the gate. Without that, a name that ran 60% at 09:35 and
faded by the close would be absent from the closing file — and a morning
feature with no closing figure cannot become a label. Carried rows are marked
`qualification = carried` and carry no score, since the score is a percentile
rank inside a live pool they are no longer part of.

**Both the intended slot and the real timestamp are recorded**, for the
scheduler-delay reason above.

**Only screened candidates are archived**, not the whole universe. That is
enough to study how already-selected momentum names behave through a session,
and not enough to learn the selection itself — there are no negative examples.
Widening it to the full ~6,000-row universe is a one-line change in the
workflow.

---

## The dashboard

`data/today.json` holds the day's slots side by side:

```json
{
  "trade_date": "2026-09-10",
  "updated_at": "2026-09-10T09:40:03-04:00",
  "criteria": { "...": "the thresholds this scan used" },
  "slots": {
    "pre_open":  { "generated_at": "...", "session_phase": "pre-market", "in_play": [] },
    "t_plus_10": { "generated_at": "...", "session_phase": "regular",    "in_play": [] }
  }
}
```

Each run merges its own slot and leaves the others alone. When the file is from
an earlier session it is replaced wholesale rather than merged — yesterday's
pre-open board sitting in a tab next to today's would be indistinguishable from
a live one.

The page is a static export, so every label on it describes the moment the scan
ran, not the moment you are reading it. `app/StaleNotice.tsx` closes that gap:
it compares the payload's date against the browser's own Eastern date on mount
and, if they differ, says so in red above the board. Without it, opening the
site at 08:00 would show yesterday's closing board wearing a green "Open"
badge.

---

## Deploying

The site is a Next.js static export. `vercel.json` pins
`"framework": "nextjs"`, which matters here: this repository was Python-only
when its Vercel project was first created, so the project's saved preset is
`python` and the build fails with *"No python entrypoint found"* until
something overrides it. Settings in `vercel.json` take precedence over the
dashboard preset, so the fix travels with the repo instead of living in one
person's project settings.

Beyond that, importing the repository is the whole setup. Every push that
changes `data/today.json` triggers a redeploy, which is how the board stays
current without a server.

---

## Project layout

```
warrior_screener/
  cli.py               `snapshot`, the table, and the JSON the dashboard reads
  live_snapshot.py     TradingView rows -> scored candidates
  scanner.py           the screen itself: filters, scoring, selection (pure)
  config.py            criteria + settings, loaded from config/criteria.yml
  market_calendar.py   is the market open, and is this data actually today's
  models.py            Candidate and friends
  archive.py           the per-slot research CSV, and the carry-forward rule
  slots.py             which measurement a scheduled run is, and whether it runs
  providers/
    tradingview.py     the one HTTP call, and its column mapping
app/                   the dashboard
  page.tsx             server component: masthead, criteria, footnotes
  Board.tsx            client: the slot tabs and the table
  StaleNotice.tsx      client: "these are not today's numbers"
lib/format.ts          types, slot captions and formatters (browser-safe)
lib/screener.ts        reads data/today.json at build time (server only)
data/today.json        the committed scan the page renders
```

`scanner.py` does no I/O: it takes candidates somebody else built and applies
the filters, the score and the selection. That is what lets the same screen
definition run against a different data source without touching it.

---

## Known data limitation: float vs. shares outstanding

No mainstream free API publishes true **free float**. This uses TradingView's
float figure, which for most small caps is shares outstanding or close to it,
and therefore **overstates** float on exactly the names this screen targets
(insider and locked-up shares included). The float filter is consequently
*conservative*: it rejects some genuine low-float runners, and never invents
one.

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest          # 150 tests, no network, no API key
ruff check .
```

The suite builds TradingView rows directly (`tests/test_live_snapshot.make_row`),
so nothing in it touches the live endpoint.

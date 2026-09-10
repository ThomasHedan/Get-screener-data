# Warrior Trading Screener

A dashboard of the low-float momentum stocks that are **in play** — the Ross
Cameron / Warrior Trading gap-and-go profile — screened from the whole US
market in a single request, rescanned every few minutes through the session
and served from three containers you can run anywhere.

The screen is Python (`warrior_screener/`). The board is a Next.js page
(`app/`). Between them is a shared volume: the extractor publishes
`today.json` on a schedule it controls, and the board reads it on every
request. See [DEPLOY.md](DEPLOY.md).

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

## The refresh loop

`warrior_screener/scheduler.py` screens the market every
`SCAN_INTERVAL_SECONDS` (default 300) whenever a US session is live —
pre-market 04:00 ET through after-hours 20:00 ET, trading days only — and
publishes each result to `SCREENER_DATA_DIR`: `today.json` for the board, plus
a timestamped copy under `history/` so a session can be replayed.

It replaced a GitHub Actions cron for a reason worth recording. That schedule
was best-effort: on 2026-09-10 a correctly configured, active workflow never
fired once, and since a commit was the only path to fresh data, the board
showed the previous session with nothing to indicate anything had gone wrong.
A scheduler that owns its own clock removes that whole class of failure — a
missed scan now costs one scan, and the next tick is five minutes away.

`.github/workflows/refresh.yml` survives as `workflow_dispatch` only: a manual
escape hatch that no longer competes with the extractor for `data/today.json`.

**Update `US_MARKET_HOLIDAYS` every January** in
`warrior_screener/market_calendar.py`. There is no free unauthenticated bulk
calendar API, so the list is hardcoded; a date past its coverage falls back to
"any weekday is a trading day".

---

## Deploying

Three containers — `extractor`, `web`, `caddy` — on a private network, with
Caddy the only one publishing a port:

```bash
cp .env.example .env      # set SCREENER_DOMAIN
docker compose up -d --build
```

Full instructions, including Oracle Cloud Always Free and GCP `e2-micro`
(and the host-firewall step on Oracle that silently drops 80/443), are in
[DEPLOY.md](DEPLOY.md).

The board no longer depends on a rebuild to show a new scan, so a deploy is
only needed when the code changes.

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
  providers/
    tradingview.py     the one HTTP call, and its column mapping
app/                   the dashboard (page.tsx, layout.tsx, globals.css)
lib/screener.ts        the payload's TypeScript shape and formatting helpers
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
pytest          # 111 tests, no network, no API key
ruff check .
```

The suite builds TradingView rows directly (`tests/test_live_snapshot.make_row`),
so nothing in it touches the live endpoint.

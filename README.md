# Warrior-Trading Screener & Intraday Archive

Screens the whole US equity market every session for the handful of low-float
momentum stocks that are **in play** — the Ross Cameron / Warrior Trading
gap-and-go profile — and archives an immutable snapshot of the scan plus
1-minute intraday bars for the names it picks.

The point of running it daily is the archive. A screen you can only run on
today's data tells you nothing about strategy; a screen whose output is written
down every day, and never rewritten, gives you a **survivorship-bias-free**
panel of exactly the stocks a momentum trader would have been watching, including
the ones that were later delisted, reverse-split into oblivion, or acquired.

---

## The screen

Defaults implement the criteria Ross Cameron publicly describes:

| # | Criterion | Default | Config key |
|---|-----------|---------|------------|
| 1 | Up on the day | **≥ 10%** vs. previous close | `min_change_pct` |
| 2 | Price | **$1 – $20** | `min_price` / `max_price` |
| 3 | Relative volume | **≥ 5×** the 20-day average | `min_relative_volume` |
| 4 | Float | **< 10M shares** | `max_float_shares` |
| 5 | News catalyst | **≥ 1 headline** since the prior close | `require_news_catalyst` |

Plus universe hygiene: common stock and ADRs only, major exchanges only,
warrants/rights/units excluded, and a `min_day_volume` floor (500k shares) so
untradeable names never reach the list.

### Ranking: which 5–10?

Every candidate that clears the filters gets a composite score in `[0, 1]`:

```
score = 0.40 · rank(relative volume)
      + 0.30 · rank(% change)
      + 0.20 · rank(−float)          # smaller float ranks higher
      + 0.10 · min(news, 3) / 3
```

The components are **percentile ranks within that day's own candidate pool**,
not absolute values. That keeps scores comparable between a sleepy Tuesday and a
small-cap frenzy, and stops one 400×-RVOL outlier from flattening everything
else. All weights live in `config/criteria.yml`, and every raw input is stored
alongside the score so you can re-rank however you like later.

### Strict vs. relaxed picks

Some sessions genuinely do not have five stocks with a sub-10M float running 5×
volume on a catalyst. Rather than return two names or invent three, the selector
tops the list up to `min_in_play` with the best names that fail *only* the
relaxable criteria (float, RVOL, news) and tags them `qualification=relaxed`.
The price/change/volume/exchange core is never relaxed.

**For quant work, filter to `qualification == "strict"` unless you have a reason
not to.** Set `fill_to_min: false` (or `--strict-only`) to switch padding off.

---

## Quick start

```bash
pip install -r requirements.txt
export POLYGON_API_KEY=...                    # https://polygon.io

python -m warrior_screener collect            # screen today, archive everything
python -m warrior_screener show               # print today's in-play list
python -m warrior_screener status             # archive coverage
```

Build history immediately instead of waiting a year for it:

```bash
python -m warrior_screener backfill --start 2025-01-02 --end 2026-08-31
```

Backfill walks oldest-first so each day's RVOL window is already cached when the
next day needs it, and a failed session is logged and skipped rather than
aborting the range. Re-running is idempotent: archived sessions are skipped
unless you pass `--force`.

### Run it every day

```cron
CRON_TZ=America/New_York
15 17 * * 1-5 /path/to/repo/scripts/run_daily.sh >> /path/to/repo/logs/cron.log 2>&1
```

17:15 ET is ~75 minutes after the close, by which point consolidated volume has
settled. On Polygon's free tier same-day data may not be published until the
next morning — if `collect` finds nothing, run it the following morning with
`--date yesterday`.

---

## Data provider

[Polygon.io](https://polygon.io) is the default, for two reasons:

1. `/v2/aggs/grouped` prices the **entire** US equity universe for one session in
   a single request, so a daily scan costs ~1 API call plus enrichment rather
   than thousands.
2. Its historical endpoints keep serving tickers **after they are delisted**,
   which is the whole reason this archive can avoid survivorship bias.

**It works on the free tier** (5 requests/minute, end-of-day data). One session
costs roughly: 1 grouped call + up to `max_enrich` reference lookups + a news
lookup per surviving candidate + one call per in-play ticker for minute bars —
about 50 calls, so ~15–25 minutes of wall clock at 5 rpm. Reference data is
cached for 30 days, so a long backfill gets much cheaper as it goes. On a paid
plan, raise `requests_per_minute` and it finishes in seconds.

To use a different vendor, implement the four methods of
`warrior_screener.providers.base.MarketDataProvider` and wire it into
`cli._make_provider`. Nothing else in the codebase knows about Polygon.

### Known data limitation: float vs. shares outstanding

No mainstream API publishes true **free float**. The screener uses Polygon's
`share_class_shares_outstanding` as the proxy, which **overstates** float on
exactly the small caps this screen targets (insider and locked-up shares are
included). Consequences: the float filter is *conservative* — it will reject some
genuine low-float runners, and never invents one.

To fix it per ticker, drop a CSV at `data/reference/float_overrides.csv`:

```csv
ticker,float_shares
ABCD,3200000
WXYZ,850000
```

Those values take precedence over the provider's number. The
`shares_outstanding` column is always preserved separately, so you can see which
rows were overridden.

---

## What gets stored

```
data/
├── daily_bars/2026-08-28.csv        full-market OHLCV — the RVOL window, and a
│                                    complete daily archive in its own right
├── scans/2026-08-28/
│   ├── candidates.csv               every evaluated candidate + why each failed
│   └── in_play.csv                  the 5–10 selected names
├── intraday/2026-08-28/ABCD.csv     1-minute bars, 04:00–20:00 ET
├── features/2026-08-28.csv          one flat feature row per in-play ticker
├── reference/
│   ├── tickers.jsonl                append-only reference snapshots
│   ├── cache.json                   TTL-bounded lookup cache
│   └── float_overrides.csv          your hand-checked floats (optional)
├── in_play_history.csv              every in-play row ever selected, one file
└── runs.jsonl                       one record per run — audit your gaps
```

Everything is plain CSV/JSONL written with the standard library, and every write
is atomic (temp file + rename) so an interrupted run never leaves a half-written
partition. The collector deliberately does **not** import pandas: a cron job that
must run 252 times a year should not break because a binary wheel stopped
matching the interpreter.

`candidates.csv` keeps the **near-misses**, not just the winners, with a
`rejected_by` column naming each failed criterion. That is what lets you ask
later whether the 10M float cut-off was actually the right one.

### Feature columns

`features/<date>.csv` joins the screen's verdict to the intraday behaviour:

- **Screen**: `score`, `qualification`, `gap_pct`, `change_pct`,
  `relative_volume`, `float_shares`, `news_count`
- **Pre-market**: `premarket_high` / `_low` / `_volume`, `premarket_high_time`,
  `premarket_change_pct`
- **Regular session**: `open`, `high`, `low`, `close`, `high_time`, `low_time`,
  `minutes_to_high`, `rth_volume`, `rth_vwap`
- **Move shape**: `open_to_high_pct`, `open_to_close_pct`, `close_vs_high_pct`
  (how much of the move it gave back), `first_5min_range_pct`
- **Volume distribution**: `first_30min_volume_share`, `first_hour_volume_share`,
  `max_minute_volume`, `big_volume_minutes` (minutes over 100k shares)
- **Microstructure**: `avg_minute_range_pct`, `minutes_traded`,
  `untraded_minutes` (a proxy for LULD halts, which these names hit often)

### A note on `news_checked`

News lookups cost an API call, so they are skipped for candidates already
rejected on structure. Rows carry `news_checked` to say whether the lookup ran:
**`news_count == 0` only means "no catalyst" when `news_checked` is true.**
Reject reasons distinguish `news` (looked, found nothing) from `news_unknown`
(never looked).

---

## Research

```python
from warrior_screener.dataset import load_features, load_intraday, load_in_play_history

features = load_features("data", strict_only=True)  # one row per ticker per day
history = load_in_play_history("data")  # every selection ever made
bars = load_intraday("data", "2026-08-28", "ABCD")  # minute bars, indexed by time

# e.g. does the high of day come in the first 15 minutes?
early = features[features["minutes_to_high"] <= 15]
print(len(early) / len(features))
```

`pandas` is only needed for `warrior_screener.dataset` — install it with
`pip install 'warrior-screener[research]'`.

### Re-tuning the criteria without spending API calls

`rescan` re-runs the screen against the cached archive, offline, writing nothing:

```bash
python -m warrior_screener rescan --date 2026-08-28 --max-float 20000000
python -m warrior_screener rescan --date 2026-08-28 --min-rvol 3 --no-news
```

It reuses the reference and news data recorded by the original scan, so the
selections are directly comparable. `--max-float 0` disables the float filter
entirely.

---

## Configuration

`config/criteria.yml` holds every threshold; each is also a CLI flag. Precedence
is defaults → YAML → environment (`POLYGON_API_KEY`, `SCREENER_DATA_DIR`) → CLI.
Unknown keys are rejected loudly rather than ignored, so a typo in the YAML can
never silently run a different screen than you think.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest          # 115 tests, no network and no API key required
python -m ruff check .
python -m ruff format .
```

The whole pipeline is testable offline: `tests/conftest.py` provides a fake
provider that serves a synthetic market, and the scanner and feature extraction
are pure functions over it.

## Caveats

- **This is a data pipeline, not a trading system.** It reproduces the screen and
  records what happened; it takes no position on entries, exits or sizing.
- **The screen is end-of-day.** It reconstructs which stocks *were* in play using
  the completed session, which is the right basis for research but is not the
  live 09:20 ET pre-market scanner a discretionary trader watches.
- **Float is a proxy** (see above), and RVOL is undefined for tickers with fewer
  than 3 prior sessions — freshly listed names are reported as unknown rather
  than assigned a fabricated multiple.
- **Split adjustment.** Provider prices are split-adjusted as of the moment they
  are fetched, so cached bars drift out of alignment once a ticker splits — and
  these small caps reverse-split constantly to hold listing compliance. The
  previous session is therefore re-fetched whenever its cached copy is more than
  a day old, which keeps `gap_pct` and `change_pct` correct (without it, a
  routine 1:10 reverse split reads as a +900% gapper). The *older* bars in the
  RVOL window are not re-fetched, so `relative_volume` can be off by the split
  factor for a ticker that split inside the lookback. If that matters to you,
  delete `data/daily_bars/*.csv` periodically to force a clean re-fetch — the
  scans and intraday bars are unaffected.

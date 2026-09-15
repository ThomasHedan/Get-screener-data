# Warrior Screener

A knowledge base of the low-float momentum names **in play** — the Ross Cameron
gap-and-go profile — screened from the whole US market every few minutes and
kept, so the session can be read back later.

TradingView's screener has no notion of a past instant: it always answers about
the live session. "What was in play at 09:35" therefore has an answer only if
something recorded it at 09:35. That recording is the point of this project.

```
db ─── postgres, every capture ever taken
 ├── scraper   screens on its own clock, writes
 └── web       reads the newest capture, renders the board
```

Only `web` publishes a port.

## Install

On Ubuntu Server 24.04 (or any Docker host):

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker

git clone https://github.com/ThomasHedan/Get-screener-data.git
cd Get-screener-data
cp .env.example .env        # set POSTGRES_PASSWORD
docker compose up -d --build
```

The board is on port 80. The first capture lands on the next tick, and only
while a US session is live — pre-market 04:00 ET through after-hours 20:00 ET,
trading days only. Outside those hours the scraper makes no network call, which
is correct rather than broken.

## Operate

```bash
docker compose logs -f scraper     # capture by capture, with the names selected
docker compose ps                  # db reports healthy/unhealthy
docker compose exec db psql -U screener screener
```

```bash
# Back up the knowledge base
docker compose exec -T db pg_dump -U screener screener | gzip > screener-$(date +%F).sql.gz

# Restore
gunzip -c screener-2026-09-15.sql.gz | docker compose exec -T db psql -U screener screener
```

## The corpus

The screen discards about 99% of what the request already returns — the whole
market arrives on the wire either way, filtering happens afterwards in Python.
`snapshot` keeps all of it, because a model trained only on what was selected
never sees the negative class.

Each row carries the core measures as typed columns and everything else —
RSI, `Perf.W`, `premarket_change`, moving averages, fundamentals — as JSONB
under TradingView's own names:

```sql
SELECT symbol, close, (extra->>'RSI')::real AS rsi
FROM snapshot
WHERE trade_date = current_date AND (extra->>'RSI')::real > 90;
```

**The column names in `EXTENDED_COLUMNS` are not verified.** TradingView's
scanner is undocumented and rejects the whole request on an unknown column, so
the scan asks for core + extended and falls back to the 15 core columns alone if
refused — a wrong guess costs the extra data, never the scan. Find out which are
real, once, on a machine that can reach the endpoint:

```bash
docker compose exec scraper python -m warrior_screener.probe_columns
```

It prints a ready-to-paste `EXTENDED_COLUMNS`. Exit code 2 means the endpoint is
unreachable and the results are meaningless — a broken network must not read as
a bad column list.

### What it costs

Measured, not estimated: 10,700 symbols per capture, 446 bytes a row.

| | |
|---|---|
| One capture | ~4.7 MB, written in 0.2 s |
| A day (192 captures) | ~870 MB |
| A month | ~18 GB |
| A year | ~215 GB |

Two levers. `STORE_MARKET_EVERY=3` in `.env` thirds it without making the board
any staler. And **66% of each row is the JSONB** — mostly key names, repeated
on every row — so promoting the keys that survive `probe_columns` into real
columns cuts storage by roughly three.

`snapshot` is partitioned by month, so retiring history is instant rather than a
`DELETE` that leaves the table bloated:

```sql
DROP TABLE snapshot_2026_09;
```

Partitions are created on demand by the scraper, which needs the app user to own
the tables — it does, because the postgres image applies `db/schema.sql` as
`POSTGRES_USER` on first start.

## Read the data

Four tables and two views. `board` is the newest capture; `intraday` is every
capture of every *selected* symbol, which is the one to query when asking
whether a criterion actually predicts anything:

```sql
-- Did names that gapped down keep going lower?
SELECT symbol,
       min(close)  FILTER (WHERE phase = 'pre-market') AS pre,
       max(close)  FILTER (WHERE phase = 'regular')    AS regular,
       max(gap_pct)                                     AS gap
FROM intraday
WHERE trade_date = current_date
GROUP BY symbol
ORDER BY gap;
```

Columns carry their caveats as SQL comments — `\d+ in_play` in psql, or
`COMMENT ON` in `db/schema.sql`.

## Tune the screen

Every threshold is in `scraper/config/criteria.yml`, a flat mapping. An unknown
key is an error, not a silent no-op — a typo would otherwise leave the screen
running on a default you believe you changed.

```bash
docker compose restart scraper      # after editing
```

## Develop

```bash
cd scraper && pip install -r requirements.txt -r requirements-dev.txt && pytest
cd web && npm install && npm run dev
```

## Two things the data is not

**Relative volume is time-of-day normalised** — volume so far today against the
average traded by this clock time over the past 10 sessions, not a full-session
ratio. Four digits just after the open is correct, and it is not comparable to a
full-day RVOL.

**Nothing checks for a news catalyst.** No free bulk news source backs this
screen, so every name lands `relaxed`. That is "unknown", not "no catalyst" —
and an empty `gap_pct` means it could not be computed, not that the name did not
gap. The screen is careful never to turn either into a verdict.

The TradingView endpoint is undocumented and unauthenticated. It is a fast, free
path for a live check, not something with an SLA.

-- Warrior screener: one capture of the live market, and the names it selected.
--
-- Shape: a narrow fact header (scan) with its rows (in_play), and a slowly
-- changing dimension (ticker) so a symbol's exchange and sector are stored
-- once rather than on every row of every capture. A session produces ~100
-- captures, so that difference compounds quickly.
--
-- Applied by the postgres image on first start; it runs once, against an empty
-- data directory. To change it afterwards, write a migration -- editing this
-- file does nothing to a cluster that already exists.

CREATE TYPE session_phase AS ENUM ('closed', 'pre-market', 'regular', 'after-hours');

-- Only selected names are stored, so 'rejected' is not a value here: a
-- candidate the screen turned down never reaches this database.
CREATE TYPE qualification AS ENUM ('strict', 'relaxed');


CREATE TABLE ticker (
    symbol         text PRIMARY KEY,
    exchange       text        NOT NULL,
    security_type  text        NOT NULL,
    sector         text,
    first_seen     timestamptz NOT NULL DEFAULT now(),
    last_seen      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE ticker IS
    'One row per symbol ever selected. Attributes are overwritten on each scan: '
    'a symbol changing sector or exchange is rare and the current value is the '
    'one the board renders.';


CREATE TABLE scan (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    captured_at    timestamptz   NOT NULL UNIQUE,
    trade_date     date          NOT NULL,
    phase          session_phase NOT NULL,
    universe_rows  integer       NOT NULL CHECK (universe_rows >= 0),
    notice         text
);

COMMENT ON TABLE scan IS
    'One capture of the whole US market. Rows are immutable: a scan records what '
    'the screen saw at captured_at, and is never corrected afterwards.';
COMMENT ON COLUMN scan.trade_date IS
    'Eastern trading day. Distinct from captured_at::date because a capture at '
    '20:00 ET still belongs to that day''s session.';
COMMENT ON COLUMN scan.notice IS
    'Why these figures may not be live -- market closed, pre-market, after-hours. '
    'NULL means the capture was taken during regular trading.';

-- The board reads the newest scan; analysis reads one day in order. Both are
-- this index.
CREATE INDEX scan_trade_date_captured_at_idx ON scan (trade_date, captured_at DESC);


CREATE TABLE in_play (
    scan_id          bigint        NOT NULL REFERENCES scan (id) ON DELETE CASCADE,
    symbol           text          NOT NULL REFERENCES ticker (symbol),
    close            numeric(12, 4) NOT NULL CHECK (close > 0),
    change_pct       numeric(9, 2),
    gap_pct          numeric(9, 2),
    open_change_pct  numeric(9, 2),
    range_position   numeric(4, 3) CHECK (range_position BETWEEN 0 AND 1),
    relative_volume  numeric(12, 2),
    volume           bigint        NOT NULL CHECK (volume >= 0),
    average_volume   bigint        CHECK (average_volume IS NULL OR average_volume >= 0),
    float_shares     bigint        CHECK (float_shares IS NULL OR float_shares > 0),
    market_cap       numeric(20, 2),
    score            numeric(6, 4) NOT NULL CHECK (score BETWEEN 0 AND 1),
    qualification    qualification NOT NULL,
    PRIMARY KEY (scan_id, symbol)
);

COMMENT ON COLUMN in_play.gap_pct IS
    'Open vs. previous close. NULL means not computable -- typically pre-open, '
    'before the symbol has a regular-session open -- and never "did not gap". '
    'Filtering must not treat the two as the same thing.';
COMMENT ON COLUMN in_play.open_change_pct IS
    'Move since the regular-session open, with the overnight gap excluded. At '
    'the 09:35 capture this is the first five minutes; change_pct is not, since '
    'it measures from the previous close. NULL means not computable (pre-open, '
    'where TradingView reports open as 0), never "did not move".';
COMMENT ON COLUMN in_play.range_position IS
    'Where the last price sat in the day''s range: 0 on the low, 1 on the high. '
    'NULL when high = low. A high change_pct at a low range_position is a mover '
    'giving the move back.';
COMMENT ON COLUMN in_play.relative_volume IS
    'Time-of-day normalized: volume so far today against the average traded by '
    'this clock time over the past 10 sessions. Four digits just after the open '
    'is correct, not an error, and it is not comparable to a full-session RVOL.';
COMMENT ON COLUMN in_play.qualification IS
    '''strict'' passed every criterion; ''relaxed'' was filled in to reach the '
    'minimum board size with some criteria dropped. No free news source backs '
    'the live path, so in practice everything lands ''relaxed''.';

-- Drives the per-symbol intraday trail on the board.
CREATE INDEX in_play_symbol_scan_idx ON in_play (symbol, scan_id DESC);


-- The board: the newest capture, joined to what it selected.
CREATE VIEW board AS
SELECT
    s.id AS scan_id, s.captured_at, s.trade_date, s.phase, s.universe_rows, s.notice,
    p.symbol, t.exchange, t.sector,
    p.close, p.change_pct, p.gap_pct, p.open_change_pct, p.range_position,
    p.relative_volume, p.volume,
    p.average_volume, p.float_shares, p.market_cap, p.score, p.qualification
FROM scan s
JOIN in_play p ON p.scan_id = s.id
JOIN ticker  t ON t.symbol = p.symbol
WHERE s.id = (SELECT id FROM scan ORDER BY captured_at DESC LIMIT 1);

-- Every capture of every symbol, in order: the sparkline behind each row, and
-- the table to query when asking whether a criterion actually predicts anything.
CREATE VIEW intraday AS
SELECT s.trade_date, s.captured_at, s.phase, p.symbol,
       p.close, p.change_pct, p.gap_pct, p.open_change_pct, p.range_position,
       p.relative_volume, p.volume, p.score
FROM in_play p
JOIN scan s ON s.id = p.scan_id;


-- ---------------------------------------------------------------- the corpus
--
-- Every row of every capture, not just the ones the screen selected. The screen
-- discards ~99% of what the request already returns; this keeps it, because
-- training on "what was in play" alone teaches nothing about what was not.
--
-- Scale is the design constraint. ~10,700 symbols x a capture every 5 minutes
-- across a 16-hour extended session is ~2 million rows a day, ~500 million a
-- year. Hence: real (4 bytes) rather than numeric for the measures -- this is a
-- research corpus, not a ledger, and ~7 significant digits is well past what a
-- feature needs -- and RANGE partitioning on trade_date so retiring a month is
-- a DROP TABLE rather than a DELETE that leaves the table bloated.
--
-- Partitions are created on demand by the scraper; see db.ensure_partition.
CREATE TABLE snapshot (
    trade_date       date   NOT NULL,
    scan_id          bigint NOT NULL REFERENCES scan (id) ON DELETE CASCADE,
    symbol           text   NOT NULL,
    exchange         text   NOT NULL,
    security_type    text   NOT NULL,
    sector           text,
    open             real,
    high             real,
    low              real,
    close            real,
    change_pct       real,
    volume           bigint,
    relative_volume  real,
    average_volume   real,
    market_cap       real,
    float_shares     real,
    extra            jsonb,
    PRIMARY KEY (trade_date, scan_id, symbol)
) PARTITION BY RANGE (trade_date);

COMMENT ON TABLE snapshot IS
    'The whole market at each capture -- the training corpus. in_play holds the '
    'screen''s verdict on the handful it selected; this holds everyone, selected '
    'or not, which is what makes the negative class learnable.';
COMMENT ON COLUMN snapshot.extra IS
    'Every TradingView column beyond the core set, keyed by TradingView''s own '
    'name (RSI, Perf.W, premarket_change, ...). Absent values are omitted rather '
    'than stored as null. Promote a key to a real column once it has proved '
    'itself worth indexing.';
COMMENT ON COLUMN snapshot.close IS
    'real, not numeric: 4 bytes across ~500M rows a year, and ~7 significant '
    'digits is far past what a model feature resolves. The board reads in_play, '
    'which keeps exact numeric.';

-- Per-symbol history is the access pattern a training set is built on; the
-- primary key orders by date first and cannot serve it.
CREATE INDEX snapshot_symbol_idx ON snapshot (symbol, trade_date);

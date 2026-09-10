import {
  formatCompact,
  formatEastern,
  formatMultiple,
  formatNumber,
  formatPercent,
  loadScreener,
  PHASE_LABELS,
  type InPlayRow,
} from "@/lib/screener";

// Read the extractor's latest scan on every request. Caching here would be
// the one thing the board must not do: someone checking the screen five
// minutes after the open needs what the last scan found, not what was cached
// before it ran. The read is one small local JSON file.
export const dynamic = "force-dynamic";

export default async function Page() {
  const board = await loadScreener();
  const generated = formatEastern(board.generated_at);
  const phase = board.session_phase;
  const strict = board.in_play.filter((row) => row.qualification === "strict").length;

  return (
    <main className="page">
      <header className="masthead">
        <div className="masthead-title">
          <h1>Warrior Trading Screener</h1>
          <p className="tagline">
            Low-float momentum names in play — the whole US market, screened in one pass.
          </p>
        </div>
        <div className="masthead-meta">
          {phase ? <span className={`phase phase-${phase}`}>{PHASE_LABELS[phase]}</span> : null}
          <span className="stamp">
            {generated ? `Scanned ${generated} ET` : "Awaiting the first scan"}
          </span>
        </div>
      </header>

      {board.notice ? (
        <p className="notice" role="status">
          {board.notice}
        </p>
      ) : null}

      <section className="criteria" aria-label="Screen criteria">
        <Chip label="Change" value={`≥ ${formatNumber(board.criteria.min_change_pct, 0)}%`} />
        <Chip
          label="Price"
          value={`$${formatNumber(board.criteria.min_price, 0)}–$${formatNumber(
            board.criteria.max_price,
            0,
          )}`}
        />
        <Chip label="RVOL" value={`≥ ${formatMultiple(board.criteria.min_relative_volume)}`} />
        <Chip
          label="Float"
          value={
            board.criteria.max_float_shares
              ? `< ${formatCompact(board.criteria.max_float_shares)}`
              : "any"
          }
        />
        <Chip label="Volume" value={`≥ ${formatCompact(board.criteria.min_day_volume)}`} />
        <Chip label="Catalyst" value={board.criteria.require_news_catalyst ? "required" : "off"} />
      </section>

      {board.in_play.length > 0 ? (
        <Board rows={board.in_play} />
      ) : (
        <EmptyState placeholder={board.placeholder} />
      )}

      <section className="stats" aria-label="Scan statistics">
        <Stat label="Tickers scanned" value={formatCompact(board.stats.universe_rows)} />
        <Stat label="Cleared price/volume" value={formatCompact(board.stats.coarse_candidates)} />
        <Stat label="Strict qualifiers" value={formatCompact(board.stats.strict_qualifiers)} />
        <Stat label="On the board" value={`${board.in_play.length} (${strict} strict)`} />
      </section>

      <footer className="footnotes">
        <p>
          <strong>Strict</strong> names clear every criterion. <strong>Relaxed</strong> names fail
          only float, RVOL or the catalyst check and are shown to fill the board out to five — the
          badge says which criteria they missed. For research, use the strict rows.
        </p>
        <p>
          Two things this data is not. Relative volume from TradingView is{" "}
          <em>time-of-day normalized</em> — today&apos;s volume so far against the average traded by
          this same clock time over the past 10 sessions — so before the open it compares two thin
          pre-market windows and reads high. And no free bulk news source backs this screen, so the
          catalyst check never runs: every row is &ldquo;news unknown&rdquo;, which is why they land
          as relaxed rather than strict. Unknown is not the same as absent.
        </p>
        <p className="schedule">
          Refreshed automatically at 07:30 ET on weekdays, two hours before the open.
        </p>
      </footer>
    </main>
  );
}

function Board({ rows }: { rows: InPlayRow[] }) {
  return (
    <div className="board-scroll">
      <table className="board">
        <thead>
          <tr>
            <th scope="col" className="col-rank">
              #
            </th>
            <th scope="col">Ticker</th>
            <th scope="col" className="num">
              Last
            </th>
            <th scope="col" className="num">
              Change
            </th>
            <th scope="col" className="num">
              Gap
            </th>
            <th scope="col" className="num">
              RVOL
            </th>
            <th scope="col" className="num">
              Volume
            </th>
            <th scope="col" className="num">
              Float
            </th>
            <th scope="col" className="num">
              Mkt cap
            </th>
            <th scope="col" className="col-score">
              Score
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={row.ticker}>
              <td className="col-rank">{index + 1}</td>
              <th scope="row" className="col-ticker">
                {/* A flex container inside the cell, not the cell itself:
                    display:flex on a <th> drops it out of table layout and its
                    row border stops short of the others. */}
                <span className="ticker-stack">
                  <span className="ticker">{row.ticker}</span>
                  <span className={`qual qual-${row.qualification}`}>
                    {row.qualification === "relaxed" && row.rejected_by.length > 0
                      ? `relaxed · ${row.rejected_by.map(labelReason).join(", ")}`
                      : row.qualification}
                  </span>
                  {row.primary_exchange ? (
                    <span className="exchange">{row.primary_exchange}</span>
                  ) : null}
                </span>
              </th>
              <td className="num">${formatNumber(row.close)}</td>
              <td className={`num ${changeClass(row.change_pct)}`}>
                {formatPercent(row.change_pct)}
              </td>
              <td className={`num ${changeClass(row.gap_pct)}`}>{formatPercent(row.gap_pct)}</td>
              <td className="num strong">{formatMultiple(row.relative_volume)}</td>
              <td className="num">{formatCompact(row.volume)}</td>
              <td className="num">{formatCompact(row.float_shares)}</td>
              <td className="num">{formatCompact(row.market_cap)}</td>
              <td className="col-score">
                <div className="score">
                  <div className="score-track">
                    <div className="score-fill" style={{ width: `${clamp(row.score) * 100}%` }} />
                  </div>
                  <span className="score-value">{formatNumber(row.score, 2)}</span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EmptyState({ placeholder }: { placeholder?: boolean }) {
  return (
    <div className="empty">
      <p className="empty-head">
        {placeholder ? "No scan has run yet." : "Nothing is in play."}
      </p>
      <p>
        {placeholder
          ? "The board fills in when the refresh workflow runs the screener and commits data/today.json — or run `npm run screen` yourself."
          : "Not one name on the US market cleared the price, change and volume floors on this scan. On a quiet session that is the honest answer, not a bug."}
      </p>
    </div>
  );
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="chip">
      <span className="chip-label">{label}</span>
      <span className="chip-value">{value}</span>
    </span>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

/** Reject keys are terse on purpose in the data; spell them out on the page. */
function labelReason(reason: string): string {
  const labels: Record<string, string> = {
    float: "float",
    relative_volume: "RVOL",
    news: "no news",
    news_unknown: "news unknown",
    security_type: "security type",
    exchange: "exchange",
    market_cap: "market cap",
  };
  return labels[reason] ?? reason;
}

function changeClass(value: number | null): string {
  if (value === null || value === undefined) return "";
  return value >= 0 ? "up" : "down";
}

function clamp(score: number): number {
  return Math.max(0, Math.min(1, score));
}

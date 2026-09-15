import { loadBoard, type Row } from "@/lib/db";
import { compact, easternDate, easternTime, multiple, percent, price } from "@/lib/format";

// Read the newest capture on every request. Caching is the one thing this page
// must not do: someone checking five minutes after the open needs what the last
// scan found, not what was cached before it ran.
export const dynamic = "force-dynamic";

const PHASE_LABEL = {
  closed: "Closed",
  "pre-market": "Pre-market",
  regular: "Open",
  "after-hours": "After hours",
} as const;

/**
 * The session so far, as one line. Direction is coloured, but the change
 * percentage beside it carries the sign — colour never carries this alone.
 */
function Trail({ points }: { points: number[] }) {
  if (points.length < 2) return <span className="unknown">—</span>;

  const [width, height, pad] = [72, 22, 3];
  const low = Math.min(...points);
  const span = Math.max(...points) - low || 1;
  const step = width / (points.length - 1);
  const y = (value: number) => pad + (1 - (value - low) / span) * (height - pad * 2);
  const path = points.map((p, i) => `${i ? "L" : "M"}${(i * step).toFixed(1)},${y(p).toFixed(1)}`);
  const rising = points[points.length - 1] >= points[0];

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path
        d={path.join(" ")}
        fill="none"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        stroke={`var(--${rising ? "good" : "critical"})`}
      />
      <circle
        cx={width}
        cy={y(points[points.length - 1])}
        r="2.5"
        fill={`var(--${rising ? "good" : "critical"})`}
      />
    </svg>
  );
}

/** Colour follows the sign; the rendered value always carries it too. */
const tone = (value: number | null) =>
  value === null ? "unknown" : value < 0 ? "down" : "up";

function BoardRow({ row, rank }: { row: Row; rank: number }) {
  return (
    <tr>
      <td className="rank num">{rank}</td>
      <td className="left">
        <div className="symbol">{row.symbol}</div>
        {row.sector && <div className="sector">{row.sector}</div>}
      </td>
      <td className="left">
        <Trail points={row.trail} />
      </td>
      <td className="num">{price(row.close)}</td>
      <td className={`num ${tone(row.changePct)}`}>{percent(row.changePct)}</td>
      <td className={`num ${tone(row.gapPct)}`}>{percent(row.gapPct)}</td>
      <td className={`num ${tone(row.openChangePct)}`}>{percent(row.openChangePct)}</td>
      <td className="num">{multiple(row.relativeVolume)}</td>
      <td className="num">{compact(row.volume)}</td>
      <td className="num">{compact(row.floatShares)}</td>
      <td>
        <div className="score">
          <span className="score-track">
            <span className="score-fill" style={{ width: `${Math.round(row.score * 100)}%` }} />
          </span>
          <span className="num">{row.score.toFixed(2)}</span>
        </div>
      </td>
      <td>
        <span className="badge" data-strict={row.qualification === "strict"}>
          {row.qualification}
        </span>
      </td>
    </tr>
  );
}

export default async function Page() {
  const board = await loadBoard();

  return (
    <main className="page">
      <header className="masthead">
        <h1>Warrior Screener</h1>
        {board && (
          <div className="stamp">
            <span>
              {easternDate(board.capturedAt)} · <span className="num">{easternTime(board.capturedAt)}</span> ET
            </span>
            <span className="phase" data-live={board.phase === "regular"}>
              {PHASE_LABEL[board.phase]}
            </span>
          </div>
        )}
      </header>

      {board?.notice && <p className="notice">{board.notice}</p>}

      {!board ? (
        <p className="empty">No capture recorded yet. The scraper writes one every few minutes while a US session is live.</p>
      ) : (
        <>
          <dl className="tiles">
            <div className="tile">
              <dt>In play</dt>
              <dd className="num">{board.rows.length}</dd>
            </div>
            <div className="tile">
              <dt>Scanned</dt>
              <dd className="num">{board.universeRows.toLocaleString("en-US")}</dd>
            </div>
            <div className="tile">
              <dt>Captures today</dt>
              <dd className="num">{board.capturesToday}</dd>
            </div>
            <div className="tile">
              <dt>Top score</dt>
              <dd className="num">{(board.rows[0]?.score ?? 0).toFixed(2)}</dd>
            </div>
          </dl>

          <div className="scroller">
            <table>
              <thead>
                <tr>
                  <th />
                  <th className="left">Symbol</th>
                  <th className="left">Session</th>
                  <th>Close</th>
                  <th>Chg</th>
                  <th>Gap</th>
                  <th title="Move since the 09:30 open, overnight gap excluded">
                    Since open
                  </th>
                  <th>RVOL</th>
                  <th>Volume</th>
                  <th>Float</th>
                  <th>Score</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {board.rows.map((row, i) => (
                  <BoardRow key={row.symbol} row={row} rank={i + 1} />
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <footer>
        <p>
          Relative volume is time-of-day normalised — volume so far today against the average traded
          by this clock time over the past 10 sessions. Four digits just after the open is correct.
        </p>
        <p>
          No free bulk news source backs this screen, so nothing checks for a catalyst and every name
          lands <em>relaxed</em>. That is &ldquo;unknown&rdquo;, not &ldquo;no catalyst&rdquo;. An
          empty gap means it could not be computed, not that the name did not gap.
        </p>
      </footer>
    </main>
  );
}

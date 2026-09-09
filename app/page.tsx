import { Board } from "./Board";
import { StaleNotice } from "./StaleNotice";
import { formatCompact, formatMultiple, formatNumber } from "@/lib/format";
import { loadScreener } from "@/lib/screener";

// The scan is a committed file, so the page is fully static: it is rebuilt when
// the refresh workflow pushes a new data/today.json, and never on page view.
export const dynamic = "force-static";

export default async function Page() {
  const payload = await loadScreener();

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
          <span className="stamp">
            {payload.trade_date ? `Session ${payload.trade_date}` : "Awaiting the first scan"}
          </span>
        </div>
      </header>

      <StaleNotice generatedAt={payload.updated_at} />

      <section className="criteria" aria-label="Screen criteria">
        <Chip label="Change" value={`≥ ${formatNumber(payload.criteria.min_change_pct, 0)}%`} />
        <Chip
          label="Price"
          value={`$${formatNumber(payload.criteria.min_price, 0)}–$${formatNumber(
            payload.criteria.max_price,
            0,
          )}`}
        />
        <Chip label="RVOL" value={`≥ ${formatMultiple(payload.criteria.min_relative_volume)}`} />
        <Chip
          label="Float"
          value={
            payload.criteria.max_float_shares
              ? `< ${formatCompact(payload.criteria.max_float_shares)}`
              : "any"
          }
        />
        <Chip label="Volume" value={`≥ ${formatCompact(payload.criteria.min_day_volume)}`} />
        <Chip
          label="Catalyst"
          value={payload.criteria.require_news_catalyst ? "required" : "off"}
        />
      </section>

      <Board payload={payload} />

      <footer className="footnotes">
        <p>
          <strong>Strict</strong> names clear every criterion. <strong>Relaxed</strong> names fail
          only float, RVOL or the catalyst check and are shown to fill the board out to five — the
          badge says which criteria they missed. For research, use the strict rows.
        </p>
        <p>
          Two things this data is not. Relative volume from TradingView is{" "}
          <em>time-of-day normalized</em> — today&apos;s volume so far against the average traded by
          this same clock time over the past 10 sessions — so a 900× reading at 09:35 is not
          comparable to one at 15:55, nor to a conventional full-session RVOL. And no free bulk news
          source backs this screen, so the catalyst check never runs: every row is &ldquo;news
          unknown&rdquo;, which is why they land as relaxed rather than strict. Unknown is not the
          same as absent.
        </p>
        <p className="schedule">
          Scanned on weekdays at 09:25, 09:35, 09:40 and 15:55 ET. Each view above is one of those
          measurements, kept separate because their numbers are not comparable.
        </p>
      </footer>
    </main>
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

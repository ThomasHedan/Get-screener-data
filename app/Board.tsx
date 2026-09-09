"use client";

import { useState } from "react";

import {
  defaultSlot,
  formatClock,
  formatCompact,
  formatEastern,
  formatMultiple,
  formatNumber,
  formatPercent,
  orderedSlots,
  PHASE_LABELS,
  PRE_OPEN_SLOTS,
  SLOT_META,
  type InPlayRow,
  type ScreenerPayload,
} from "@/lib/format";

/**
 * The day's boards, one tab per measurement.
 *
 * A pre-open watchlist and a ten-minutes-in board answer different questions —
 * "what might run today" versus "what is actually running" — and a trader
 * wants both without waiting for the next scheduled scan. They are separate
 * views rather than one merged table because their numbers are not comparable:
 * before the bell the gap column is meaningless and the quiet names still
 * carry yesterday's figures.
 */
export function Board({ payload }: { payload: ScreenerPayload }) {
  const slots = orderedSlots(payload.slots);
  const [selected, setSelected] = useState<string | null>(() => defaultSlot(payload.slots));

  if (slots.length === 0) return <EmptyState placeholder={payload.placeholder} />;

  const active = selected && payload.slots[selected] ? selected : slots[0];
  const board = payload.slots[active];
  const meta = SLOT_META[active] ?? { label: active, time: "", caption: "" };
  const strict = board.in_play.filter((row) => row.qualification === "strict").length;

  return (
    <>
      <nav className="slots" aria-label="Session view">
        {slots.map((slot) => {
          const slotMeta = SLOT_META[slot] ?? { label: slot, time: "" };
          const captured = formatClock(payload.slots[slot]?.generated_at ?? null);
          return (
            <button
              key={slot}
              type="button"
              className={`slot ${slot === active ? "slot-active" : ""}`}
              aria-current={slot === active}
              onClick={() => setSelected(slot)}
            >
              <span className="slot-label">{slotMeta.label}</span>
              <span className="slot-time">{captured ? `${captured} ET` : slotMeta.time}</span>
            </button>
          );
        })}
      </nav>

      {meta.caption ? <p className="slot-caption">{meta.caption}</p> : null}

      <div className="slot-meta">
        {board.session_phase ? (
          <span className={`phase phase-${board.session_phase}`}>
            {PHASE_LABELS[board.session_phase]}
          </span>
        ) : null}
        <span className="stamp">
          {formatEastern(board.generated_at)
            ? `Scanned ${formatEastern(board.generated_at)} ET`
            : "No timestamp"}
        </span>
      </div>

      {board.notice ? (
        <p className={`notice ${PRE_OPEN_SLOTS.has(active) ? "notice-preopen" : ""}`} role="status">
          {board.notice}
        </p>
      ) : null}

      {board.in_play.length > 0 ? (
        <Table rows={board.in_play} />
      ) : (
        <EmptyState placeholder={false} />
      )}

      <section className="stats" aria-label="Scan statistics">
        <Stat label="Tickers scanned" value={formatCompact(board.stats.universe_rows)} />
        <Stat label="Cleared price/volume" value={formatCompact(board.stats.coarse_candidates)} />
        <Stat label="Strict qualifiers" value={formatCompact(board.stats.strict_qualifiers)} />
        <Stat label="On the board" value={`${board.in_play.length} (${strict} strict)`} />
      </section>
    </>
  );
}

function Table({ rows }: { rows: InPlayRow[] }) {
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
      <p className="empty-head">{placeholder ? "No scan has run yet." : "Nothing is in play."}</p>
      <p>
        {placeholder
          ? "The board fills in when the refresh workflow runs the screener and commits data/today.json — or run `npm run screen` yourself."
          : "Not one name on the US market cleared the price, change and volume floors at this point in the session. On a quiet day that is the honest answer, not a bug."}
      </p>
    </div>
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

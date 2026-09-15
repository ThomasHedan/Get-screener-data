import { Pool } from "pg";

/**
 * One pool for the process. Next keeps modules alive across requests, so a pool
 * created per request would leak connections until Postgres refused new ones.
 */
const pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 4 });

export type Qualification = "strict" | "relaxed";
export type Phase = "closed" | "pre-market" | "regular" | "after-hours";

export interface Row {
  symbol: string;
  exchange: string;
  sector: string | null;
  close: number;
  changePct: number | null;
  /** null means the gap was not computable, never "did not gap". */
  gapPct: number | null;
  relativeVolume: number | null;
  volume: number;
  floatShares: number | null;
  score: number;
  qualification: Qualification;
  /** Every close recorded for this symbol today, oldest first. */
  trail: number[];
}

export interface Board {
  capturedAt: Date;
  tradeDate: string;
  phase: Phase;
  universeRows: number;
  notice: string | null;
  capturesToday: number;
  rows: Row[];
}

/** Postgres returns numeric as a string to protect precision; the board wants numbers. */
const num = (value: string | null) => (value === null ? null : Number(value));

/**
 * The latest capture, its rows, and each row's intraday trail.
 *
 * Returns null when the database is empty or unreachable — a board that has
 * never run is a truthful state to render, not a crash.
 */
export async function loadBoard(): Promise<Board | null> {
  try {
    const { rows: board } = await pool.query("SELECT * FROM board ORDER BY score DESC");
    if (board.length === 0) return null;

    const head = board[0];
    const [{ rows: trails }, { rows: counts }] = await Promise.all([
      pool.query(
        `SELECT symbol, close FROM intraday
          WHERE trade_date = $1 AND symbol = ANY($2)
          ORDER BY captured_at`,
        [head.trade_date, board.map((r) => r.symbol)],
      ),
      pool.query("SELECT count(*)::int AS n FROM scan WHERE trade_date = $1", [head.trade_date]),
    ]);

    const bySymbol = new Map<string, number[]>();
    for (const { symbol, close } of trails) {
      bySymbol.set(symbol, [...(bySymbol.get(symbol) ?? []), Number(close)]);
    }

    return {
      capturedAt: head.captured_at,
      tradeDate: head.trade_date.toISOString().slice(0, 10),
      phase: head.phase,
      universeRows: head.universe_rows,
      notice: head.notice,
      capturesToday: counts[0].n,
      rows: board.map((r) => ({
        symbol: r.symbol,
        exchange: r.exchange,
        sector: r.sector,
        close: Number(r.close),
        changePct: num(r.change_pct),
        gapPct: num(r.gap_pct),
        relativeVolume: num(r.relative_volume),
        volume: Number(r.volume),
        floatShares: r.float_shares === null ? null : Number(r.float_shares),
        score: Number(r.score),
        qualification: r.qualification,
        trail: bySymbol.get(r.symbol) ?? [],
      })),
    };
  } catch (error) {
    console.error("board query failed", error);
    return null;
  }
}

import { readFile } from "node:fs/promises";
import path from "node:path";

/**
 * The dashboard's only data source is data/today.json, written by
 * `python -m warrior_screener snapshot --json`. The shape below mirrors
 * `_write_json` in warrior_screener/cli.py -- if you add a field there, add it
 * here too.
 */

export type Qualification = "strict" | "relaxed" | "rejected";

export type SessionPhase = "closed" | "pre-market" | "regular" | "after-hours";

export interface InPlayRow {
  ticker: string;
  close: number;
  change_pct: number | null;
  gap_pct: number | null;
  relative_volume: number | null;
  volume: number;
  avg_volume: number | null;
  float_shares: number | null;
  market_cap: number | null;
  primary_exchange: string | null;
  score: number;
  qualification: Qualification;
  rejected_by: string[];
  /** False means nothing checked for news -- not that there is no catalyst. */
  news_checked: boolean;
  news_count: number;
}

export interface ScreenerPayload {
  generated_at: string | null;
  trade_date: string | null;
  session_phase: SessionPhase | null;
  notice: string | null;
  stats: {
    universe_rows?: number;
    coarse_candidates?: number;
    strict_qualifiers?: number;
    in_play?: number;
    relaxed_in_play?: number;
  };
  criteria: {
    min_change_pct?: number;
    min_price?: number;
    max_price?: number;
    min_relative_volume?: number;
    max_float_shares?: number | null;
    min_day_volume?: number;
    require_news_catalyst?: boolean;
  };
  in_play: InPlayRow[];
  /** True while no real scan has been written yet (fresh clone, first deploy). */
  placeholder?: boolean;
}

const EMPTY: ScreenerPayload = {
  generated_at: null,
  trade_date: null,
  session_phase: null,
  notice: null,
  stats: {},
  criteria: {},
  in_play: [],
  placeholder: true,
};

/**
 * Read the scan the extractor last published.
 *
 * SCREENER_DATA_DIR is the volume the extractor writes to, so a new scan shows
 * up on the next request with no rebuild and no deploy. It falls back to the
 * repo's own data/ so `npm run dev` works against a local `snapshot --json`.
 *
 * A missing or unparseable file is an empty board, never a failure: the page's
 * whole job is to say what the screen found, and "nothing has run yet" is a
 * truthful answer it can render.
 */
export async function loadScreener(): Promise<ScreenerPayload> {
  try {
    const dir = process.env.SCREENER_DATA_DIR ?? path.join(process.cwd(), "data");
    const file = path.join(dir, "today.json");
    const parsed = JSON.parse(await readFile(file, "utf8")) as Partial<ScreenerPayload>;
    return { ...EMPTY, ...parsed, in_play: parsed.in_play ?? [] };
  } catch {
    return EMPTY;
  }
}

// ------------------------------------------------------------------ Formatting

const ET = "America/New_York";

/** Format the scan time in Eastern, the only timezone a US session is read in. */
export function formatEastern(iso: string | null): string | null {
  if (!iso) return null;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return null;
  return new Intl.DateTimeFormat("en-US", {
    timeZone: ET,
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatNumber(value, 1)}%`;
}

/** Compact share counts and market caps: 8.4M reads faster than 8,400,000. */
export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (Math.abs(value) >= 1_000_000_000) return `${formatNumber(value / 1_000_000_000, 2)}B`;
  if (Math.abs(value) >= 1_000_000) return `${formatNumber(value / 1_000_000, 2)}M`;
  if (Math.abs(value) >= 1_000) return `${formatNumber(value / 1_000, 1)}K`;
  return formatNumber(value, 0);
}

/** RVOL runs from ~1x to four digits pre-market; keep it readable at both ends. */
export function formatMultiple(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value >= 100 ? formatNumber(value, 0) : formatNumber(value, 1)}×`;
}

export const PHASE_LABELS: Record<SessionPhase, string> = {
  closed: "Market closed",
  "pre-market": "Pre-market",
  regular: "Open",
  "after-hours": "After hours",
};

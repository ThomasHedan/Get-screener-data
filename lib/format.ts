/**
 * Types and formatting shared by the server page and the client board.
 *
 * Deliberately free of `node:fs` — the board is a client component, so
 * anything it imports has to survive being bundled for the browser. The file
 * reading lives next door in screener.ts.
 */

export type Qualification = "strict" | "relaxed" | "rejected" | "carried";

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

/** One measurement of the day: what the screen saw at that moment. */
export interface SlotBoard {
  generated_at: string | null;
  session_phase: SessionPhase | null;
  notice: string | null;
  stats: {
    universe_rows?: number;
    coarse_candidates?: number;
    strict_qualifiers?: number;
    in_play?: number;
    relaxed_in_play?: number;
  };
  in_play: InPlayRow[];
}

export interface ScreenerPayload {
  trade_date: string | null;
  updated_at: string | null;
  criteria: {
    min_change_pct?: number;
    min_price?: number;
    max_price?: number;
    min_relative_volume?: number;
    max_float_shares?: number | null;
    min_day_volume?: number;
    require_news_catalyst?: boolean;
  };
  slots: Record<string, SlotBoard>;
  /** True while no real scan has been written yet (fresh clone, first deploy). */
  placeholder?: boolean;
}

/**
 * Display order and captions for the day's slots. Mirrors SLOT_TIMES in
 * warrior_screener/slots.py — if you add a slot there, add it here.
 */
export const SLOT_ORDER = ["pre_open", "t_plus_5", "t_plus_10", "close", "manual"] as const;

export const SLOT_META: Record<string, { label: string; time: string; caption: string }> = {
  pre_open: {
    label: "Pre-open",
    time: "09:25",
    caption:
      "Five minutes before the bell. Names already gapping on real pre-market volume are live here; " +
      "quieter ones still carry yesterday's figures, and the gap column is not yet meaningful because " +
      "the session has not opened. Read this as a watchlist, not as prices.",
  },
  t_plus_5: {
    label: "Open +5",
    time: "09:35",
    caption:
      "Five minutes into the session — the window where the day's momentum declares itself, and where " +
      "relative volume is at its most extreme against a normal 09:35.",
  },
  t_plus_10: {
    label: "Open +10",
    time: "09:40",
    caption:
      "Ten minutes in. The opening auction has settled, the gap is real, and the names still running " +
      "here are the ones that held their move rather than spiking on the bell.",
  },
  close: {
    label: "Close",
    time: "15:55",
    caption:
      "Five minutes before the bell. This is how the day actually ended — useful for judging the " +
      "morning's board after the fact, not for trading.",
  },
  manual: {
    label: "Manual",
    time: "",
    caption: "A hand-triggered scan, outside the daily schedule.",
  },
};

/** Slots whose figures precede the open, and carry that caveat. */
export const PRE_OPEN_SLOTS = new Set(["pre_open"]);

/** The tab to open on: the ten-minute board when it exists, else the latest. */
export function defaultSlot(slots: Record<string, SlotBoard>): string | null {
  const present = orderedSlots(slots);
  if (present.length === 0) return null;
  if (present.includes("t_plus_10")) return "t_plus_10";
  // Latest by capture time, falling back to display order.
  return present.reduce((latest, slot) => {
    const a = slots[slot]?.generated_at ?? "";
    const b = slots[latest]?.generated_at ?? "";
    return a > b ? slot : latest;
  }, present[0]);
}

/** Present slots, in display order, with anything unrecognised appended. */
export function orderedSlots(slots: Record<string, SlotBoard>): string[] {
  const known = SLOT_ORDER.filter((slot) => slot in slots);
  const extra = Object.keys(slots).filter(
    (slot) => !(SLOT_ORDER as readonly string[]).includes(slot),
  );
  return [...known, ...extra];
}

// ------------------------------------------------------------------ Formatting

const ET = "America/New_York";

/** Format a scan time in Eastern, the only timezone a US session is read in. */
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

export function formatClock(iso: string | null): string | null {
  if (!iso) return null;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return null;
  return new Intl.DateTimeFormat("en-US", {
    timeZone: ET,
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

/** RVOL runs from ~1x to four digits at the open; keep it readable at both ends. */
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

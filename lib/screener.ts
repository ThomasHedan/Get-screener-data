import { readFile } from "node:fs/promises";
import path from "node:path";

import type { ScreenerPayload, SlotBoard } from "./format";

/**
 * The dashboard's only data source is data/today.json, written by
 * `python -m warrior_screener snapshot --json`. The shape lives in
 * lib/format.ts and mirrors `_write_json` in warrior_screener/cli.py -- if you
 * add a field there, add it here too.
 *
 * Server-only: this reads the filesystem at build time. Client components
 * import lib/format.ts instead.
 */

const EMPTY: ScreenerPayload = {
  trade_date: null,
  updated_at: null,
  criteria: {},
  slots: {},
  placeholder: true,
};

/**
 * Read the scan written by the refresh workflow.
 *
 * A missing or unparseable file is an empty board, never a build failure: the
 * page's whole job is to say what the screen found, and "nothing has run yet"
 * is a truthful answer it can render.
 */
export async function loadScreener(): Promise<ScreenerPayload> {
  try {
    const file = path.join(process.cwd(), "data", "today.json");
    const parsed = JSON.parse(await readFile(file, "utf8")) as Record<string, unknown>;
    return normalise(parsed);
  } catch {
    return EMPTY;
  }
}

/**
 * Accept the single-board payload the first version wrote.
 *
 * Only matters for the window between deploying this and the next scheduled
 * scan: the committed file is still the old shape, and a page that crashed on
 * it would take the site down for no reason.
 */
function normalise(parsed: Record<string, unknown>): ScreenerPayload {
  const base: ScreenerPayload = {
    ...EMPTY,
    trade_date: (parsed.trade_date as string) ?? null,
    updated_at: (parsed.updated_at as string) ?? (parsed.generated_at as string) ?? null,
    criteria: (parsed.criteria as ScreenerPayload["criteria"]) ?? {},
    placeholder: Boolean(parsed.placeholder),
  };

  if (parsed.slots && typeof parsed.slots === "object") {
    return { ...base, slots: parsed.slots as Record<string, SlotBoard> };
  }

  if (Array.isArray(parsed.in_play) && parsed.in_play.length > 0) {
    return {
      ...base,
      slots: {
        manual: {
          generated_at: (parsed.generated_at as string) ?? null,
          session_phase: (parsed.session_phase as SlotBoard["session_phase"]) ?? null,
          notice: (parsed.notice as string) ?? null,
          stats: (parsed.stats as SlotBoard["stats"]) ?? {},
          in_play: parsed.in_play as SlotBoard["in_play"],
        },
      },
    };
  }

  return base;
}

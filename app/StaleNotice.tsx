"use client";

import { useEffect, useState } from "react";

/**
 * Warn when the board predates today's session.
 *
 * The page is a static build, so every label on it — including the session
 * phase badge — describes the moment the scan ran, not the moment you are
 * reading it. Open the site at 08:00 and you would see yesterday's closing
 * board wearing a green "Open" badge, which is exactly the misreading a
 * trader cannot afford. The server cannot catch this (it rendered hours ago),
 * so the check has to happen in the browser.
 *
 * Rendered only after mount: the server has no idea what "today" is for the
 * viewer, and rendering it during SSR would guarantee a hydration mismatch.
 */
export function StaleNotice({ generatedAt }: { generatedAt: string | null }) {
  const [staleSince, setStaleSince] = useState<string | null>(null);

  useEffect(() => {
    if (!generatedAt) return;
    const scanned = new Date(generatedAt);
    if (Number.isNaN(scanned.getTime())) return;

    const easternDay = (date: Date) =>
      new Intl.DateTimeFormat("en-CA", {
        timeZone: "America/New_York",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).format(date);

    if (easternDay(scanned) !== easternDay(new Date())) {
      setStaleSince(
        new Intl.DateTimeFormat("en-US", {
          timeZone: "America/New_York",
          weekday: "long",
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        }).format(scanned),
      );
    }
  }, [generatedAt]);

  if (!staleSince) return null;

  return (
    <p className="notice notice-stale" role="alert">
      These are not today&apos;s numbers — this board was last scanned {staleSince} ET. The next
      refresh runs at 09:35 ET on the following trading day.
    </p>
  );
}

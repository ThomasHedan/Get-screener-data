const ET = "America/New_York";

export const easternTime = (at: Date) =>
  new Intl.DateTimeFormat("en-US", {
    timeZone: ET,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(at);

export const easternDate = (at: Date) =>
  new Intl.DateTimeFormat("en-US", {
    timeZone: ET,
    weekday: "short",
    month: "short",
    day: "numeric",
  }).format(at);

export const price = (value: number) => value.toFixed(2);

export const percent = (value: number | null) =>
  value === null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;

/** RVOL runs to four digits just after the open; keep it readable at both ends. */
export const multiple = (value: number | null) =>
  value === null ? "—" : `${value >= 100 ? Math.round(value).toLocaleString("en-US") : value.toFixed(1)}×`;

export const compact = (value: number | null) => {
  if (value === null) return "—";
  if (value >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(0)}K`;
  return `${value}`;
};

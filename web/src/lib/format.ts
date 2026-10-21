/** Shared formatting for the dashboard (en-US, UTC). */

export function fmtInt(n: number | string | null | undefined): string {
  if (n === null || n === undefined || n === "") return "—";
  return Number(n).toLocaleString("en-US");
}

export function fmtSol(n: number | string | null | undefined, digits = 1): string {
  if (n === null || n === undefined || n === "") return "—";
  const v = Number(n);
  if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(1)}K SOL`;
  return `${v.toFixed(digits)} SOL`;
}

export function fmtPct(ratio: number | string | null | undefined, digits = 0): string {
  if (ratio === null || ratio === undefined || ratio === "") return "—";
  return `${(Number(ratio) * 100).toFixed(digits)}%`;
}

export function fmtMultiple(n: number | string | null | undefined): string {
  if (n === null || n === undefined || n === "") return "—";
  const v = Number(n);
  return `×${v >= 10 ? v.toFixed(0) : v.toFixed(1)}`;
}

export function fmtFollowers(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}

export function toDate(d: Date | string | number | null | undefined): Date | null {
  if (d === null || d === undefined) return null;
  return d instanceof Date ? d : new Date(d);
}

/** "2026-10-19 17:31 UTC" */
export function fmtDateTime(d: Date | string | number | null | undefined): string {
  const x = toDate(d);
  if (!x) return "—";
  return `${x.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/** "5m ago", "3h ago", "2d ago" relative to `now`. */
export function fmtAgo(d: Date | string | number | null | undefined, now = Date.now()): string {
  const x = toDate(d);
  if (!x) return "—";
  const s = Math.max(0, Math.round((now - x.getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

/** "7xKp…h4Qm" */
export function shortAddr(a: string | null | undefined, head = 4, tail = 4): string {
  if (!a) return "—";
  return a.length <= head + tail + 1 ? a : `${a.slice(0, head)}…${a.slice(-tail)}`;
}

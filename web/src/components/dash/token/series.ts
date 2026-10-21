import type { MarketPoint } from "@/components/chart/types";

/** Pure helpers for the token window: series lookups and compact formatting. Times are unix seconds unless noted. */

/** Linear interpolation of the (despiked) market-cap series; null outside the observed range. */
export function valueAt(points: MarketPoint[], t: number): number | null {
  if (!points.length || t < points[0].t || t > points[points.length - 1].t) return null;
  let lo = 0, hi = points.length - 1;
  while (lo < hi) { const mid = (lo + hi) >>> 1; if (points[mid].t < t) lo = mid + 1; else hi = mid; }
  const b = points[lo], a = points[Math.max(0, lo - 1)];
  return a.t === b.t ? b.mcapSol : a.mcapSol + (b.mcapSol - a.mcapSol) * (t - a.t) / (b.t - a.t);
}

/** Highest point at or after `t`, with the base value at `t`. When the series starts later, the first observation is the base
 * only within two minutes of `t`; a later base would overstate the move since `t`. */
export function peakSince(points: MarketPoint[], t: number): { t: number; value: number; base: number; multiple: number } | null {
  const later = points.filter(p => p.t >= t);
  if (!later.length) return null;
  const at = valueAt(points, t);
  if (at === null && later[0].t - t > 120) return null;
  const base = at ?? later[0].mcapSol;
  const top = later.reduce((best, p) => p.mcapSol > best.mcapSol ? p : best, later[0]);
  return base > 0 ? { t: top.t, value: top.mcapSol, base, multiple: top.mcapSol / base } : null;
}

/** SVG path ("M x y L x y …") for a sparkline of `values` in a width × height box. */
export function sparkPath(values: number[], width: number, height: number): string {
  if (values.length < 2) return "";
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  return values.map((v, i) => `${i ? "L" : "M"}${(i / (values.length - 1) * width).toFixed(1)} ${(height - 2 - (v - min) / span * (height - 4)).toFixed(1)}`).join(" ");
}

/** "48.2", "1.3K" — SOL amounts for KPI tiles and badges (unit printed separately). */
export const fmtMcap = (v: number | null | undefined) => v === null || v === undefined || !Number.isFinite(v) ? "—" : v >= 1000 ? `${(v / 1000).toFixed(1)}K` : v.toFixed(v >= 100 ? 0 : 1);

/** "+12%", "−8%" from a ratio of two market caps; null when either side is missing. */
export const fmtChange = (from: number | null, to: number | null) => from === null || to === null || from <= 0 ? null : (to / from - 1) * 100;

/** "45s", "2m 05s", "1h 12m", "2d 3h" */
export function fmtDur(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor(s % 86400 / 3600)}h`;
}

/** Trade time relative to the KOL's own first call of this token. */
export function relToCall(tradeIso: string, callIso: string | null): string {
  if (!callIso) return "no call from this KOL";
  const diff = (Date.parse(tradeIso) - Date.parse(callIso)) / 1000;
  if (Math.abs(diff) < 5) return "at the call";
  return diff > 0 ? `${fmtDur(diff)} after call` : `${fmtDur(-diff)} before call`;
}

/** "12:34:56" (UTC clock for the log) */
export const clock = (iso: string | number) => new Date(iso).toISOString().slice(11, 19);

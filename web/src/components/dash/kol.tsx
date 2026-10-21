import Link from "next/link";
import { Avatar } from "@/components/ui/Avatar";
import { fmtInt, fmtPct } from "@/lib/format";

/** CA tiers (client rule): 0–9 green, 10–19 yellow, 20 and above red. Same thresholds as the landing graph. */
export function CaBadge({ value }: { value: number }) {
  return <span title="Distinct contract addresses ever mentioned, including unlinked tokens. CA < 10: green; 10–19: yellow; ≥ 20: red. More CA means more shilling." className={`inline-flex rounded border border-current/25 px-2 py-0.5 font-mono ${value < 10 ? "text-win" : value < 20 ? "text-[#f2c94c]" : "text-rug"}`}>{fmtInt(value)}</span>;
}
export function SmartsBadge({ value }: { value: number | null }) {
  return <span className="inline-flex items-center gap-1 font-mono text-accent" title="SMARTS: smart followers. A dash means data is not available."><span aria-hidden="true">✦</span>{value === null ? "—" : fmtInt(value)}</span>;
}

/** Round avatar + handle linking to the shiller profile (stored avatar_url first, then unavatar.io, then initials). */
export function KolName({ id, username, avatar, size = 28, className = "" }: { id: string; username: string; avatar?: string | null; size?: number; className?: string }) {
  return <Link href={`/app/shillers/${id}`} className={`inline-flex min-w-0 items-center gap-2 hover:text-accent ${className}`}><Avatar username={username} src={avatar} size={size} /><span className="break-all">@{username}</span></Link>;
}

export type HoldStats = { median_hold_s: number | null; closed_positions: number; positions: number; fast_dump_share: number | null };

/** "9s", "25s", "1 min", "12 min", "2.5 h", "3 d". */
export function fmtHold(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} h`;
  return `${(seconds / 86400).toFixed(1)} d`;
}

// Heat scale, as in the Pump.fun callouts table: the faster a KOL dumps, the darker the red.
// Stops: seconds → [r, g, b]. Below 30 s is the darkest band, 10 min and more is neutral (no fill).
const heatStops: [number, [number, number, number]][] = [
  [0, [92, 7, 7]], [30, [143, 16, 16]], [60, [207, 51, 51]], [180, [239, 98, 98]], [300, [248, 125, 125]], [600, [250, 150, 150]],
];

function heat(seconds: number | null): { background?: string; color?: string } {
  if (seconds === null || seconds >= 600) return {};
  let i = 0;
  while (i < heatStops.length - 2 && seconds > heatStops[i + 1][0]) i++;
  const [s0, c0] = heatStops[i], [s1, c1] = heatStops[i + 1];
  const t = Math.min(1, Math.max(0, (seconds - s0) / (s1 - s0)));
  const rgb = c0.map((v, k) => Math.round(v + (c1[k] - v) * t));
  // light fills get dark red text, dark fills white text
  const luma = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2];
  return { background: `rgb(${rgb.join(",")})`, color: luma > 150 ? "#4a0a0a" : "#fff" };
}

function holdTitle(h: HoldStats): string {
  if (!h.closed_positions) return `No closed positions yet (${fmtInt(h.positions)} tracked). Median hold time runs from a KOL wallet's first buy to its first sell.`;
  return `${fmtInt(h.closed_positions)} closed of ${fmtInt(h.positions)} positions · ${fmtPct(h.fast_dump_share, 0)} sold within 60 s of buying`;
}

/** Median hold time as a table cell filled with the heat colour (Pump.fun callouts style). */
export function HoldCell({ hold, className = "" }: { hold: HoldStats; className?: string }) {
  return <td title={holdTitle(hold)} className={`border-b border-line/60 px-3 py-2.5 text-center font-mono text-[12px] font-semibold ${className}`} style={heat(hold.median_hold_s)}>{fmtHold(hold.median_hold_s)}</td>;
}

/** Compact variant for lists, where there is no full-height cell. */
export function HoldPill({ hold }: { hold: HoldStats }) {
  return <span title={holdTitle(hold)} className="inline-flex rounded px-2 py-0.5 font-mono text-xs font-semibold" style={heat(hold.median_hold_s)}>{fmtHold(hold.median_hold_s)}</span>;
}

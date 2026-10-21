import type { ReactNode } from "react";
import { sparkPath } from "./series";

/** KPI tiles for the token window: big number, small sub-line and an optional graphic (sparkline / ring). Server components. */

export function KpiTile({ label, value, unit, sub, graphic, tone }: { label: ReactNode; value: ReactNode; unit?: ReactNode; sub?: ReactNode; graphic?: ReactNode; tone?: "win" | "rug" | "accent" }) {
  const color = tone === "win" ? "text-win" : tone === "rug" ? "text-rug" : tone === "accent" ? "text-accent" : "text-fg";
  return <div className={`relative min-w-0 overflow-hidden rounded-2xl border border-line bg-ink-2 p-4 ${tone ? "pl-5" : ""}`}>
    {tone && <span aria-hidden className={`absolute top-4 bottom-4 left-0 w-0.5 ${tone === "win" ? "bg-win" : tone === "rug" ? "bg-rug" : "bg-accent"}`} />}
    <p className="mono-label text-[0.62rem] text-muted">{label}</p>
    <div className="mt-2 flex items-end justify-between gap-3">
      <div className="min-w-0"><p className={`font-mono text-2xl leading-none break-words sm:text-[26px] ${color}`}>{value}{unit ? <span className="ml-1 text-xs text-muted">{unit}</span> : null}</p>{sub ? <p className="mt-2 font-mono text-[11px] break-words text-muted">{sub}</p> : null}</div>
      {graphic ? <div className="shrink-0 [&>svg]:hidden sm:[&>svg]:block">{graphic}</div> : null}
    </div>
  </div>;
}

export function Sparkline({ values, color = "var(--color-win)", width = 92, height = 34 }: { values: number[]; color?: string; width?: number; height?: number }) {
  const d = sparkPath(values, width, height);
  if (!d) return null;
  const last = d.slice(d.lastIndexOf("L") + 1).trim().split(" ");
  return <svg aria-hidden width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="overflow-visible">
    <path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" style={{ filter: `drop-shadow(0 0 3px ${color})` }} />
    <circle cx={last[0]} cy={last[1]} r="2.5" fill={color} style={{ filter: `drop-shadow(0 0 4px ${color})` }} />
  </svg>;
}

/** 0–100 ring; the arc colour follows the value (orange accent by default). */
export function RingGauge({ value, size = 56, color = "var(--color-accent)", label }: { value: number | null; size?: number; color?: string; label?: ReactNode }) {
  const r = (size - 6) / 2, c = 2 * Math.PI * r, v = value === null ? 0 : Math.max(0, Math.min(100, value));
  return <div className="relative grid place-items-center" style={{ width: size, height: size }}>
    <svg aria-hidden width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--color-line)" strokeWidth="4" />
      {value !== null && <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth="4" strokeLinecap="round" strokeDasharray={`${c * v / 100} ${c}`} style={{ filter: `drop-shadow(0 0 4px ${color})` }} />}
    </svg>
    <span className="absolute font-mono text-[11px] text-fg">{label ?? (value === null ? "—" : Math.round(v))}</span>
  </div>;
}

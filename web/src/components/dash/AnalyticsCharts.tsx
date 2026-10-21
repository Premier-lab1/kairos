import type { ReactNode } from "react";
import { Empty } from "./ui";

/** Horizontal SVG bars with a fixed 0–100% axis for narrative mindshare. */
export function NarrativeMindshare({ rows }: { rows: { narrative: string; share: string; shills: string }[] }) {
  if (!rows.some(r => Number(r.shills) > 0)) return <Empty>No linked shills in this window.</Empty>;
  return <svg viewBox={`0 0 640 ${rows.length * 34 + 24}`} role="img" aria-label="Narrative share of linked shills" className="w-full font-mono text-[12px]">
    <title>Narrative share of linked shills</title>
    {rows.map((r, i) => <g key={r.narrative} transform={`translate(0 ${i * 34})`}>
      <text x="0" y="18" fill="currentColor">{r.narrative}</text>
      <rect x="120" y="6" width="430" height="16" rx="3" fill="currentColor" className="text-line" />
      <rect x="120" y="6" width={430 * Number(r.share ?? 0)} height="16" rx="3" fill="currentColor" className="text-accent"><title>{`${r.narrative}: ${(Number(r.share ?? 0) * 100).toFixed(1)}% · ${r.shills} shills`}</title></rect>
      <text x="635" y="18" textAnchor="end" fill="currentColor" className="text-muted">{(Number(r.share ?? 0) * 100).toFixed(1)}%</text>
    </g>)}
  </svg>;
}

/** Paired columns, one pair per narrative; denominator is labeled tokens. */
export function NarrativeOutcomes({ rows }: { rows: { narrative: string; labeled: string; success_rate: string | null; rug_rate: string | null }[] }) {
  const labeled = rows.filter(r => Number(r.labeled) > 0);
  if (!labeled.length) return <Empty>No labeled 1h outcomes for tokens shilled in this window.</Empty>;
  const width = Math.max(640, labeled.length * 85), step = (width - 65) / labeled.length;
  return <div className="overflow-x-auto"><svg viewBox={`0 0 ${width} 250`} style={{ minWidth: width }} role="img" aria-label="Success and rug rates by narrative, 1h" className="w-full font-mono text-[10px]">
    <title>Success (green) and rug (red) rates among labeled tokens · 1h</title>
    {[0, 50, 100].map(p => <g key={p}><line x1="40" x2={width - 10} y1={175 - p * 1.4} y2={175 - p * 1.4} stroke="currentColor" className="text-line" /><text x="34" y={179 - p * 1.4} textAnchor="end" fill="currentColor">{p}%</text></g>)}
    {labeled.map((r, i) => <g key={r.narrative}>{[{ rate: r.success_rate, label: "success", color: "text-win" }, { rate: r.rug_rate, label: "rug", color: "text-rug" }].map((b, j) => <rect key={b.label} x={45 + i * step + j * step * .35} y={175 - Number(b.rate) * 140} width={step * .3} height={Number(b.rate) * 140} fill="currentColor" className={b.color}><title>{`${r.narrative} · ${b.label}: ${(Number(b.rate) * 100).toFixed(1)}% · ${r.labeled} labeled`}</title></rect>)}<text transform={`translate(${45 + i * step} 193) rotate(25)`} fill="currentColor" className="text-muted">{r.narrative}</text></g>)}
    <text x="40" y="243" fill="currentColor" className="text-win">Green: success</text><text x="180" y="243" fill="currentColor" className="text-rug">Red: rug</text>
  </svg></div>;
}

export type Bar = { label: string; value: number; color?: string };
export function Bars({ rows, label }: { rows: Bar[]; label: string }) {
  if (!rows.some(r => r.value > 0)) return <Empty>No activity in this window.</Empty>;
  const max = Math.max(...rows.map(r => r.value), 1), width = 640, step = 580 / rows.length;
  return <svg viewBox={`0 0 ${width} 220`} role="img" aria-label={label} className="w-full font-mono text-[10px]">
    <title>{label}</title>
    {[0, .5, 1].map(f => <g key={f}><line x1="45" x2="625" y1={170 - f * 145} y2={170 - f * 145} stroke="currentColor" className="text-line" /><text x="38" y={174 - f * 145} textAnchor="end" fill="currentColor" className="text-muted">{Number((max * f).toFixed(1))}</text></g>)}
    {rows.map((r, i) => <g key={r.label}><rect x={45 + i * step + step * .12} y={170 - r.value / max * 145} width={step * .76} height={r.value / max * 145} fill="currentColor" className={r.color ?? "text-accent"}><title>{`${r.label}: ${r.value}`}</title></rect>{rows.length <= 6 && <text x={45 + (i + .5) * step} y={160 - r.value / max * 145} textAnchor="middle" fill="currentColor" className={r.color ?? "text-accent"}>{r.value}</text>}{(rows.length <= 6 || i % Math.ceil(rows.length / 4) === 0) && <text x={45 + (i + .5) * step} y="192" textAnchor={i === 0 ? "start" : "middle"} fill="currentColor" className="text-muted">{r.label}</text>}</g>)}
  </svg>;
}
export function RankedBars({ rows, label, max = 100 }: { rows: { id: string; name: ReactNode; value: number; detail: ReactNode }[]; label: string; max?: number }) {
  if (!rows.length) return <Empty>No qualifying activity in this window.</Empty>;
  return <ul aria-label={label} className="space-y-4">{rows.map(r => <li key={r.id}><div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-xs"><div className="flex min-w-0 flex-wrap items-center gap-2">{r.name}</div><span className="font-mono">{r.detail}</span></div><div className="h-2 overflow-hidden rounded bg-ink"><div className="h-full rounded bg-accent" style={{ width: `${Math.min(100, r.value / Math.max(max, 1) * 100)}%` }} /></div></li>)}</ul>;
}
const colors: Record<string, string> = { success: "bg-win", rug: "bg-rug", neutral: "bg-slate-400", dead: "bg-slate-600", pending: "bg-accent", no_data: "bg-slate-800" };
export function OutcomeBar({ rows }: { rows: { label: string; count: string }[] }) {
  const total = rows.reduce((n, r) => n + Number(r.count), 0);
  if (!total) return <Empty>No called tokens yet.</Empty>;
  return <div><div role="img" aria-label={rows.map(r => `${r.label}: ${r.count}`).join(", ")} className="flex h-7 overflow-hidden rounded">{rows.map(r => <div key={r.label} title={`${r.label}: ${r.count}`} className={colors[r.label] ?? "bg-slate-800"} style={{ width: `${Number(r.count) / total * 100}%` }} />)}</div><ul className="mt-3 flex flex-wrap gap-4 font-mono text-xs">{rows.map(r => <li key={r.label} className="flex items-center gap-1"><span className={`size-2 rounded ${colors[r.label]}`} />{r.label === "success" ? "win" : r.label.replaceAll("_", " ")}: {r.count}</li>)}</ul></div>;
}

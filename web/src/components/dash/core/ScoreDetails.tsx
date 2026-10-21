import Link from "next/link";
import { Card, ConfidencePill, Empty, OutcomePill, TableWrap, td, th } from "@/components/dash/ui";
import { RiskTags, humanLabel, linkClass } from "./DataViews";
import { fmtInt, fmtMultiple, fmtPct } from "@/lib/format";
import type { Signal } from "@/lib/server/queries/signals";

export function ScoreDetails({ signal }: { signal: Signal }) {
  const f = signal.features;
  const scale = Math.max(1, ...Object.values(f.components).map(Math.abs));
  return <Card title="Why this score" aside="Scorer s1">
    <div className="mb-4 flex items-center gap-4"><span className="font-mono text-3xl">{signal.score.toFixed(1)}<span className="text-base text-muted"> / 100</span></span><ConfidencePill value={signal.confidence} /></div>
    <p className="mb-6 text-sm text-muted">Score = rules + statistics over past outcomes; AI only explains.</p>
    <div className="space-y-3">{Object.entries(f.components).map(([key, value]) => <div key={key} className="grid grid-cols-[7rem_1fr_3rem] items-center gap-2 font-mono text-xs sm:grid-cols-[9rem_1fr_4rem]"><span>{humanLabel(key)}</span><div className="relative grid h-3 grid-cols-2"><div className="flex justify-end border-r border-line">{value < 0 && <span className="h-full bg-rug" style={{ width: `${Math.abs(value) / scale * 100}%` }} />}</div><div>{value > 0 && <div className="h-full bg-win" style={{ width: `${value / scale * 100}%` }} />}</div></div><span className={`text-right ${value < 0 ? "text-rug" : value > 0 ? "text-win" : "text-muted"}`}>{value > 0 ? "+" : ""}{value.toFixed(1)}</span></div>)}</div>
    <h3 className="mono-label mb-3 mt-8 text-muted">Shillers inside</h3>
    {f.inside.length ? <TableWrap><table className="w-full"><thead><tr>{["Shiller", "Minute", "Final outcomes", "Wins", "Rugs", "Lift"].map(h => <th key={h} className={th}>{h}</th>)}</tr></thead><tbody>{f.inside.map((s, i) => <tr key={`${s.kolId}-${i}`}><td className={td}><Link className={linkClass} href={`/app/shillers/${s.kolId}`}>@{s.username}</Link></td><td className={td}>{s.minute?.toFixed(1) ?? "—"}</td><td className={td}>{fmtInt(s.nFinal)}</td><td className={td}>{fmtInt(s.success)}</td><td className={td}>{fmtInt(s.rug)}</td><td className={td}>{fmtMultiple(s.lift)}</td></tr>)}</tbody></table></TableWrap> : <Empty>No shiller details were stored with this signal.</Empty>}
    <h3 className="mono-label mb-3 mt-8 text-muted">Similar past cases</h3>
    {f.similar.length ? <TableWrap><table className="w-full"><thead><tr>{["Token", "Similarity", "Outcome", "Peak"].map(h => <th key={h} className={th}>{h}</th>)}</tr></thead><tbody>{f.similar.map((s, i) => <tr key={`${s.tokenId}-${i}`}><td className={td}><Link className={linkClass} href={`/app/tokens/${s.tokenId}`}>{s.symbol}</Link></td><td className={td}>{fmtPct(s.similarity)}</td><td className={td}><OutcomePill label={s.label} /></td><td className={td}>{fmtMultiple(s.peak)}</td></tr>)}</tbody></table></TableWrap> : <Empty>No similar finalized cases were found.</Empty>}
    <h3 className="mono-label mb-3 mt-8 text-muted">Risks</h3><RiskTags codes={f.risks} />
    <h3 className="mono-label mb-3 mt-8 text-muted">Base rates</h3><p className="font-mono text-xs">Success {fmtPct(f.base.successRate)} · rug {fmtPct(f.base.rugRate)} · {fmtInt(f.base.nFinal)} final outcomes</p>{f.base.defaultUsed && <p className="mt-2 text-sm text-muted">Default rates used: fewer than 100 finalized outcomes were available when this signal was scored.</p>}
  </Card>;
}

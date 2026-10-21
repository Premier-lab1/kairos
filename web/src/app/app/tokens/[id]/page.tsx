import { notFound } from "next/navigation";
import Link from "next/link";
import { requireUser } from "@/lib/server/auth";
import { getToken } from "@/lib/server/queries/tokens";
import { Card, ConfidencePill, Empty, OutcomePill, TableWrap, td, th } from "@/components/dash/ui";
import { SmartsBadge } from "@/components/dash/kol";
import { Avatar } from "@/components/ui/Avatar";
import { MentionsTable, linkClass } from "@/components/dash/core/DataViews";
import { ScoreDetails } from "@/components/dash/core/ScoreDetails";
import { CallChart } from "@/components/chart/CallChart";
import type { ChartEvent } from "@/components/chart/types";
import { KpiTile, RingGauge, Sparkline } from "@/components/dash/token/Kpis";
import { CopyButton } from "@/components/dash/token/CopyButton";
import { KolBuysPanel } from "@/components/dash/token/KolBuys";
import { TweetsPanel } from "@/components/dash/token/Tweets";
import { ActivityLog } from "@/components/dash/token/ActivityLog";
import { clock, fmtChange, fmtDur, fmtMcap, peakSince } from "@/components/dash/token/series";
import { fmtAgo, fmtDateTime, fmtFollowers, fmtMultiple, fmtPct, shortAddr } from "@/lib/format";
import type { Outcome } from "@/lib/demo";

const yesNo = (value: boolean | null) => value === null ? "—" : value ? "Yes" : "No";
function chartOutcome(label: string | undefined): Outcome | undefined {
  return label === "success" || label === "rug" || label === "dead" || label === "neutral" || label === "pending" ? label : undefined;
}
export default async function TokenPage({ params }: { params: Promise<{ id: string }> }) {
  await requireUser();
  const { id } = await params;
  if (!/^\d+$/.test(id) || !Number.isSafeInteger(Number(id)) || Number(id) <= 0) notFound();
  const token = await getToken(Number(id));
  if (!token) notFound();
  const migrationCreation = token.createdAt && token.migratedAt && Math.abs(Date.parse(token.createdAt) - Date.parse(token.migratedAt)) <= 60_000;
  const outcome = token.outcomes.find(o => o.ruleVersion === "v2" && o.reference === "first_mention" && o.hours === 24);
  const mint = encodeURIComponent(token.mint);
  const name = token.symbol || token.name || "Token";
  const points = token.chart.points, last = points[points.length - 1] ?? null;
  const hasChart = points.length >= 2 && points.some(p => p.mcapSol !== points[0].mcapSol);
  // KPIs: everything derives from data already loaded (mentions, despiked chart, signal, outcomes).
  const first = token.mentions.length ? token.mentions.reduce((a, m) => Date.parse(m.time) < Date.parse(a.time) ? m : a) : null;
  const firstT = first ? Date.parse(first.time) / 1000 : null;
  const peak = firstT !== null ? peakSince(points, firstT) : null;
  const peakMultiple = peak?.multiple ?? outcome?.peak ?? null;
  const changeSinceCall = fmtChange(peak?.base ?? null, last?.mcapSol ?? null);
  const shillers = new Map<number, number | null>(); for (const m of token.mentions) if (!shillers.has(m.kolId)) shillers.set(m.kolId, m.smarts);
  const smarts = [...shillers.values()].reduce<number>((s, v) => s + (v ?? 0), 0), smartsKnown = [...shillers.values()].some(v => v !== null);
  const firstMinute = firstT !== null && token.createdAt && !migrationCreation ? (firstT - Date.parse(token.createdAt) / 1000) / 60 : null;
  const trend = outcome?.label === "rug" ? "down" : outcome?.label === "dead" ? "flat" : "up";
  const events: ChartEvent[] = token.kolTrades.map(k => ({ t: Date.parse(k.time) / 1000, tone: k.side === "buy" ? "up" : "down", label: `@${k.username} ${k.side.toUpperCase()} ${k.solAmount.toFixed(2)} SOL · ${clock(k.time)} UTC` }));
  const scoreColor = token.signal ? token.signal.score >= 70 ? "var(--color-win)" : token.signal.score >= 40 ? "var(--color-accent)" : "var(--color-rug)" : "var(--color-accent)";
  return <>
    <header className="mb-6 flex flex-wrap items-start justify-between gap-x-8 gap-y-4 pt-6">
      <div className="min-w-0 max-w-full">
        <div className="flex flex-wrap items-center gap-3"><h1 className="text-3xl font-extrabold uppercase tracking-tight sm:text-4xl">{token.symbol || shortAddr(token.mint, 6, 6)}</h1><OutcomePill label={outcome?.label} />{token.launchpad && <span className="mono-label text-[0.62rem] text-muted">{token.launchpad}</span>}</div>
        <p className="mt-2 text-sm text-muted">{token.name || "No name recorded"}</p>
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 font-mono text-[11px]"><span className="break-all text-muted" title="Mint">{token.mint}</span><CopyButton text={token.mint} /><a className={linkClass} href={`https://pump.fun/coin/${mint}`} target="_blank" rel="noopener noreferrer">pump.fun ↗</a><a className={linkClass} href={`https://solscan.io/token/${mint}`} target="_blank" rel="noopener noreferrer">Solscan ↗</a></div>
        {migrationCreation && <p className="mt-2 max-w-[70ch] text-xs text-accent">Creation time is within one minute of migration. This may be the migration time rather than the original launch.</p>}
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 font-mono text-[11px] sm:grid-cols-4 lg:text-right">
        <div><dt className="mono-label text-[0.62rem] text-muted">Created</dt><dd className="mt-1">{fmtDateTime(token.createdAt)}</dd></div>
        <div><dt className="mono-label text-[0.62rem] text-muted">Migrated</dt><dd className="mt-1">{fmtDateTime(token.migratedAt)}</dd></div>
        <div><dt className="mono-label text-[0.62rem] text-muted">Creator</dt><dd className="mt-1" title={token.creator ?? undefined}>{shortAddr(token.creator)}</dd></div>
        <div><dt className="mono-label text-[0.62rem] text-muted">Trades synced</dt><dd className="mt-1">{token.tradesSyncedUntil ? fmtAgo(token.tradesSyncedUntil) : "queued"}</dd></div>
      </dl>
    </header>
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
        <KpiTile label="Market cap" value={fmtMcap(last?.mcapSol ?? null)} unit="SOL" tone={last ? changeSinceCall === null ? undefined : changeSinceCall >= 0 ? "win" : "rug" : undefined} sub={last ? changeSinceCall === null ? `as of ${clock(last.t * 1000)} UTC` : `${changeSinceCall >= 0 ? "+" : ""}${changeSinceCall.toFixed(0)}% since first call` : "no trades yet"} graphic={points.length >= 2 ? <Sparkline values={points.slice(-60).map(p => p.mcapSol)} color={trend === "down" ? "var(--color-rug)" : trend === "flat" ? "var(--color-flat)" : "var(--color-win)"} /> : undefined} />
        <KpiTile label="Peak since first call" value={fmtMultiple(peakMultiple)} tone={peakMultiple !== null ? peakMultiple >= 2 ? "win" : undefined : undefined} sub={peak ? `${fmtMcap(peak.base)} → ${fmtMcap(peak.value)} SOL · ${clock(peak.t * 1000)} UTC` : outcome?.peak !== null && outcome?.peak !== undefined ? "from outcome v2 · 24h" : first ? "no chart after the first call" : "no calls yet"} />
        <KpiTile label="Kairos score" value={token.signal ? token.signal.score.toFixed(1) : "—"} unit={token.signal ? "/ 100" : undefined} sub={token.signal ? <ConfidencePill value={token.signal.confidence} /> : "not scored"} graphic={<RingGauge value={token.signal?.score ?? null} color={scoreColor} />} />
        <KpiTile label="Shillers" value={shillers.size} sub={<span className="inline-flex items-center gap-1">SMARTS <SmartsBadge value={smartsKnown ? smarts : null} /></span>} graphic={<div className="flex -space-x-2">{[...shillers.keys()].slice(0, 4).map(kolId => { const m = token.mentions.find(x => x.kolId === kolId)!; return <span key={kolId} className="rounded-full ring-2 ring-ink-2"><Link href={`/app/shillers/${kolId}`} className="block rounded-full hover:ring-2 hover:ring-accent"><Avatar username={m.username} size={26} src={m.avatarUrl} /></Link></span>; })}</div>} />
        <KpiTile label="First call" value={firstMinute === null ? "—" : firstMinute < 1 ? `${Math.round(firstMinute * 60)}s` : firstMinute < 120 ? `${firstMinute.toFixed(0)}m` : fmtDur(firstMinute * 60)} unit={firstMinute === null ? undefined : "after creation"} sub={first ? <>@{first.username} · {fmtFollowers(first.followers)} followers</> : "no calls yet"} />
      </div>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,25rem)]">
        <div className="min-w-0 space-y-5">
          <Card title="Market cap & calls" aside={hasChart ? `${fmtDateTime(token.chart.start === null ? null : token.chart.start * 1000)} → ${fmtDateTime(token.chart.end === null ? null : token.chart.end * 1000)} · ${token.chart.bucketSeconds === null ? "—" : token.chart.bucketSeconds / 60} min` : "SOL"}>{hasChart ? <>
            <CallChart variant="glow" trend={trend} points={points} volume={token.chart.volume} calls={token.chart.calls} events={events} marker={token.signal?.firstMentionAt ? { t: Date.parse(token.signal.firstMentionAt) / 1000, label: "Kairos signal" } : undefined} outcome={chartOutcome(outcome?.label)} ariaLabel={`${name} market cap in SOL, trade volume, shiller calls and KOL wallet trades`} />
            <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-muted"><span className="inline-flex items-center gap-1.5"><span className="size-1.5 rounded-full bg-accent" /> call anchor</span><span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-full bg-win" /> KOL buy</span><span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-full bg-rug" /> KOL sell</span><span className="inline-flex items-center gap-1.5"><span className="flex items-end gap-0.5"><i className="h-3 w-1 bg-win/50" /><i className="h-2 w-1 bg-rug/50" /></span> buys / sells volume</span><span className="inline-flex items-center gap-1.5"><span className="w-4 border-t border-dashed border-accent" /> Kairos signal</span></p>
          </> : <Empty>{token.tradesSyncedUntil === null ? "Trades not loaded yet — this token is in the backfill queue" : "Not enough trades to draw a chart"}</Empty>}</Card>
          <TweetsPanel mentions={token.mentions} points={points} />
        </div>
        <div className="min-w-0 space-y-5">
          <KolBuysPanel trades={token.kolTrades} />
          <ActivityLog token={token} mentions={token.mentions} kolTrades={token.kolTrades} signal={token.signal} peak={peak} />
        </div>
      </div>
      <Card title="Outcomes">{token.outcomes.length ? <TableWrap><table className="w-full"><thead><tr>{["Rule", "Reference", "Horizon", "Outcome", "Peak", "End", "Max drawdown", "Liquidity alive", "Migrated"].map(h => <th key={h} className={th}>{h}</th>)}</tr></thead><tbody>{token.outcomes.map(o => <tr key={`${o.ruleVersion}-${o.reference}-${o.hours}`}><td className={td}>{o.ruleVersion}</td><td className={td}>{o.reference.replaceAll("_", " ")}</td><td className={td}>{o.hours}h</td><td className={td}><OutcomePill label={o.label} /></td><td className={td}>{fmtMultiple(o.peak)}</td><td className={td}>{fmtMultiple(o.end)}</td><td className={td}>{fmtPct(o.drawdown)}</td><td className={td}>{yesNo(o.liquidityAlive)}</td><td className={td}>{yesNo(o.migrated)}</td></tr>)}</tbody></table></TableWrap> : <Empty>No outcomes computed yet. Outcome labels need a reference price and sufficient on-chain history.</Empty>}</Card>
      {token.signal && <ScoreDetails signal={token.signal} />}
      <Card title="Mentions" aside={`${token.mentions.length} collected`}><MentionsTable mentions={token.mentions} detail /></Card>
    </div>
  </>;
}

import { getDailyCalls, getShillerOutcomes } from "@/lib/server/queries/analytics";
import { Bars, OutcomeBar } from "@/components/dash/AnalyticsCharts";
import { CaBadge, HoldPill, SmartsBadge } from "@/components/dash/kol";
import { Avatar } from "@/components/ui/Avatar";
import { notFound } from "next/navigation";
import { requireUser } from "@/lib/server/auth";
import { getShiller } from "@/lib/server/queries/shillers";
import { getShillerTrades } from "@/lib/server/queries/kolTrades";
import { TradeFeed } from "@/app/app/kols/TradeFeed";
import { Card, Empty, PageTitle, Stat } from "@/components/dash/ui";
import { CallsTable, minute } from "@/components/dash/shillers/Tables";
import { fmtDateTime, fmtInt, fmtMultiple, fmtPct, shortAddr } from "@/lib/format";

export default async function ShillerPage({ params }: { params: Promise<{ id: string }> }) {
  await requireUser();
  const { id } = await params;
  if (!/^[1-9]\d{0,18}$/.test(id) || BigInt(id) > BigInt("9223372036854775807")) notFound();
  const shiller = await getShiller(id);
  if (!shiller) notFound();
  const [daily, outcomes, trades] = await Promise.all([getDailyCalls(id), getShillerOutcomes(id), getShillerTrades(id)]);
  return <>
    <PageTitle title={<span className="flex items-center gap-3 sm:gap-4"><Avatar username={shiller.username} src={shiller.avatar_url} size={56} /><a href={`https://x.com/${encodeURIComponent(shiller.username)}`} target="_blank" rel="noopener noreferrer" className="min-w-0 break-all hover:text-accent">@{shiller.username}</a></span>} sub={shiller.display_name ?? "Shiller profile"} />
    <div className="space-y-5">
      <Card title="Profile">
        <div className="mb-6 grid grid-cols-2 gap-5"><Stat label="SMARTS" value={<SmartsBadge value={shiller.smart_followers_count} />} hint="Smart followers" /><Stat label="CA" value={<CaBadge value={shiller.ca} />} hint="Distinct addresses ever mentioned" /></div>
        <div className="mb-6 grid grid-cols-2 gap-5 lg:grid-cols-4"><Stat label="Median hold" value={<HoldPill hold={shiller.hold} />} hint="Wallet first buy → first sell" /><Stat label="Positions" value={fmtInt(shiller.hold.positions)} hint="Tracked wallet buys, one per token" /><Stat label="Closed" value={fmtInt(shiller.hold.closed_positions)} hint="Sold after buying; only these count" /><Stat label="Fast dumps" value={shiller.hold.closed_positions ? fmtPct(shiller.hold.fast_dump_share, 0) : "—"} hint="Closed positions held under 60 s" /></div>
        <dl className="grid gap-4 sm:grid-cols-3">{[["Followers", fmtInt(shiller.followers_count)], ["First seen", fmtDateTime(shiller.first_seen_at)]].map(([label, value]) => <div key={label}><dt className="text-xs text-muted">{label}</dt><dd className="mt-1 font-mono text-sm">{value}</dd></div>)}</dl>
        {shiller.history.length > 0 && <div className="mt-4"><h3 className="text-xs text-muted">Username history</h3><ul className="mt-1 space-y-1 font-mono text-xs">{shiller.history.map(item => <li key={item.username} className="break-all">@{item.username} · {fmtDateTime(item.changed_at)}</li>)}</ul></div>}
        <div className="mt-4"><h3 className="text-xs text-muted">Linked wallets</h3>{shiller.wallets.length ? <ul className="mt-1 flex flex-wrap gap-3 font-mono text-xs">{shiller.wallets.map(wallet => <li key={`${wallet.address}-${wallet.relation}`}><span title={wallet.address}>{shortAddr(wallet.address, 6, 6)}</span> · {wallet.relation}</li>)}</ul> : <p className="mt-1 text-sm text-muted">No linked wallets recorded.</p>}</div>
      </Card>
      <Card title="Wallets & recent trades" aside="Latest 20">
        {shiller.wallets.length ? <ul className="mb-4 space-y-2 font-mono text-xs">{shiller.wallets.map(wallet => <li className="break-all" key={`${wallet.address}:${wallet.relation}`}>{wallet.address} <span className="text-muted">· {wallet.relation}</span></li>)}</ul> : <p className="mb-4 text-sm text-muted">No linked wallets recorded.</p>}
        {trades.length ? <TradeFeed trades={trades} /> : <Empty>No wallet trades recorded yet.</Empty>}
      </Card>
      <Card title="Track record · v2 / 24h / first mention">
        <div className="grid grid-cols-2 gap-5 lg:grid-cols-4">
          <Stat label="Tokens called" value={fmtInt(shiller.calls)} hint="All distinct linked tokens" />
          <Stat label="Finals" value={fmtInt(shiller.finals)} hint="Attributable tokens only" />
          <Stat label="Wins / rugs" value={`${shiller.wins} / ${shiller.rugs}`} />
          <Stat label="Win rate" value={fmtPct(shiller.winRate, 1)} hint={`95% Wilson LB: ${shiller.finals ? fmtPct(shiller.lowerBound, 1) : "—"}`} />
          <Stat label="Rug rate" value={fmtPct(shiller.rugRate, 1)} />
          <Stat label="Median entry · min" value={minute(shiller.median_entry)} />
          <Stat label="Shrunk win lift" value={fmtMultiple(shiller.lift)} hint="(wins + 10 × base) / (finals + 10) / base" />
          <Stat label="Base win rate" value={fmtPct(shiller.base.winRate, 1)} hint={`${fmtInt(shiller.base.finals)} global finals${shiller.base.defaultUsed ? " · 5% fallback (<100 finals)" : " · observed rate"}`} />
        </div>
        <p className="mt-5 text-xs text-muted">Finals are success, rug, dead or neutral. Attribution requires this shiller’s first call within 60 minutes of the token’s first mention. Entry medians use first calls and omit missing, negative or apparent migration-time creation dates. These are current descriptive statistics, not a historical signal score.</p>
      </Card>
      <div className="grid gap-5 lg:grid-cols-2"><Card title="Calls by day · 30 days" aside="UTC · today is partial"><Bars label="Mentions by day over 30 UTC calendar days" rows={daily.map(r => ({ label: r.label, value: Number(r.count) }))} /></Card><Card title="Called token outcomes" aside="v2 · 24h · first mention"><OutcomeBar rows={outcomes} /><p className="mt-4 text-xs text-muted">All distinct called addresses, including late calls. Dead and no data are shown separately; unlinked tokens have no data. Track record rates above use attributable tokens only.</p></Card></div>
      <Card title="Calls" aside="Newest 300 mentions">
        <p className="mb-4 text-xs text-muted">Repeat tweets are shown separately. First shill marks a tweet at the token’s earliest mention (including ties); attributable refers to this shiller’s first call. Outcomes and peak use the token’s first mention, not this tweet. A dash means unknown or not yet labelled. Times are UTC.</p>
        {shiller.callsList.length ? <CallsTable calls={shiller.callsList} username={shiller.username} /> : <Empty>No calls recorded for this shiller yet.</Empty>}
      </Card>
    </div>
  </>;
}

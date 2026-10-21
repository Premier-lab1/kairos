import Link from "next/link";
import { getNarratives } from "@/lib/server/queries/narratives";
import { NarrativeMindshare } from "@/components/dash/AnalyticsCharts";
import { getHourlyActivity, getMindshare, getMostShilled } from "@/lib/server/queries/analytics";
import { Bars, RankedBars } from "@/components/dash/AnalyticsCharts";
import { CaBadge, SmartsBadge } from "@/components/dash/kol";
import { OutcomePill } from "@/components/dash/ui";
import { requireUser } from "@/lib/server/auth";
import { getOverview } from "@/lib/server/queries/overview";
import { getShillers } from "@/lib/server/queries/shillers";
import { TopShillers } from "@/components/dash/shillers/TopShillers";
import { Avatar } from "@/components/ui/Avatar";
import { Card, Empty, PageTitle, Stat } from "@/components/dash/ui";
import { Distribution, MentionsTable, SignalsTable, ThinBar, humanLabel } from "@/components/dash/core/DataViews";
import { fmtAgo, fmtInt } from "@/lib/format";

export default async function OverviewPage() {
  await requireUser();
  const [data, hourly, mindshare, mostShilled, narratives, topShillers] = await Promise.all([getOverview(), getHourlyActivity(), getMindshare(), getMostShilled(), getNarratives(), getShillers("", "lb", 10, true)]);
  const s = data.stats;
  return <><PageTitle title="Overview" sub="A read-only view of the live collection. Data updates every minute; reload to see the latest collection. Not financial advice." />
    <div className="space-y-5">
      <Card title="Hot narratives · 24h" aside={<Link className="text-accent hover:underline" href="/app/narratives">All narratives →</Link>}><NarrativeMindshare rows={narratives.filter(r => Number(r.shills) > 0).slice(0, 5)} /></Card>
      <Card title="Top shillers" aside={<Link className="text-accent hover:underline" href="/app/shillers">Leaderboard →</Link>}>{topShillers.length ? <TopShillers rows={topShillers} /> : <Empty>No shiller has a final outcome yet.</Empty>}<p className="mt-3 text-xs text-muted">Top 10 by 95% Wilson lower bound of the win rate (v2 · 24h · first mention). Median hold: time a shiller’s wallet holds a bought token before the first sell; dark red = fast dump, neutral from 10 min.</p></Card>
      <Card title="Shills by hour · 48h" aside="UTC · rolling hourly buckets"><Bars label="Mentions by hour over the last 48 hours, UTC" rows={hourly.map(r => ({ label: r.label, value: Number(r.count) }))} /></Card>
      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Top shillers · 24h" aside="Share of all mentions"><RankedBars label="Shiller mindshare over 24 hours" rows={mindshare.map(r => ({ id: r.id, name: <><Avatar username={r.username} src={r.avatar_url} size={22} /><Link className="break-all text-accent hover:underline" href={`/app/shillers/${r.id}`}>@{r.username}</Link><span>SMARTS <SmartsBadge value={r.smart_followers_count} /></span><span>CA <CaBadge value={Number(r.ca)} /></span></>, value: Number(r.share) * 100, detail: `${(Number(r.share) * 100).toFixed(1)}% · ${r.count} shills` }))} /></Card>
        <Card title="Most shilled tokens · 24h" aside="Distinct shillers"><RankedBars label="Tokens ranked by distinct shillers in 24 hours" max={Math.max(...mostShilled.map(r => Number(r.count)), 1)} rows={mostShilled.map(r => ({ id: r.id, name: <><Link className="text-accent hover:underline" href={`/app/tokens/${r.id}`}>{r.symbol || r.mint.slice(0, 8)}</Link><OutcomePill label={r.label} /></>, value: Number(r.count), detail: `${r.count} shillers` }))} /><p className="mt-3 text-xs text-muted">Linked tokens only. Outcome: v2 · 24h from first mention.</p></Card>
      </div>
      <Card title="Collection" aside={s.latest ? `Last shill seen ${fmtAgo(s.latest)}` : "No shills seen yet"}><div className="grid grid-cols-2 gap-6 sm:grid-cols-3 lg:grid-cols-4">{[
        ["Tokens total", s.tokens], ["Tokens seen · 24h", s.seen], ["Migrated · 24h", s.migrated], ["Mentions total", s.mentions], ["Mentions · 24h", s.recentMentions], ["Shillers tracked", s.shillers], ["Trade rows", s.trades],
      ].map(([label, value]) => <Stat key={label} label={label} value={fmtInt(value)} />)}</div></Card>
      <div className="grid gap-5 lg:grid-cols-2"><Card title="API credits today" aside="UTC day"><div className="space-y-5">{[{ provider: "frontrun", budget: 3000 }, { provider: "helius", budget: 20000 }].map(p => { const used = data.credits.find(c => c.provider === p.provider)?.used ?? 0; return <div key={p.provider}><div className="flex justify-between gap-3 font-mono text-xs"><span className="capitalize">{p.provider}</span><span>{fmtInt(used)} / {fmtInt(p.budget)}</span></div><ThinBar value={used} max={p.budget} /></div>; })}{data.credits.filter(c => !["frontrun", "helius"].includes(c.provider)).map(c => <p key={c.provider} className="font-mono text-xs">{c.provider}: {fmtInt(c.used)} credits · no budget configured</p>)}</div></Card>
      <Card title="Jobs queue" aside="Pending · top 5">{data.jobs.length ? <ul className="space-y-3">{data.jobs.map(j => <li key={j.kind} className="flex justify-between gap-3 font-mono text-xs"><span className="break-all">{humanLabel(j.kind)}</span><span>{fmtInt(j.count)}</span></li>)}</ul> : <Empty>No jobs pending.</Empty>}</Card></div>
      <div className="grid gap-5 lg:grid-cols-2">{[1, 24].map(hours => <Card key={hours} title={`Outcomes · ${hours}h`} aside="v2 · first mention"><Distribution rows={data.distribution.filter(r => r.hours === hours)} /></Card>)}</div>
      <Card title="Latest shills" aside="Last 20"><MentionsTable mentions={data.mentions} /></Card>
      <Card title="Latest signals" aside="s1 · last 10 updated"><SignalsTable signals={data.signals} /></Card>
    </div></>;
}

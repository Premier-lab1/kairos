import Link from "next/link";
import { requireUser } from "@/lib/server/auth";
import { getNarratives, getNarrativeShillers, getPumpingTokens } from "@/lib/server/queries/narratives";
import { NarrativeMindshare, NarrativeOutcomes } from "@/components/dash/AnalyticsCharts";
import { Avatar } from "@/components/ui/Avatar";
import { Card, Empty, PageTitle, TableWrap, td, th } from "@/components/dash/ui";

const percent = (value: string | null) => value === null ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
const multiple = (value: string | null) => value === null ? "—" : `${Number(value).toFixed(2)}×`;

export default async function NarrativesPage({ searchParams }: {
  searchParams: Promise<{ window?: string | string[] }>;
}) {
  await requireUser();
  const window = (await searchParams).window === "6h" ? "6h" : "24h";
  const [rows, shillers, pumping] = await Promise.all([
    getNarratives(window), getNarrativeShillers(window), getPumpingTokens(window),
  ]);
  return <><PageTitle title="Narratives" sub="Automatically classified from token names and tickers using keyword rules. Themes can be ambiguous; this is observed activity, not investment advice." right={
    <nav aria-label="Narrative time window" className="flex gap-3 font-mono text-sm">{["6h", "24h"].map(w => <Link key={w} href={`/app/narratives?window=${w}`} aria-current={window === w ? "page" : undefined} className={window === w ? "text-accent underline" : "text-muted hover:text-fg"}>{w}</Link>)}</nav>
  } />
    <div className="space-y-5">
      <div className="grid gap-5 xl:grid-cols-2">
        <Card title={`Narrative mindshare · ${window}`} aside="Share of linked shills"><NarrativeMindshare rows={rows} /><p className="mt-3 text-xs text-muted">Each linked shill counts once. Unlinked mentions are excluded; unclassified tokens are shown separately.</p></Card>
        <Card title={`Success / rug rates · ${window}`} aside="v2 · first mention · 1h"><NarrativeOutcomes rows={rows} /><p className="mt-3 text-xs text-muted">One outcome per distinct token shilled in the window. Pending and no-data outcomes are excluded from rates and peak multiples.</p></Card>
      </div>
      <Card title={`Narratives · ${window}`} aside="Top 3 shillers per theme">
        {!rows.length ? <Empty>No linked shills or migrations in this window.</Empty> : <TableWrap><table className="w-full"><thead><tr>{["Narrative", "Tokens shilled", "Shills", "Shillers", "Migrations", "Labeled 1h", "Success", "Rug", "Median peak", "Max peak", "Top shillers"].map(h => <th key={h} className={th}>{h}</th>)}</tr></thead><tbody>{rows.map(r => {
          const leaders = shillers.filter(s => s.narrative === r.narrative);
          return <tr key={r.narrative}><td className={`${td} text-accent`}>{r.narrative}</td><td className={td}>{r.tokens}</td><td className={td}>{r.shills}</td><td className={td}>{r.shillers}</td><td className={td}>{r.migrations}</td><td className={td}>{r.labeled}</td><td className={`${td} text-win`}>{percent(r.success_rate)}</td><td className={`${td} text-rug`}>{percent(r.rug_rate)}</td><td className={td}>{multiple(r.median_peak)}</td><td className={td}>{multiple(r.max_peak)}</td><td className={td}>{leaders.length ? <ul className="space-y-1">{leaders.map(s => <li key={s.id} className="flex items-center gap-2"><Avatar username={s.username} src={s.avatar_url} size={22} /><Link className="text-accent hover:underline" href={`/app/shillers/${s.id}`}>@{s.username}</Link> <span className="text-muted">· {s.shills}</span></li>)}</ul> : "—"}</td></tr>;
        })}</tbody></table></TableWrap>}
        <p className="mt-3 text-xs text-muted">Migrations count all tokens that migrated in the window, including those without shills. Other metrics describe linked shills in the same window.</p>
      </Card>
      <Card title={`Pumping now · ${window}`} aside="Top 20 by market cap growth">
        {!pumping.length ? <Empty>No tokens with positive growth and at least two valid market cap points in this window.</Empty> : <TableWrap><table className="w-full"><thead><tr>{["Token", "Narrative", "Growth", "First cap · SOL", "Last cap · SOL", "Points"].map(h => <th key={h} className={th}>{h}</th>)}</tr></thead><tbody>{pumping.map(t => <tr key={t.id}><td className={td}><Link className="text-accent hover:underline" href={`/app/tokens/${t.id}`}>{t.symbol || t.name || t.mint.slice(0, 8)}</Link></td><td className={td}>{t.narrative}</td><td className={`${td} text-win`}>+{percent(t.growth)}</td><td className={td}>{Number(t.first_cap).toFixed(2)}</td><td className={td}>{Number(t.last_cap).toFixed(2)}</td><td className={td}>{t.points}</td></tr>)}</tbody></table></TableWrap>}
        <p className="mt-3 text-xs text-muted">Last / first positive trade market cap within the window, minus one. At least two distinct timestamps required. Sparse trade coverage may miss price moves.</p>
      </Card>
    </div>
  </>;
}

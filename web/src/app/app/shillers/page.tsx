import Link from "next/link";
import { requireUser } from "@/lib/server/auth";
import { getCaDistribution } from "@/lib/server/queries/analytics";
import { getOutcomeBase, getShillers, getRugComparison, shillerSorts } from "@/lib/server/queries/shillers";
import { Bars, RankedBars } from "@/components/dash/AnalyticsCharts";
import { CaBadge, KolName } from "@/components/dash/kol";
import { Card, Empty, PageTitle } from "@/components/dash/ui";
import { Leaderboard } from "@/components/dash/shillers/Tables";
import { fmtInt } from "@/lib/format";

export default async function ShillersPage({ searchParams }: { searchParams: Promise<{ q?: string | string[]; sort?: string | string[] }> }) {
  await requireUser();
  const params = await searchParams;
  const q = (typeof params.q === "string" ? params.q : "").trim().replace(/^@/, "").slice(0, 100);
  const sort = shillerSorts.find(value => value === params.sort) ?? "lb";
  const [rows, base, distribution, comparison] = await Promise.all([getShillers(q, sort), getOutcomeBase(), getCaDistribution(), getRugComparison()]);
  const labels = { smarts: "SMARTS", ca: "CA", lb: "Wilson LB", finals: "Finals", rug: "Rug rate", followers: "Followers", recent: "Recent" };
  return <>
    <PageTitle title="Shillers" sub="Track records from v2 / 24h / first mention outcomes." right={<p className="font-mono text-xs text-muted">All tokens: {fmtInt(base.pending)} pending / {fmtInt(base.finals)} final / {fmtInt(base.noData)} no data</p>} />
    <div className="mb-5 grid gap-5 lg:grid-cols-2">
      <Card title="Shillers by CA" aside="All tracked shillers"><Bars label="Number of shillers by distinct CA count" rows={["< 10", "10–19", "≥ 20"].map((label, i) => ({ label, value: Number(distribution.find(r => r.label === label)?.count ?? 0), color: ["text-win", "text-[#f2c94c]", "text-rug"][i] }))} /></Card>
      <Card title="CA ↔ rug rate" aside="Up to 40 / highest CA first">{comparison.length ? <RankedBars label="CA and attributable rug rate, minimum three final tokens" rows={comparison.map(r => ({ id: r.id, name: <><KolName id={r.id} username={r.username} avatar={r.avatar_url} size={22} className="text-accent" /><span>CA <CaBadge value={r.ca} /></span></>, value: (r.rugRate ?? 0) * 100, detail: `${((r.rugRate ?? 0) * 100).toFixed(1)}% rugs / ${r.finals} final` }))} /> : <Empty>Not enough labelled tokens. Shillers need at least 3 attributable final outcomes to appear.</Empty>}<p className="mt-3 text-xs text-muted">All tracked shillers, independent of search. Bars show rug rate on a 0–100% scale.</p></Card>
    </div>
    <Card title="Leaderboard" aside="Up to 200 shillers">
      <p className="mb-4 max-w-[90ch] text-sm text-muted">CA counts every distinct address mentioned, including unlinked tokens. SMARTS means smart followers; a dash means unavailable. Final means success, rug, dead or neutral; pending and no data never enter the rates. A token is attributable only when the shiller first called it within 60 minutes of anyone’s first mention. Tokens called includes all distinct linked tokens; finals and rates use attributable tokens only.</p>
      <form action="/app/shillers" className="mb-4 flex flex-wrap gap-2">
        <label htmlFor="shiller-search" className="sr-only">Search username</label>
        <input id="shiller-search" name="q" defaultValue={q} placeholder="Search @username" maxLength={100} className="min-w-0 flex-1 rounded-lg border border-line bg-ink px-3 py-2 text-sm focus-visible:outline-accent" />
        <input type="hidden" name="sort" value={sort} /><button className="rounded-lg border border-line px-4 py-2 text-sm hover:border-accent">Search</button>
      </form>
      <nav aria-label="Leaderboard sort" className="mb-4 flex flex-wrap gap-2">{shillerSorts.map(value => <Link key={value} href={`/app/shillers?${new URLSearchParams({ sort: value, ...(q ? { q } : {}) })}`} aria-current={sort === value ? "page" : undefined} className={`rounded-full border px-3 py-1 font-mono text-xs ${sort === value ? "border-accent text-accent" : "border-line text-muted hover:text-fg"}`}>{labels[value]} ↓</Link>)}</nav>
      {rows.length ? <Leaderboard rows={rows} /> : <Empty>{q ? "No shillers match this username." : "No shillers collected yet. Profiles appear when CA mentions are discovered."}</Empty>}
      <p className="mt-4 text-xs text-muted">Wilson LB is the 95% lower confidence bound of the win rate. Entry minutes use each shiller’s first call; missing, negative and apparent migration-time creation dates are excluded. Times are UTC. Median hold is the median time from a shiller’s wallet first buying a token to its first sell, over closed positions only (dark red = fast dump, neutral from 10 min); a dash means no closed position yet.</p>
    </Card>
  </>;
}

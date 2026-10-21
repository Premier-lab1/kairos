import Link from "next/link";
import { Avatar } from "@/components/ui/Avatar";
import { HoldCell } from "@/components/dash/kol";
import { fmtInt, fmtPct } from "@/lib/format";
import type { ShillerStats } from "@/lib/server/queries/shillers";

const head = "border-b border-line bg-win/10 px-3 py-2.5 mono-label text-[0.62rem] font-normal tracking-widest text-fg";

/** Compact Pump.fun "callouts" style table: rank, avatar, name, win rate (green), median hold (heat). */
export function TopShillers({ rows }: { rows: ShillerStats[] }) {
  return <div className="overflow-hidden rounded-xl border border-win/30 bg-ink"><table className="w-full"><caption className="sr-only">Top shillers by Wilson lower bound of the win rate</caption>
    <thead><tr><th scope="col" className={`${head} w-10 text-center`}>#</th><th scope="col" className={`${head} text-left`}>Name</th><th scope="col" className={`${head} text-center`}>Win rate</th><th scope="col" className={`${head} text-center`}>Median hold</th></tr></thead>
    <tbody>{rows.map((r, i) => <tr key={r.id} className="hover:bg-ink-3">
      <td className="border-b border-line/60 px-3 py-2 text-center font-mono text-sm font-bold">{i + 1}</td>
      <td className="w-full min-w-28 max-w-0 border-b border-line/60 px-3 py-2 sm:min-w-40"><Link href={`/app/shillers/${r.id}`} className="flex min-w-0 items-center gap-3 hover:text-accent"><Avatar username={r.username} src={r.avatar_url} size={32} /><span className="min-w-0 truncate text-sm font-semibold">@{r.username}</span></Link></td>
      <td className="whitespace-nowrap border-b border-line/60 px-3 py-2 text-center font-mono text-sm font-bold text-win" title={`${fmtInt(r.wins)} wins of ${fmtInt(r.finals)} final tokens · Wilson LB ${fmtPct(r.lowerBound, 1)}`}>{fmtPct(r.winRate, 1)}</td>
      <HoldCell hold={r.hold} className="w-24 text-sm" />
    </tr>)}</tbody></table></div>;
}

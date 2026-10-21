import { CaBadge, HoldCell, KolName, SmartsBadge } from "@/components/dash/kol";
import Link from "next/link";
import { OutcomePill, TableWrap, th, td } from "@/components/dash/ui";
import { fmtDateTime, fmtInt, fmtMultiple, fmtPct, shortAddr } from "@/lib/format";
import type { ShillerCall, ShillerStats } from "@/lib/server/queries/shillers";

export function minute(value: number | string | null) {
  return value === null ? "—" : Number(value).toFixed(1);
}

export function Leaderboard({ rows }: { rows: ShillerStats[] }) {
  return <TableWrap><table className="w-full"><caption className="sr-only">Shiller leaderboard, v2 first mention outcomes at 24 hours</caption>
    <thead><tr>{["#", "Shiller", "Median hold", "SMARTS", "CA", "Tokens called", "Finals", "Wins", "Rugs", "Win rate", "Rug rate", "Wilson LB · 95%", "Median entry · min", "Last call", "Followers"].map(label => <th scope="col" className={th} key={label}>{label}</th>)}</tr></thead>
    <tbody>{rows.map((row, i) => <tr key={row.id} className="hover:bg-ink-3">
      <td className={`${td} text-center text-base font-bold`}>{i + 1}</td>
      <td className={td}><KolName id={row.id} username={row.username} avatar={row.avatar_url} size={32} className="text-sm font-semibold" /></td>
      <HoldCell hold={row.hold} className="min-w-24" />
      <td className={td}><SmartsBadge value={row.smart_followers_count} /></td><td className={td}><CaBadge value={row.ca} /></td>
      {[fmtInt(row.calls), fmtInt(row.finals), fmtInt(row.wins), fmtInt(row.rugs), fmtPct(row.winRate, 1), fmtPct(row.rugRate, 1), row.finals ? fmtPct(row.lowerBound, 1) : "—", minute(row.median_entry), fmtDateTime(row.last_call)].map((value, i) => <td className={i === 4 ? `${td} font-semibold text-win` : td} key={i}>{value}</td>)}
    <td className={`${td} text-muted`}>{fmtInt(row.followers_count)}</td>
    </tr>)}</tbody></table></TableWrap>;
}

export function CallsTable({ calls, username }: { calls: ShillerCall[]; username: string }) {
  const yesNo = (value: boolean | null) => value === null ? "—" : value ? "Yes" : "No";
  return <TableWrap><table className="w-full"><caption className="sr-only">Latest mentions, including repeat calls; outcomes use the token’s first mention</caption>
    <thead><tr>{["Token", "Tweet time", "Entry · min", "First shill", "Attributable", "1h", "24h", "Peak · 24h", "Deleted"].map(label => <th scope="col" key={label} className={th}>{label}</th>)}</tr></thead>
    <tbody>{calls.map(call => <tr key={call.id} className="hover:bg-ink-3">
      <td className={td}>{call.token_id ? <Link href={`/app/tokens/${call.token_id}`} className="text-accent hover:underline">{call.symbol || "Unnamed"}</Link> : <span>Unlinked token</span>}<span className="mt-1 block text-[10px] text-muted" title={call.mint}>{shortAddr(call.mint)}</span></td>
      <td className={td}><a href={`https://x.com/${encodeURIComponent(username)}/status/${encodeURIComponent(call.tweet_id)}`} target="_blank" rel="noopener noreferrer" className="hover:text-accent hover:underline">{fmtDateTime(call.tweet_time)}</a></td>
      <td className={td}>{minute(call.minute)}</td><td className={td}>{yesNo(call.first_shill)}</td><td className={td}>{yesNo(call.attributable)}</td>
      <td className={td}><OutcomePill label={call.outcome_1h} /></td><td className={td}><OutcomePill label={call.outcome_24h} /></td>
      <td className={td}>{fmtMultiple(call.peak_multiple)}</td><td className={td}>{yesNo(call.is_deleted)}</td>
    </tr>)}</tbody></table></TableWrap>;
}

import Link from "next/link";
import { ConfidencePill, Empty, OutcomePill, TableWrap, td, th } from "@/components/dash/ui";
import { fmtAgo, fmtDateTime, fmtFollowers, fmtInt, shortAddr } from "@/lib/format";
import type { Signal } from "@/lib/server/queries/signals";
import type { getMentions } from "@/lib/server/queries/tokens";

export const humanLabel = (code: string) => ({ creator_rugged_before: "creator rugged before", single_shiller: "single shiller", shiller_rug_history: "shiller rug history", low_evidence: "low evidence", late_entry: "late entry", unknown_creation: "unknown creation time", kol_fast_dumper: "shiller dumps fast (median hold < 2 min)", kol_sold_after_call: "shiller sold after his call", kol_bought_before_call: "shiller bought before calling", kol_holding: "shiller still holding" }[code] ?? code.replaceAll("_", " "));
/** Risk codes that speak for the token rather than against it. */
const positiveRisks = new Set(["kol_holding"]);
export function RiskTags({ codes }: { codes: string[] }) {
  if (!codes.length) return <span className="text-sm text-muted">None recorded</span>;
  return <span className="flex flex-wrap gap-2">{codes.map(code => <span key={code} className={`rounded border px-2 py-0.5 font-mono text-[11px] ${positiveRisks.has(code) ? "border-win/40 text-win" : "border-rug/40 text-rug"}`}>{humanLabel(code)}</span>)}</span>;
}
export const linkClass = "text-fg underline decoration-line underline-offset-4 hover:text-accent focus-visible:outline-2 focus-visible:outline-accent";
export function ThinBar({ value, max = 100 }: { value: number; max?: number }) {
  return <div className="mt-2 h-1 w-full overflow-hidden rounded bg-line" aria-hidden><div className="h-full bg-accent" style={{ width: `${Math.max(0, Math.min(100, value / max * 100))}%` }} /></div>;
}
export function Score({ value }: { value: number }) {
  return <div className="min-w-16 font-mono">{value.toFixed(1)}<ThinBar value={value} /></div>;
}
export function SignalsTable({ signals }: { signals: Signal[] }) {
  if (!signals.length) return <Empty>No signals yet for this view. Scorer s1 needs a first shill within the last 6 hours; past outcome history determines the strength and confidence. Filters may also hide signals.</Empty>;
  return <TableWrap><table className="w-full"><thead><tr>{["Token", "Score", "Confidence", "Shillers inside", "First mention", "Top risks"].map(h => <th key={h} className={th}>{h}</th>)}</tr></thead><tbody>{signals.map(s => <tr key={s.id}>
    <td className={td}><Link className={linkClass} href={`/app/tokens/${s.tokenId}`}>{s.symbol || "Unknown"}</Link><div className="mt-1 text-muted">{shortAddr(s.mint)}</div></td>
    <td className={td}><Score value={s.score} /></td><td className={td}><ConfidencePill value={s.confidence} /></td>
    <td className={td}>{fmtInt(s.nShillers)}<div className="mt-1 text-muted">{s.features.inside.slice(0, 3).map(i => `@${i.username}`).join(", ") || "No details"}</div></td>
    <td className={td}>{fmtDateTime(s.firstMentionAt)}</td><td className={`${td} max-w-64 whitespace-normal text-muted`}>{s.features.risks.slice(0, 3).map(humanLabel).join(" · ") || "None recorded"}</td>
  </tr>)}</tbody></table></TableWrap>;
}
export function MentionsTable({ mentions, detail = false }: { mentions: Awaited<ReturnType<typeof getMentions>>; detail?: boolean }) {
  if (!mentions.length) return <Empty>No shills collected for this view yet.</Empty>;
  const headers = detail ? ["Time (UTC)", "Minute", "Shiller", "Followers", "Deleted", "Likes / reposts", "Content", "Post"] : ["Token", "Shiller", "Followers", "Seen", "Post"];
  return <TableWrap><table className="w-full"><thead><tr>{headers.map(h => <th key={h} className={th}>{h}</th>)}</tr></thead><tbody>{mentions.map(m => <tr key={m.id}>
    {detail ? <><td className={td}>{fmtDateTime(m.time)}</td><td className={td}>{m.minute?.toFixed(1) ?? "—"}</td></> : <td className={td}>{m.tokenId ? <Link className={linkClass} href={`/app/tokens/${m.tokenId}`}>{m.symbol || shortAddr(m.mint)}</Link> : <span title="Token not linked yet">{shortAddr(m.mint)}</span>}</td>}
    <td className={td}><Link className={linkClass} href={`/app/shillers/${m.kolId}`}>@{m.username}</Link></td><td className={td}>{fmtFollowers(m.followers)}</td>
    {detail ? <><td className={td}>{m.deleted ? "Yes" : "No"}</td><td className={td}>{fmtInt(m.likes)} / {fmtInt(m.retweets)}</td><td className={`${td} min-w-64 max-w-96`} style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{m.content || "No content available"}</td></> : <td className={td}><time dateTime={m.time} title={fmtDateTime(m.time)}>{fmtAgo(m.time)}</time></td>}
    <td className={td}><a className={linkClass} href={m.tweetUrl} target="_blank" rel="noopener noreferrer">View post ↗</a></td>
  </tr>)}</tbody></table></TableWrap>;
}
const colors: Record<string, string> = { success: "bg-win", rug: "bg-rug", dead: "bg-flat", neutral: "bg-fg/60", pending: "bg-accent", no_data: "bg-muted/40" };
export function Distribution({ rows }: { rows: { label: string; count: number }[] }) {
  const total = rows.reduce((sum, r) => sum + r.count, 0);
  if (!total) return <Empty>No v2 outcomes for this horizon yet.</Empty>;
  return <><div className="flex h-3 overflow-hidden rounded" aria-hidden>{rows.map(r => <span key={r.label} className={colors[r.label] ?? "bg-muted"} style={{ width: `${r.count / total * 100}%` }} />)}</div><ul className="mt-4 flex flex-wrap gap-3">{rows.map(r => <li key={r.label} className="flex items-center gap-2 font-mono text-xs"><span className={`size-2 ${colors[r.label]}`} /><OutcomePill label={r.label} />{fmtInt(r.count)}</li>)}</ul></>;
}

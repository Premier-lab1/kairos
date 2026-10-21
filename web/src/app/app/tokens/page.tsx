import Link from "next/link";
import { requireUser } from "@/lib/server/auth";
import { getTokens } from "@/lib/server/queries/tokens";
import { Card, Empty, OutcomePill, PageTitle, TableWrap, td, th } from "@/components/dash/ui";
import { Score, linkClass } from "@/components/dash/core/DataViews";
import { fmtDateTime, fmtInt, fmtMultiple, shortAddr } from "@/lib/format";

export default async function TokensPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  await requireUser();
  const params = await searchParams, q = typeof params.q === "string" ? params.q.trim().slice(0, 200) : "";
  const requested = typeof params.page === "string" ? Number(params.page) : 1;
  const page = Number.isSafeInteger(requested) && requested > 0 ? Math.min(requested, 200) : 1; // public page: no deep OFFSET scans
  const { tokens, hasNext } = await getTokens(q, page);
  const href = (p: number) => `/app/tokens?${new URLSearchParams({ q, page: String(p) })}`;
  return <><PageTitle title="Tokens" sub="Tokens with at least one shill, newest first mention first. Outcomes use v2 · 24h · first mention." />
    <form action="/app/tokens" method="get" className="mb-5 flex flex-wrap gap-3"><label htmlFor="token-search" className="sr-only">Search symbol, name or exact mint</label><input id="token-search" name="q" defaultValue={q} placeholder="Symbol, name or exact mint" className="min-w-0 flex-1 rounded-lg border border-line bg-ink-2 px-4 py-3 text-sm outline-accent" /><button className="rounded-lg bg-accent px-5 py-3 text-sm font-semibold text-ink focus-visible:outline-2 focus-visible:outline-fg" type="submit">Search</button></form>
    <Card title="Shilled tokens" aside={`Page ${page} · 50 per page`}><p className="mb-4 text-xs text-muted">Shillers = distinct callers · 24h outcome = result from the first shill · Peak = highest multiple in that window. Hover for a summary; select any row to open the token.</p>{tokens.length ? <TableWrap><table className="w-full"><thead><tr>{["Token", "Mint", "Created", "Migrated", "Mentions", "Shillers", "24h outcome", "Score"].map(h => <th key={h} className={`${th} ${h === "Shillers" || h === "24h outcome" ? "bg-accent/10 text-accent" : ""}`}>{h}</th>)}</tr></thead><tbody>{tokens.map(t => <tr key={t.id} className="relative hover:bg-accent/10 focus-within:bg-accent/10" title={`First shill: ${fmtDateTime(t.firstMention)} UTC by @${t.firstShiller ?? "unknown"} · ${t.shillers} shillers · Peak: ${fmtMultiple(t.peak)} · 24h outcome: ${t.label ?? "—"}`}><td className={td}><Link className={`${linkClass} after:absolute after:inset-0 focus-visible:outline-2 focus-visible:outline-accent`} aria-label={`Open ${t.symbol || "unknown token"}, ${t.shillers} shillers, 24h outcome ${t.label ?? "unknown"}`} href={`/app/tokens/${t.id}`}>{t.symbol || "Unknown"}</Link><div className="max-w-48 truncate text-muted" title={t.name ?? undefined}>{t.name || "Unnamed"}</div></td><td className={td} title={t.mint}>{shortAddr(t.mint)}</td><td className={td}>{fmtDateTime(t.createdAt)}</td><td className={td}>{t.migratedAt ? "Yes" : "No"}</td><td className={td}>{fmtInt(t.mentions)}</td><td className={`${td} bg-accent/5 font-bold text-accent`}>{fmtInt(t.shillers)}</td><td className={`${td} bg-accent/5`}><OutcomePill label={t.label} /></td><td className={td}>{t.score !== null ? <Score value={t.score} /> : "—"}</td></tr>)}</tbody></table></TableWrap> : <Empty>No shilled tokens found on this page. Try another search or return to the first page.</Empty>}
      <nav aria-label="Token pagination" className="mt-5 flex flex-wrap justify-between gap-4 font-mono text-xs">{page > 1 ? <Link className={linkClass} href={href(page - 1)}>← Previous</Link> : <span />}{page > 1 && <Link className={linkClass} href={href(1)}>First page</Link>}{hasNext && <Link className={linkClass} href={href(page + 1)}>Next →</Link>}</nav>
    </Card></>;
}

import Link from "next/link";
import { Avatar } from "@/components/ui/Avatar";
import { CaBadge, SmartsBadge } from "@/components/dash/kol";
import { fmtAgo, fmtDateTime, fmtSol, shortAddr } from "@/lib/format";
import type { KolTrade } from "@/lib/server/queries/kolTrades";

export function TradeFeed({ trades }: { trades: KolTrade[] }) {
  return <ul className="divide-y divide-line">{trades.map(trade => <li key={`${trade.signature}:${trade.wallet_id}:${trade.mint}`} className={`grid gap-3 border-l-2 py-4 pl-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] ${trade.side === "buy" ? "border-win" : "border-rug"}`}>
    <div className="min-w-0"><div className="flex flex-wrap items-center gap-2 text-xs"><Link href={`/app/shillers/${trade.kol_id}`} className="inline-flex min-w-0 items-center gap-2 font-semibold hover:text-accent"><Avatar username={trade.username} src={trade.avatar_url} size={28} /><span className="break-all">@{trade.username}</span></Link><SmartsBadge value={trade.smart_followers_count} /><CaBadge value={Number(trade.ca)} /></div><time dateTime={new Date(trade.ts).toISOString()} title={fmtDateTime(trade.ts)} className="mt-2 block font-mono text-xs text-muted">{fmtAgo(trade.ts)}</time></div>
    <div className="min-w-0 font-mono text-sm"><span title={trade.mint}>{trade.token_id ? <Link className="hover:text-accent" href={`/app/tokens/${trade.token_id}`}>{trade.symbol || shortAddr(trade.mint)}</Link> : shortAddr(trade.mint)}</span><p className="mt-2 text-xs text-muted">MC {fmtSol(trade.market_cap_sol)}</p></div>
    <div className={`font-mono text-sm sm:text-right ${trade.side === "buy" ? "text-win" : "text-rug"}`}><span className="block text-[10px] uppercase">{trade.side}</span>{fmtSol(trade.sol_amount, 3)}</div>
  </li>)}</ul>;
}

import Link from "next/link";
import { Avatar } from "@/components/ui/Avatar";
import { SmartsBadge } from "@/components/dash/kol";
import { Card, Empty } from "@/components/dash/ui";
import { fmtDateTime } from "@/lib/format";
import type { KolTokenTrade } from "@/lib/server/queries/tokens";
import { clock, fmtDur, fmtMcap, relToCall } from "./series";

/** Hold line for one trade row: how long this wallet held (view kol_positions) or that it still holds. */
function hold(trade: KolTokenTrade): string | null {
  if (trade.side === "sell") return trade.holdSeconds !== null ? `sold after ${fmtDur(trade.holdSeconds)}` : trade.firstBuy ? null : "sold without a tracked buy";
  if (trade.holdSeconds !== null) return `held ${fmtDur(trade.holdSeconds)}`;
  return trade.firstBuy && !trade.firstSell ? "still holding" : null;
}

/** "KOL BUYS": on-chain trades of tracked KOL wallets in this token (table kol_trades), oldest first. */
export function KolBuysPanel({ trades }: { trades: KolTokenTrade[] }) {
  const buys = trades.filter(t => t.side === "buy"), sells = trades.filter(t => t.side === "sell");
  const net = buys.reduce((s, t) => s + t.solAmount, 0) - sells.reduce((s, t) => s + t.solAmount, 0);
  const wallets = new Set(trades.map(t => t.walletId)).size;
  return <Card title="KOL buys" aside={trades.length ? `${buys.length} buy · ${sells.length} sell · ${wallets} wallet${wallets === 1 ? "" : "s"}` : "Tracked wallets"}>
    {trades.length ? <>
      <p className="mb-3 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-muted"><span>Net flow <span className={net >= 0 ? "text-win" : "text-rug"}>{net >= 0 ? "+" : "−"}{Math.abs(net).toFixed(2)} SOL</span></span><span>Time relative to each KOL&apos;s own call</span></p>
      <ol className="max-h-[520px] divide-y divide-line/60 overflow-y-auto">{trades.map(t => <li key={`${t.signature}:${t.walletId}`} className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-3 py-3">
        <Avatar username={t.username} size={28} src={t.avatarUrl} ring={t.side === "buy" ? "var(--color-win)" : "var(--color-rug)"} />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs"><Link href={`/app/shillers/${t.kolId}`} className="truncate font-semibold hover:text-accent">@{t.username}</Link><span className={`font-mono text-[10px] uppercase tracking-wider ${t.side === "buy" ? "text-win" : "text-rug"}`}>{t.side}</span><SmartsBadge value={t.smarts} /></div>
          <p className="mt-1 truncate font-mono text-[11px] text-muted" title={`${relToCall(t.time, t.firstCall)} · ${t.program}`}>{relToCall(t.time, t.firstCall)}{t.mcapSol !== null && <> · MC {fmtMcap(t.mcapSol)} SOL</>}</p>
          {(hold(t) || t.closedPositions > 0) && <p className="mt-0.5 truncate font-mono text-[11px] text-muted">{hold(t) && <span className={hold(t) === "still holding" ? "text-win" : t.holdSeconds !== null && t.holdSeconds < 120 ? "text-rug" : ""}>{hold(t)}</span>}{hold(t) && t.closedPositions > 0 ? " · " : ""}{t.closedPositions > 0 && <span title={`${t.closedPositions} of ${t.positions} tracked positions closed`}>median hold {fmtDur(t.medianHoldSeconds)}</span>}</p>}
        </div>
        <div className={`text-right font-mono text-sm ${t.side === "buy" ? "text-win" : "text-rug"}`}>{t.side === "buy" ? "+" : "−"}{t.solAmount.toFixed(2)}<span className="ml-1 text-[10px] text-muted">SOL</span><time dateTime={t.time} title={fmtDateTime(t.time)} className="block text-[10px] text-muted">{clock(t.time)} UTC</time></div>
      </li>)}</ol>
    </> : <Empty>No tracked KOL wallet has traded this token yet. KOL wallets are still being discovered: Frontrun links wallets to shillers over time, and their on-chain buys and sells show up here once a linked wallet touches this mint.</Empty>}
  </Card>;
}

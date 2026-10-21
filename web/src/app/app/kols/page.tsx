import Link from "next/link";
import { requireUser } from "@/lib/server/auth";
import { getKolTrades, getKolWalletSummaries } from "@/lib/server/queries/kolTrades";
import { CaBadge, SmartsBadge } from "@/components/dash/kol";
import { Avatar } from "@/components/ui/Avatar";
import { Card, Empty, PageTitle } from "@/components/dash/ui";
import { fmtInt, fmtSol } from "@/lib/format";
import { TradeFeed } from "./TradeFeed";
import { Refresh } from "./Refresh";

export default async function KolsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  await requireUser();
  const params = await searchParams;
  const side = params.side === "buy" || params.side === "sell" ? params.side : null;
  const kol = typeof params.kol === "string" && /^[1-9]\d{0,18}$/.test(params.kol) && BigInt(params.kol) <= BigInt("9223372036854775807") ? params.kol : null;
  const [trades, shillers] = await Promise.all([getKolTrades(side, kol), getKolWalletSummaries()]);
  return <><Refresh /><PageTitle title="KOL trades" sub="Wallet activity · finalized Solana transactions · hourly collection" />
    <div className="grid items-start gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
      <Card title="Shillers with wallets" aside="24h"><ul className="divide-y divide-line">{shillers.map(shiller => <li key={shiller.id} className="py-4"><Link href={`/app/kols?kol=${shiller.id}${side ? `&side=${side}` : ""}`} className="inline-flex items-center gap-2 text-sm font-semibold hover:text-accent"><Avatar username={shiller.username} src={shiller.avatar_url} size={28} /><span className="break-all">@{shiller.username}</span></Link><div className="mt-2 flex items-center gap-3 text-xs"><SmartsBadge value={shiller.smart_followers_count} /><CaBadge value={Number(shiller.ca)} /><span className={`ml-auto font-mono ${Number(shiller.net) >= 0 ? "text-win" : "text-rug"}`}>{Number(shiller.net) > 0 ? "+" : ""}{fmtSol(shiller.net, 2)}</span></div><p className="mt-2 font-mono text-[10px] text-muted">Buy {fmtSol(shiller.buys)} · Sell {fmtSol(shiller.sells)} · {fmtInt(shiller.tokens)} tokens · {fmtInt(shiller.wallets)} wallets</p></li>)}</ul>{!shillers.length && <Empty>No linked wallets yet.</Empty>}</Card>
      <Card title="Wallet trades" aside="Latest 200 · refreshes every minute">
        <form action="/app/kols" className="mb-4 flex flex-wrap items-end gap-3 text-xs">
          <label className="grid gap-1">Side<select name="side" defaultValue={side ?? ""} className="rounded border border-line bg-ink-2 p-2"><option value="">All sides</option><option value="buy">Buys</option><option value="sell">Sells</option></select></label>
          <label className="grid gap-1">Shiller<select name="kol" defaultValue={kol ?? ""} className="max-w-56 rounded border border-line bg-ink-2 p-2"><option value="">All shillers</option>{shillers.map(k => <option key={k.id} value={k.id}>@{k.username}</option>)}</select></label>
          <button className="rounded border border-line px-3 py-2 hover:text-accent">Apply</button><Link href="/app/kols" className="px-2 py-2 text-muted hover:text-accent">Reset</Link>
        </form>
        {trades.length ? <TradeFeed trades={trades} /> : <Empty>{side || kol ? "No trades match these filters." : "Wallets of top shillers are being fetched from Frontrun; trades appear after the first hourly poll"}</Empty>}
      </Card>
    </div></>;
}

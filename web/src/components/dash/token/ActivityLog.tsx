import Link from "next/link";
import { Card, Empty } from "@/components/dash/ui";
import { fmtDateTime, fmtMultiple } from "@/lib/format";
import type { Signal } from "@/lib/server/queries/signals";
import type { KolTokenTrade, Mention } from "@/lib/server/queries/tokens";
import { clock, fmtDur, fmtMcap } from "./series";

type Tag = "CREATE" | "CALL" | "KOL BUY" | "KOL SELL" | "SIGNAL" | "MIGRATE" | "PEAK";
type Event = { t: number; tag: Tag; text: string; href?: string; detail?: string };
const tagClass: Record<Tag, string> = { CREATE: "text-muted", CALL: "text-accent", "KOL BUY": "text-win", "KOL SELL": "text-rug", SIGNAL: "text-warn", MIGRATE: "text-fg", PEAK: "text-win" };
const MAX = 400;

/** "ACTIVITY LOG": creation, every call, KOL trades, the Kairos signal, migration and the peak, merged chronologically. */
export function ActivityLog({ token, mentions, kolTrades, signal, peak }: { token: { createdAt: string | null; migratedAt: string | null; symbol: string | null }; mentions: Mention[]; kolTrades: KolTokenTrade[]; signal: Signal | null; peak: { t: number; value: number; multiple: number } | null }) {
  const events: Event[] = [];
  const created = token.createdAt ? Date.parse(token.createdAt) : null;
  const since = (ms: number) => created === null ? "" : ` · +${fmtDur((ms - created) / 1000)}`;
  if (created !== null) events.push({ t: created, tag: "CREATE", text: `${token.symbol ? `$${token.symbol}` : "Token"} created` });
  for (const m of mentions) events.push({ t: Date.parse(m.time), tag: "CALL", text: `@${m.username} posted CA${m.deleted ? " (deleted later)" : ""}${since(Date.parse(m.time))}`, href: `/app/shillers/${m.kolId}` });
  for (const k of kolTrades) events.push({ t: Date.parse(k.time), tag: k.side === "buy" ? "KOL BUY" : "KOL SELL", text: `@${k.username} ${k.side === "buy" ? "bought" : "sold"} ${k.solAmount.toFixed(2)} SOL${k.mcapSol !== null ? ` @ MC ${fmtMcap(k.mcapSol)}` : ""}${k.side === "sell" && k.holdSeconds !== null ? ` · held ${fmtDur(k.holdSeconds)}` : ""}`, href: `/app/shillers/${k.kolId}` });
  if (signal) events.push({ t: Date.parse(signal.updatedAt), tag: "SIGNAL", text: `Kairos score ${signal.score.toFixed(1)}${signal.confidence ? ` · ${signal.confidence} confidence` : ""} · ${signal.nShillers} shiller${signal.nShillers === 1 ? "" : "s"}` });
  if (token.migratedAt) events.push({ t: Date.parse(token.migratedAt), tag: "MIGRATE", text: `Left the bonding curve${since(Date.parse(token.migratedAt))}` });
  if (peak) events.push({ t: peak.t * 1000, tag: "PEAK", text: `${fmtMultiple(peak.multiple)} from the first call · ${fmtMcap(peak.value)} SOL${since(peak.t * 1000)}` });
  events.sort((a, b) => a.t - b.t);
  const shown = events.slice(-MAX);
  return <Card title="Activity log" aside={`${events.length} event${events.length === 1 ? "" : "s"}${events.length > MAX ? ` · last ${MAX}` : ""}`}>
    {shown.length ? <ol className="max-h-[420px] overflow-y-auto rounded-lg border border-line bg-ink p-2 font-mono text-[11px] leading-6">{shown.map((e, i) => <li key={`${e.t}-${e.tag}-${i}`} className="flex gap-2 whitespace-nowrap px-1 hover:bg-ink-2"><time dateTime={new Date(e.t).toISOString()} title={fmtDateTime(e.t)} className="shrink-0 text-muted">{clock(e.t)}</time><span className={`shrink-0 ${tagClass[e.tag]}`}>[{e.tag}]</span>{e.href ? <Link href={e.href} className="min-w-0 truncate text-fg/90 hover:text-accent">{e.text}</Link> : <span className="min-w-0 truncate text-fg/90">{e.text}</span>}</li>)}</ol> : <Empty>Nothing recorded for this token yet.</Empty>}
  </Card>;
}

import Link from "next/link";
import { Avatar } from "@/components/ui/Avatar";
import { SmartsBadge } from "@/components/dash/kol";
import { Card, Empty } from "@/components/dash/ui";
import { fmtDateTime, fmtFollowers, fmtInt } from "@/lib/format";
import type { MarketPoint } from "@/components/chart/types";
import type { Mention } from "@/lib/server/queries/tokens";
import { clock, fmtChange, fmtMcap, valueAt } from "./series";

function Delta({ label, from, to }: { label: string; from: number | null; to: number | null }) {
  const pct = fmtChange(from, to);
  return <span className="inline-flex items-center gap-1 rounded border border-line px-1.5 py-0.5"><span className="text-muted">{label}</span>{pct === null ? <span className="text-muted">—</span> : <span className={pct > 0.5 ? "text-win" : pct < -0.5 ? "text-rug" : "text-fg/70"}>{pct > 0 ? "+" : ""}{Math.abs(pct) >= 100 ? pct.toFixed(0) : pct.toFixed(1)}%</span>}</span>;
}

/** "TWEETS & ENGAGEMENT": every mention with engagement counts and the market-cap reaction 5 and 30 minutes after the tweet. */
export function TweetsPanel({ mentions, points }: { mentions: Mention[]; points: MarketPoint[] }) {
  const ordered = [...mentions].sort((a, b) => Date.parse(a.time) - Date.parse(b.time) || a.id - b.id);
  const views = mentions.reduce((s, m) => s + (m.views ?? 0), 0), likes = mentions.reduce((s, m) => s + (m.likes ?? 0), 0);
  return <Card title="Tweets & engagement" aside={mentions.length ? `${mentions.length} tweet${mentions.length === 1 ? "" : "s"} · ${fmtInt(likes)} likes${views ? ` · ${fmtFollowers(views)} views` : ""}` : undefined}>
    {ordered.length ? <ol className="divide-y divide-line/60">{ordered.map(m => {
      const t = Date.parse(m.time) / 1000, at = valueAt(points, t), p5 = valueAt(points, t + 300), p30 = valueAt(points, t + 1800);
      return <li key={m.id} className={`grid grid-cols-[auto_minmax(0,1fr)] gap-3 py-4 ${m.deleted ? "opacity-70" : ""}`}>
        <Avatar username={m.username} size={32} src={m.avatarUrl} />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs"><Link href={`/app/shillers/${m.kolId}`} className="truncate font-semibold hover:text-accent">@{m.username}</Link>{m.displayName && <span className="truncate text-muted">{m.displayName}</span>}<span className="font-mono text-[10px] text-muted">{fmtFollowers(m.followers)} followers</span><SmartsBadge value={m.smarts} />{m.deleted && <span className="rounded border border-rug/40 px-1.5 py-px font-mono text-[9px] uppercase tracking-wider text-rug">deleted</span>}</div>
          <p className={`mt-2 line-clamp-4 text-sm leading-relaxed break-words whitespace-pre-line text-fg/90 ${m.deleted ? "line-through decoration-rug/60" : ""}`}>{m.content || <span className="text-muted">No content available</span>}</p>
          <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-muted"><span>{fmtInt(m.likes)} likes</span><span>{fmtInt(m.retweets)} reposts</span><span>{fmtInt(m.replies)} replies</span><span>{m.views === null ? "— views" : `${fmtFollowers(m.views)} views`}</span><a className="text-fg underline decoration-line underline-offset-4 hover:text-accent" href={m.tweetUrl} target="_blank" rel="noopener noreferrer">x.com ↗</a><time dateTime={m.time} title={fmtDateTime(m.time)}>{clock(m.time)} UTC{m.minute !== null ? ` · min ${m.minute.toFixed(0)}` : ""}</time></p>
          <p className="mt-2 flex flex-wrap items-center gap-2 font-mono text-[11px]"><span className="text-muted">Reaction</span>{at === null ? <span className="text-muted">no chart data at tweet time</span> : <><span className="text-fg/80">at {fmtMcap(at)} SOL</span><Delta label="+5m" from={at} to={p5} /><Delta label="+30m" from={at} to={p30} /></>}</p>
        </div>
      </li>;
    })}</ol> : <Empty>No tweets collected for this token yet.</Empty>}
  </Card>;
}

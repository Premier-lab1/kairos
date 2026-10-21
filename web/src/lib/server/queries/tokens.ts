import "server-only";
import { query } from "@/lib/server/db";
import { getSignals, numberOrNull } from "./signals";
import type { CallMarker, MarketPoint, VolumeBar } from "@/components/chart/types";

type TokenRow = { id: string; mint: string; symbol: string | null; name: string | null; created_at: Date | null; migrated_at: Date | null; launchpad: string | null; creator: string | null; trades_synced_until: Date | null };
const tokenObject = (r: TokenRow) => ({ id: Number(r.id), mint: r.mint, symbol: r.symbol, name: r.name, createdAt: r.created_at?.toISOString() ?? null, migratedAt: r.migrated_at?.toISOString() ?? null, launchpad: r.launchpad, creator: r.creator, tradesSyncedUntil: r.trades_synced_until?.toISOString() ?? null });
export async function getTokens(q = "", page = 1) {
  const rows = await query<TokenRow & { mentions: string; shillers: string; first_mention: Date; first_shiller: string | null; peak_multiple: string | null; label: string | null; score: string | null }>(`WITH mentioned AS (
    SELECT token_id, min(tweet_time) first_mention, count(*) mentions, count(DISTINCT kol_id) shillers FROM mentions WHERE token_id IS NOT NULL GROUP BY token_id
  ) SELECT t.id, t.mint, t.symbol, t.name, t.created_at, t.migrated_at, t.launchpad, t.trades_synced_until, NULL::text creator, m.*, o.label, o.peak_multiple, s.score,
      (SELECT k.username FROM mentions first_m JOIN kols k ON k.id = first_m.kol_id WHERE first_m.token_id = t.id ORDER BY first_m.tweet_time, first_m.id LIMIT 1) AS first_shiller
    FROM mentioned m JOIN tokens t ON t.id = m.token_id
    LEFT JOIN token_outcomes o ON o.token_id = t.id AND o.rule_version = 'v2' AND o.reference = 'first_mention' AND o.horizon = interval '24 hours'
    LEFT JOIN signals s ON s.token_id = t.id AND s.scorer_version = 's1'
    WHERE ($1 = '' OR t.symbol ILIKE $2 OR t.name ILIKE $2 OR t.mint = $1)
    ORDER BY m.first_mention DESC, t.id DESC LIMIT 51 OFFSET $3`, [q, `%${q}%`, (page - 1) * 50]);
  return { hasNext: rows.length > 50, tokens: rows.slice(0, 50).map(r => ({ ...tokenObject(r), mentions: Number(r.mentions), shillers: Number(r.shillers), firstMention: r.first_mention.toISOString(), firstShiller: r.first_shiller, peak: numberOrNull(r.peak_multiple), label: r.label, score: numberOrNull(r.score) })) };
}
/** Mentions, newest first. `contentChars` clamps the tweet text (140 in lists, more on the token page). */
export async function getMentions(tokenId: number | null, limit: number | null = null, contentChars = 140) {
  const rows = await query<{ id: string; token_id: string | null; symbol: string | null; ca: string; kol_id: string; username: string; display_name: string | null; avatar_url: string | null; followers_count: number | null; smart_followers_count: number | null; tweet_id: string; tweet_time: Date; content: string | null; is_deleted: boolean; favorite_count: number | null; retweet_count: number | null; reply_count: number | null; quote_count: number | null; view_count: string | null; minute: string | null }>(`SELECT m.id, m.token_id, t.symbol, m.ca, m.kol_id, k.username, k.display_name, k.avatar_url, k.followers_count, k.smart_followers_count, m.tweet_id, m.tweet_time, left(m.content, $3) content, m.is_deleted, m.favorite_count, m.retweet_count, m.reply_count, m.quote_count, m.view_count, extract(epoch FROM (m.tweet_time - t.created_at))/60 AS "minute"
    FROM mentions m JOIN kols k ON k.id = m.kol_id LEFT JOIN tokens t ON t.id = m.token_id
    WHERE ($1::bigint IS NULL OR m.token_id = $1) ORDER BY m.tweet_time DESC, m.id DESC LIMIT $2`, [tokenId, limit, Math.max(1, Math.floor(contentChars))]);
  return rows.map(r => ({ id: Number(r.id), tokenId: numberOrNull(r.token_id), symbol: r.symbol, mint: r.ca, kolId: Number(r.kol_id), username: r.username, displayName: r.display_name, avatarUrl: r.avatar_url, followers: numberOrNull(r.followers_count), smarts: numberOrNull(r.smart_followers_count), time: r.tweet_time.toISOString(), tweetUrl: `https://x.com/${encodeURIComponent(r.username)}/status/${encodeURIComponent(r.tweet_id)}`, content: r.content, deleted: r.is_deleted, likes: numberOrNull(r.favorite_count), retweets: numberOrNull(r.retweet_count), replies: numberOrNull(r.reply_count), quotes: numberOrNull(r.quote_count), views: numberOrNull(r.view_count), minute: numberOrNull(r.minute) }));
}
export type Mention = Awaited<ReturnType<typeof getMentions>>[number];

export type KolTokenTrade = {
  signature: string; walletId: number; kolId: number; username: string; displayName: string | null; avatarUrl: string | null; smarts: number | null;
  /** ISO time */ time: string; side: "buy" | "sell"; solAmount: number; tokenAmount: number; mcapSol: number | null; program: string;
  /** position of this wallet in this token (view kol_positions): first buy, first sell after it, seconds held */
  firstBuy: string | null; firstSell: string | null; holdSeconds: number | null;
  /** this KOL's first tweet with the CA (null: traded without calling) */
  firstCall: string | null;
  /** this KOL across all tracked tokens (kol_positions): closed positions and the median hold */
  positions: number; closedPositions: number; medianHoldSeconds: number | null;
};
/**
 * On-chain trades of tracked KOL wallets in one token, oldest first. Hold time and the KOL-wide
 * hold stats are read through the kol_positions view with LATERAL joins so the planner keeps the
 * (kol_id, ts) index instead of materialising the whole view.
 */
export async function getTokenKolTrades(id: number, mint: string): Promise<KolTokenTrade[]> {
  const rows = await query<{ signature: string; wallet_id: string; kol_id: string; username: string; display_name: string | null; avatar_url: string | null; smart_followers_count: number | null; ts: Date; side: "buy" | "sell"; sol_amount: string; token_amount: string; market_cap_sol: string | null; program: string; first_buy: Date | null; first_sell: Date | null; hold_s: string | null; first_call: Date | null; positions: string | null; closed: string | null; median_hold_s: string | null }>(`WITH t AS (
      SELECT kt.signature, kt.wallet_id, kt.kol_id, kt.mint, kt.ts, kt.side, kt.sol_amount, kt.token_amount, kt.market_cap_sol, kt.program
      FROM kol_trades kt WHERE kt.mint = $2 ORDER BY kt.ts, kt.signature LIMIT 200
    ) SELECT t.signature, t.wallet_id, t.kol_id, t.ts, t.side, t.sol_amount, t.token_amount, t.market_cap_sol, t.program,
      k.username, k.display_name, k.avatar_url, k.smart_followers_count, p.first_buy, p.first_sell, p.hold_s, c.first_call, h.positions, h.closed, h.median_hold_s
    FROM t JOIN kols k ON k.id = t.kol_id
    LEFT JOIN LATERAL (SELECT first_buy, first_sell, hold_s FROM kol_positions p WHERE p.kol_id = t.kol_id AND p.wallet_id = t.wallet_id AND p.mint = t.mint LIMIT 1) p ON true
    LEFT JOIN LATERAL (SELECT min(m.tweet_time) first_call FROM mentions m WHERE m.kol_id = t.kol_id AND m.token_id = $1) c ON true
    LEFT JOIN LATERAL (SELECT count(*) positions, count(first_sell) closed, percentile_cont(0.5) WITHIN GROUP (ORDER BY hold_s) median_hold_s FROM kol_positions p WHERE p.kol_id = t.kol_id) h ON true
    ORDER BY t.ts, t.signature`, [id, mint]);
  return rows.map(r => ({ signature: r.signature, walletId: Number(r.wallet_id), kolId: Number(r.kol_id), username: r.username, displayName: r.display_name, avatarUrl: r.avatar_url, smarts: numberOrNull(r.smart_followers_count), time: r.ts.toISOString(), side: r.side, solAmount: Number(r.sol_amount), tokenAmount: Number(r.token_amount), mcapSol: numberOrNull(r.market_cap_sol), program: r.program, firstBuy: r.first_buy?.toISOString() ?? null, firstSell: r.first_sell?.toISOString() ?? null, holdSeconds: numberOrNull(r.hold_s), firstCall: r.first_call?.toISOString() ?? null, positions: Number(r.positions ?? 0), closedPositions: Number(r.closed ?? 0), medianHoldSeconds: numberOrNull(r.median_hold_s) }));
}
export async function getToken(id: number) {
  const rows = await query<TokenRow>(`SELECT t.id, t.mint, t.symbol, t.name, t.created_at, t.migrated_at, t.launchpad, t.trades_synced_until, w.address creator FROM tokens t LEFT JOIN wallets w ON w.id = t.creator_wallet_id WHERE t.id = $1 LIMIT 1`, [id]);
  if (!rows[0]) return null;
  const [mentions, signals, outcomes, chart, history, kolTrades] = await Promise.all([
    getMentions(id, 500, 400), getSignals({ tokenId: id, limit: 1 }),
    query<{ rule_version: string; reference: string; hours: string; label: string; peak_multiple: string | null; end_multiple: string | null; max_drawdown: string | null; liquidity_alive: boolean | null; migrated: boolean | null }>(`SELECT rule_version, reference, extract(epoch FROM horizon)/3600 hours, label, peak_multiple, end_multiple, max_drawdown, liquidity_alive, migrated FROM token_outcomes WHERE token_id = $1 ORDER BY rule_version, reference, horizon`, [id]),
    getTokenChart(id),
    query<{ kol_id: string; finals: string; wins: string; rugs: string }>(`WITH calls AS (
      SELECT m.kol_id, m.token_id, min(m.tweet_time) first_call FROM mentions m
      WHERE m.kol_id IN (SELECT kol_id FROM mentions WHERE token_id = $1) AND m.token_id <> $1 GROUP BY m.kol_id, m.token_id
    ) SELECT c.kol_id, count(*) finals, count(*) FILTER (WHERE o.label = 'success') wins, count(*) FILTER (WHERE o.label = 'rug') rugs
    FROM calls c JOIN token_outcomes o ON o.token_id = c.token_id
    WHERE o.rule_version = 'v2' AND o.reference = 'first_mention' AND o.horizon = interval '24 hours'
      AND o.label IN ('success','rug','dead','neutral') AND c.first_call <= o.reference_time + interval '60 minutes'
    GROUP BY c.kol_id`, [id]),
    getTokenKolTrades(id, rows[0].mint),
  ]);
  const tones = new Map(history.map(r => [Number(r.kol_id), Number(r.finals) >= 3 ? Number(r.wins) / Number(r.finals) >= .3 ? 'good' : Number(r.rugs) / Number(r.finals) >= .5 ? 'bad' : 'neutral' : 'neutral'] as const));
  const calls: CallMarker[] = mentions.map(m => ({ t: Date.parse(m.time) / 1000, username: m.username, displayName: m.displayName, followers: m.followers, avatarUrl: m.avatarUrl, href: `/app/shillers/${m.kolId}`, tone: tones.get(m.kolId) ?? 'neutral' }));
  return { ...tokenObject(rows[0]), mentions, kolTrades, signal: signals[0] ?? null, chart: { ...chart, calls }, outcomes: outcomes.map(r => ({ ruleVersion: r.rule_version, reference: r.reference, hours: Number(r.hours), label: r.label, peak: numberOrNull(r.peak_multiple), end: numberOrNull(r.end_multiple), drawdown: numberOrNull(r.max_drawdown), liquidityAlive: r.liquidity_alive, migrated: r.migrated })) };
}
export type TokenDetail = NonNullable<Awaited<ReturnType<typeof getToken>>>;

/**
 * Drop single-bucket market-cap spikes (bad trade prints, e.g. a pool swap priced against the wrong reserve):
 * a point more than 8x away from the median of its neighbours (±7 buckets) is not shown on the chart.
 */
function despike(points: MarketPoint[]): MarketPoint[] {
  if (points.length < 5) return points;
  return points.filter((p, i) => {
    const near = points.slice(Math.max(0, i - 7), i + 8).filter((_, j) => j !== Math.min(i, 7)).map(q => q.mcapSol).sort((a, b) => a - b);
    const median = near[Math.floor(near.length / 2)];
    return !(median > 0) || (p.mcapSol <= median * 8 && p.mcapSol >= median / 8);
  });
}

export async function getTokenChart(id: number): Promise<{ points: MarketPoint[]; volume: VolumeBar[]; start: number | null; end: number | null; bucketSeconds: number | null }> {
  // Filter first; snapshots are used only when the token has no trade rows.
  // Widen exceptionally long windows to retain the full history within ~1500 buckets.
  const rows = await query<{ t: string; mcap: string | null; buy: string; sell: string; start: string; finish: string; step: string }>(`WITH raw AS MATERIALIZED (
    SELECT ts, market_cap_sol, sol_amount, side, signature AS tie FROM trades WHERE token_id = $1
  ), data AS (
    SELECT * FROM raw UNION ALL
    SELECT ts, market_cap_sol, 0::numeric, NULL::text, ''::text FROM token_snapshots WHERE token_id = $1 AND NOT EXISTS (SELECT 1 FROM raw)
  ), bounds AS (
    SELECT least(coalesce((SELECT created_at FROM tokens WHERE id = $1), min(ts)), min(ts)) start, max(ts) finish FROM data
  ), settings AS (
    SELECT *, greatest(CASE WHEN finish - start <= interval '6 hours' THEN 60 WHEN finish - start <= interval '2 days' THEN 300 ELSE 900 END,
      ceil(extract(epoch FROM (finish-start))/1499/60)*60) step FROM bounds
  ), bucketed AS (
    SELECT d.*, floor(extract(epoch FROM (ts - s.start))/s.step) bucket, s.start, s.finish, s.step FROM data d CROSS JOIN settings s
  ) SELECT CASE WHEN bucket = floor(extract(epoch FROM (finish-start))/step)
      THEN extract(epoch FROM finish) ELSE extract(epoch FROM start) + bucket * step END t,
    (array_agg(market_cap_sol ORDER BY ts DESC, tie DESC) FILTER (WHERE market_cap_sol IS NOT NULL))[1] mcap,
    coalesce(sum(sol_amount) FILTER (WHERE side = 'buy'),0) buy, coalesce(sum(sol_amount) FILTER (WHERE side = 'sell'),0) sell,
    extract(epoch FROM start) start, extract(epoch FROM finish) finish, step
    FROM bucketed GROUP BY bucket, start, finish, step ORDER BY bucket LIMIT 1500`, [id]);
  const volume = rows.map(r => ({ t: Number(r.t), buySol: Number(r.buy), sellSol: Number(r.sell) }));
  const start = rows[0] ? Number(rows[0].start) : null;
  // Extend the time axis to creation without inventing a market-cap price
  // before the first observation. The final bucket is anchored at the last trade.
  if (start !== null && volume[0]?.t > start) volume.unshift({ t: start, buySol: 0, sellSol: 0 });
  return { points: despike(rows.filter(r => r.mcap !== null && Number(r.mcap) > 0).map(r => ({ t: Number(r.t), mcapSol: Number(r.mcap) }))), volume, start, end: rows[0] ? Number(rows[0].finish) : null, bucketSeconds: rows[0] ? Number(rows[0].step) : null };
}

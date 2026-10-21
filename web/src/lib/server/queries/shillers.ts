import "server-only";
import { query } from "@/lib/server/db";

type Stamp = Date | string | null;
type StatsRow = {
  id: string; username: string; display_name: string | null; avatar_url: string | null;
  followers_count: number | null; smart_followers_count: number | null; first_seen_at: Stamp;
  ca: string; calls: string; finals: string; wins: string; rugs: string;
  median_entry: number | null; last_call: Stamp;
  // kol_hold_stats (NULL when the KOL has no tracked wallet trades)
  hold_positions: string | null; hold_closed: string | null; median_hold_s: number | null;
  fast_dump_share: string | null; last_trade: Stamp;
};

export function wilsonLowerBound(wins: number, total: number): number {
  if (!total) return 0;
  const z = 1.959963984540054;
  const p = wins / total;
  return Math.max(0, (p + z * z / (2 * total) - z * Math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))) / (1 + z * z / total));
}

// A migration timestamp is not a reliable creation time. The one-second tolerance
// also catches independently recorded timestamps for the same migration event.
const entryMinute = (time: string) => `CASE WHEN t.created_at IS NOT NULL
  AND ${time} >= t.created_at
  AND (t.migrated_at IS NULL OR t.created_at < t.migrated_at - interval '1 second')
  THEN extract(epoch FROM (${time} - t.created_at)) / 60.0 END`;

const statsSql = `WITH ca_counts AS (
  SELECT kol_id, count(DISTINCT ca) AS ca, max(tweet_time) AS last_call FROM mentions GROUP BY kol_id
), first_calls AS (
  SELECT kol_id, token_id, min(tweet_time) AS first_call, max(tweet_time) AS last_call
  FROM mentions WHERE token_id IS NOT NULL GROUP BY kol_id, token_id
), token_first AS (
  SELECT token_id, min(first_call) AS first_call FROM first_calls GROUP BY token_id
), history AS (
  SELECT f.*, ${entryMinute("f.first_call")} AS entry_minute,
    CASE WHEN f.first_call <= tf.first_call + interval '60 minutes' THEN o.label END AS label
  FROM first_calls f JOIN token_first tf USING (token_id)
  JOIN tokens t ON t.id = f.token_id
  LEFT JOIN token_outcomes o ON o.token_id = f.token_id
    AND o.rule_version = 'v2' AND o.reference = 'first_mention' AND o.horizon = interval '24 hours'
)
SELECT k.id::text, k.username, k.display_name, k.avatar_url, k.followers_count, k.smart_followers_count, k.first_seen_at,
  coalesce(cc.ca, 0)::text AS ca,
  count(h.token_id)::text AS calls,
  count(*) FILTER (WHERE h.label IN ('success','rug','dead','neutral'))::text AS finals,
  count(*) FILTER (WHERE h.label = 'success')::text AS wins,
  count(*) FILTER (WHERE h.label = 'rug')::text AS rugs,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY h.entry_minute) AS median_entry,
  cc.last_call,
  hs.positions::text AS hold_positions, hs.closed_positions::text AS hold_closed, hs.median_hold_s,
  hs.fast_dump_share::text AS fast_dump_share, hs.last_trade
FROM kols k LEFT JOIN ca_counts cc ON cc.kol_id = k.id LEFT JOIN history h ON h.kol_id = k.id
LEFT JOIN kol_hold_stats hs ON hs.kol_id = k.id
WHERE ($1::text IS NULL OR strpos(lower(k.username), lower($1)) > 0)
  AND ($2::bigint IS NULL OR k.id = $2)
GROUP BY k.id, cc.ca, cc.last_call, hs.positions, hs.closed_positions, hs.median_hold_s, hs.fast_dump_share, hs.last_trade`;

function stats(row: StatsRow) {
  const finals = Number(row.finals), wins = Number(row.wins), rugs = Number(row.rugs);
  return { ...row, ca: Number(row.ca), calls: Number(row.calls), finals, wins, rugs,
    median_entry: row.median_entry === null ? null : Number(row.median_entry),
    hold: { positions: Number(row.hold_positions ?? 0), closed_positions: Number(row.hold_closed ?? 0),
      median_hold_s: row.median_hold_s === null ? null : Number(row.median_hold_s),
      fast_dump_share: row.fast_dump_share === null ? null : Number(row.fast_dump_share) },
    winRate: finals ? wins / finals : null, rugRate: finals ? rugs / finals : null,
    lowerBound: wilsonLowerBound(wins, finals) };
}

export type ShillerStats = ReturnType<typeof stats>;
export const shillerSorts = ["lb", "finals", "rug", "followers", "recent", "smarts", "ca"] as const;
export type ShillerSort = typeof shillerSorts[number];

export async function getShillers(search: string, sort: ShillerSort, limit = 200, onlyFinals = false) {
  const order: Record<ShillerSort, string> = {
    smarts: "smart_followers_count DESC NULLS LAST", ca: "ca::bigint DESC",
    followers: "followers_count DESC NULLS LAST", recent: "last_call DESC NULLS LAST",
    finals: "finals::bigint DESC", rug: "rugs::numeric / nullif(finals::double precision, 0) DESC NULLS LAST",
    lb: `CASE WHEN finals::double precision = 0 THEN 0 ELSE
      (wins::double precision/finals::double precision + 3.841458820694124/(2*finals::double precision)
      - 1.959963984540054 * sqrt((wins::double precision/finals::double precision)*(1-wins::double precision/finals::double precision)/finals::double precision
      + 3.841458820694124/(4*finals::double precision*finals::double precision))) / (1+3.841458820694124/finals::double precision) END DESC`,
  };
  return (await query<StatsRow>(`SELECT * FROM (${statsSql}) ranked ${onlyFinals ? "WHERE finals::bigint > 0" : ""} ORDER BY ${order[sort]}, finals::bigint DESC, calls::bigint DESC, username, id LIMIT ${Math.min(200, Math.max(1, Math.trunc(limit)))}`, [search || null, null])).map(stats);
}

export async function getOutcomeBase() {
  const [row] = await query<{ finals: string; wins: string; pending: string; no_data: string }>(`
    SELECT count(*) FILTER (WHERE label IN ('success','rug','dead','neutral'))::text AS finals,
      count(*) FILTER (WHERE label = 'success')::text AS wins,
      count(*) FILTER (WHERE label = 'pending')::text AS pending,
      count(*) FILTER (WHERE label = 'no_data')::text AS no_data
    FROM token_outcomes WHERE rule_version = 'v2' AND reference = 'first_mention'
      AND horizon = interval '24 hours'`);
  const finals = Number(row.finals);
  return { finals, pending: Number(row.pending), noData: Number(row.no_data),
    defaultUsed: finals < 100, winRate: finals < 100 ? 0.05 : Number(row.wins) / finals };
}

export type ShillerCall = {
  id: string; token_id: string | null; symbol: string | null; mint: string;
  tweet_id: string; tweet_time: Stamp; is_deleted: boolean;
  minute: string | null; first_shill: boolean | null; attributable: boolean | null;
  outcome_1h: string | null; outcome_24h: string | null; peak_multiple: string | null;
};

export async function getShiller(id: string) {
  const [row] = await query<StatsRow>(`${statsSql} LIMIT 1`, [null, id]);
  if (!row) return null;
  const [history, wallets, calls, base] = await Promise.all([
    query<{ username: string; changed_at: Stamp }>(
      "SELECT username, changed_at FROM kol_username_history WHERE kol_id = $1 ORDER BY changed_at DESC NULLS LAST, username", [id]),
    query<{ address: string; relation: string }>(
      "SELECT w.address, kw.relation FROM kol_wallets kw JOIN wallets w ON w.id = kw.wallet_id WHERE kw.kol_id = $1 ORDER BY kw.relation, w.address", [id]),
    query<ShillerCall>(`SELECT m.id::text, m.token_id::text, t.symbol, coalesce(t.mint, m.ca) AS mint,
      m.tweet_id, m.tweet_time, m.is_deleted, ${entryMinute("m.tweet_time")} AS minute,
      m.tweet_time = tf.first_call AS first_shill,
      kf.first_call <= tf.first_call + interval '60 minutes' AS attributable,
      o1.label AS outcome_1h, o24.label AS outcome_24h, o24.peak_multiple
    FROM mentions m LEFT JOIN tokens t ON t.id = m.token_id
    LEFT JOIN LATERAL (SELECT min(tweet_time) AS first_call FROM mentions WHERE token_id = m.token_id) tf ON true
    LEFT JOIN LATERAL (SELECT min(tweet_time) AS first_call FROM mentions WHERE token_id = m.token_id AND kol_id = m.kol_id) kf ON true
    LEFT JOIN token_outcomes o1 ON o1.token_id = m.token_id AND o1.rule_version = 'v2'
      AND o1.reference = 'first_mention' AND o1.horizon = interval '1 hour'
    LEFT JOIN token_outcomes o24 ON o24.token_id = m.token_id AND o24.rule_version = 'v2'
      AND o24.reference = 'first_mention' AND o24.horizon = interval '24 hours'
    WHERE m.kol_id = $1 ORDER BY m.tweet_time DESC, m.id DESC LIMIT 300`, [id]),
    getOutcomeBase(),
  ]);
  const summary = stats(row);
  const shrunkRate = (summary.wins + 10 * base.winRate) / (summary.finals + 10);
  return { ...summary, history, wallets, callsList: calls, base,
    lift: base.winRate > 0 ? shrunkRate / base.winRate : null };
}

export async function getRugComparison() {
  return (await query<StatsRow>(`SELECT * FROM (${statsSql}) ranked
    WHERE finals::bigint >= 3 ORDER BY ca::bigint DESC, id LIMIT 40`, [null, null])).map(stats);
}

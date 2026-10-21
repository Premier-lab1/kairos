import "server-only";
import { query } from "@/lib/server/db";

export type NarrativeWindow = "6h" | "24h";
export type Narrative = {
  narrative: string; tokens: string; shills: string; shillers: string; migrations: string;
  labeled: string; successes: string; rugs: string; median_peak: string | null; max_peak: string | null;
  share: string; success_rate: string | null; rug_rate: string | null;
};
export type NarrativeShiller = { narrative: string; id: string; username: string; avatar_url: string | null; shills: string };
export type PumpingToken = { id: string; name: string | null; symbol: string | null; mint: string;
  narrative: string; first_cap: string; last_cap: string; growth: string; points: string };

const hours = (window: NarrativeWindow) => window === "6h" ? 6 : 24;

// Aggregate mentions and outcomes separately: repeated shills must not weight
// token outcomes. NULL classifications remain visible as "unclassified".
export const NARRATIVES_SQL = `WITH recent AS MATERIALIZED (
  SELECT m.token_id, m.kol_id, coalesce(t.narrative, 'unclassified') narrative
  FROM mentions m JOIN tokens t ON t.id=m.token_id
  WHERE m.tweet_time >= now()-make_interval(hours => $1::int) AND m.tweet_time < now()
), activity AS (
  SELECT narrative, count(DISTINCT token_id) tokens, count(*) shills,
    count(DISTINCT kol_id) shillers FROM recent GROUP BY narrative
), outcomes AS (
  SELECT r.narrative, count(*) FILTER (WHERE o.label IN ('success','rug','dead','neutral')) labeled,
    count(*) FILTER (WHERE o.label='success') successes, count(*) FILTER (WHERE o.label='rug') rugs,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY o.peak_multiple)
      FILTER (WHERE o.label IN ('success','rug','dead','neutral')) median_peak,
    max(o.peak_multiple) FILTER (WHERE o.label IN ('success','rug','dead','neutral')) max_peak
  FROM (SELECT DISTINCT narrative,token_id FROM recent) r
  LEFT JOIN token_outcomes o ON o.token_id=r.token_id AND o.horizon=interval '1 hour'
    AND o.rule_version='v2' AND o.reference='first_mention'
  GROUP BY r.narrative
), migrations AS (
  SELECT coalesce(narrative,'unclassified') narrative, count(*) migrations FROM tokens
  WHERE migrated_at >= now()-make_interval(hours => $1::int) AND migrated_at < now() GROUP BY 1
), keys AS (SELECT narrative FROM activity UNION SELECT narrative FROM migrations)
SELECT k.narrative, coalesce(a.tokens,0)::text tokens, coalesce(a.shills,0)::text shills,
  coalesce(a.shillers,0)::text shillers, coalesce(m.migrations,0)::text migrations,
  coalesce(o.labeled,0)::text labeled, coalesce(o.successes,0)::text successes, coalesce(o.rugs,0)::text rugs,
  o.median_peak::text, o.max_peak::text,
  (coalesce(a.shills,0)::numeric / nullif(sum(a.shills) OVER (),0))::text share,
  (o.successes::numeric / nullif(o.labeled,0))::text success_rate,
  (o.rugs::numeric / nullif(o.labeled,0))::text rug_rate
FROM keys k LEFT JOIN activity a USING(narrative) LEFT JOIN outcomes o USING(narrative)
LEFT JOIN migrations m USING(narrative) ORDER BY coalesce(a.shills,0) DESC,k.narrative LIMIT 32`;

export const NARRATIVE_SHILLERS_SQL = `WITH counts AS (
  SELECT coalesce(t.narrative,'unclassified') narrative,m.kol_id,count(*) shills
  FROM mentions m JOIN tokens t ON t.id=m.token_id
  WHERE m.tweet_time >= now()-make_interval(hours => $1::int) AND m.tweet_time < now()
  GROUP BY 1,m.kol_id
), ranked AS (
  SELECT *,row_number() OVER (PARTITION BY narrative ORDER BY shills DESC,kol_id) rank FROM counts
) SELECT r.narrative,k.id::text,k.username,k.avatar_url,r.shills::text FROM ranked r JOIN kols k ON k.id=r.kol_id
WHERE r.rank<=3 ORDER BY r.narrative,r.rank LIMIT 96`;

// Timestamp ties use signature as a deterministic tiebreaker. Invalid caps
// are excluded before selecting endpoints; require two different timestamps.
export const PUMPING_SQL = `WITH spans AS MATERIALIZED (
  SELECT token_id, min(ts) first_ts, max(ts) last_ts, count(*) points
  FROM trades WHERE ts >= now()-make_interval(hours => $1::int) AND ts < now()
    AND market_cap_sol > 0
  GROUP BY token_id HAVING count(*) >= 2 AND min(ts) < max(ts)
), endpoints AS (
  SELECT s.token_id,s.points,f.market_cap_sol first_cap,l.market_cap_sol last_cap
  FROM spans s
  CROSS JOIN LATERAL (SELECT market_cap_sol FROM trades
    WHERE token_id=s.token_id AND ts=s.first_ts AND market_cap_sol>0
    ORDER BY signature LIMIT 1) f
  CROSS JOIN LATERAL (SELECT market_cap_sol FROM trades
    WHERE token_id=s.token_id AND ts=s.last_ts AND market_cap_sol>0
    ORDER BY signature DESC LIMIT 1) l
), ranked AS (
  SELECT *,last_cap/first_cap-1 growth FROM endpoints WHERE last_cap>first_cap
  ORDER BY growth DESC,token_id LIMIT 20
) SELECT t.id::text,t.name,t.symbol,t.mint,coalesce(t.narrative,'unclassified') narrative,
  r.first_cap::text,r.last_cap::text,r.growth::text,r.points::text
FROM ranked r JOIN tokens t ON t.id=r.token_id ORDER BY r.growth DESC,t.id LIMIT 20`;

// Window aggregates over all trades/mentions: slow (~9 s on the server), so cached for 5 minutes.
const SLOW_TTL_MS = 300_000;

export function getNarratives(window: NarrativeWindow = "24h") {
  return query<Narrative>(NARRATIVES_SQL, [hours(window)], SLOW_TTL_MS);
}
export function getNarrativeShillers(window: NarrativeWindow) {
  return query<NarrativeShiller>(NARRATIVE_SHILLERS_SQL, [hours(window)], SLOW_TTL_MS);
}
export function getPumpingTokens(window: NarrativeWindow) {
  return query<PumpingToken>(PUMPING_SQL, [hours(window)], SLOW_TTL_MS);
}

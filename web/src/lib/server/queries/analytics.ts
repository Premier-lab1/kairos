import "server-only";

import { query } from "@/lib/server/db";



export type Activity = { label: string; count: string };

// Exact rolling windows, including zero buckets. Labels are explicitly UTC.

export async function getHourlyActivity() {

  return query<Activity>(`WITH counts AS (
    SELECT floor(extract(epoch FROM (tweet_time - (now() - interval '48 hours'))) / 3600)::int AS bucket, count(*) AS count
    FROM mentions WHERE tweet_time >= now() - interval '48 hours' AND tweet_time < now() GROUP BY bucket
  ) SELECT to_char((now() - interval '48 hours' + n * interval '1 hour') AT TIME ZONE 'UTC', 'DD Mon HH24:MI') AS label,
    coalesce(c.count, 0)::text AS count
    FROM generate_series(0, 47) n LEFT JOIN counts c ON c.bucket = n ORDER BY n LIMIT 48`);

}

export async function getMindshare() {

  return query<{ id: string; username: string; avatar_url: string | null; smart_followers_count: number | null; ca: string; count: string; share: string }>(`

    WITH recent AS (

      SELECT kol_id, count(*) AS count FROM mentions

      WHERE tweet_time >= now() - interval '24 hours' AND tweet_time < now() GROUP BY kol_id

    ), ranked AS (

      SELECT *, count::numeric / sum(count) OVER () AS share FROM recent

      ORDER BY count DESC, kol_id LIMIT 10

    ) SELECT k.id::text, k.username, k.avatar_url, k.smart_followers_count, r.count::text, r.share::text,

      (SELECT count(DISTINCT ca)::text FROM mentions WHERE kol_id = k.id) AS ca

    FROM ranked r JOIN kols k ON k.id = r.kol_id ORDER BY r.count DESC, k.id LIMIT 10`);

}

export async function getMostShilled() {

  return query<{ id: string; symbol: string | null; mint: string; count: string; label: string | null }>(`

    WITH ranked AS (

      SELECT token_id, count(DISTINCT kol_id) AS count FROM mentions

      WHERE tweet_time >= now() - interval '24 hours' AND tweet_time < now() AND token_id IS NOT NULL

      GROUP BY token_id ORDER BY count DESC, token_id LIMIT 10

    ) SELECT t.id::text, t.symbol, t.mint, r.count::text, o.label

    FROM ranked r JOIN tokens t ON t.id = r.token_id

    LEFT JOIN token_outcomes o ON o.token_id = t.id AND o.rule_version = 'v2'

      AND o.reference = 'first_mention' AND o.horizon = interval '24 hours'

    ORDER BY r.count DESC, t.id LIMIT 10`);

}

export async function getCaDistribution() {

  return query<Activity>(`WITH counts AS (

    SELECT k.id, count(DISTINCT m.ca) AS ca FROM kols k LEFT JOIN mentions m ON m.kol_id = k.id GROUP BY k.id

  ), buckets AS (

    SELECT CASE WHEN ca < 10 THEN '< 10' WHEN ca < 20 THEN '10–19' ELSE '≥ 20' END AS label FROM counts

  ) SELECT label, count(*)::text AS count FROM buckets GROUP BY label ORDER BY label LIMIT 3`);

}

export async function getDailyCalls(id: string) {

  return query<Activity>(`WITH buckets AS (

    SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') - n * interval '1 day' AS start

    FROM generate_series(0, 29) n

  ) SELECT to_char(b.start AT TIME ZONE 'UTC', 'DD Mon') AS label, count(m.id)::text AS count

    FROM buckets b LEFT JOIN mentions m ON m.kol_id = $1 AND m.tweet_time >= b.start

      AND m.tweet_time < b.start + interval '1 day' AND m.tweet_time < now()

    GROUP BY b.start ORDER BY b.start LIMIT 30`, [id]);

}

export async function getShillerOutcomes(id: string) {

  return query<Activity>(`WITH called AS (

    SELECT ca, max(token_id) AS token_id FROM mentions WHERE kol_id = $1 GROUP BY ca

  ) SELECT coalesce(o.label, 'no_data') AS label, count(*)::text AS count

    FROM called c LEFT JOIN token_outcomes o ON o.token_id = c.token_id

      AND o.rule_version = 'v2' AND o.reference = 'first_mention' AND o.horizon = interval '24 hours'

    GROUP BY coalesce(o.label, 'no_data') ORDER BY label LIMIT 6`, [id]);

}


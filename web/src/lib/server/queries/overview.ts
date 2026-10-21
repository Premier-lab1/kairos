import "server-only";
import { query } from "@/lib/server/db";
import { getSignals } from "./signals";
import { getMentions } from "./tokens";

export async function getOverview() {
  const [stats, distribution, credits, jobs, mentions, signals] = await Promise.all([
    query<{ tokens: string; seen: string; migrated: string; mentions: string; recent_mentions: string; shillers: string; trades: string; latest: Date | null }>(`SELECT
      (SELECT count(*) FROM tokens) tokens,
      (SELECT count(*) FROM tokens WHERE first_seen_at >= now() - interval '24 hours') seen,
      (SELECT count(*) FROM tokens WHERE migrated_at >= now() - interval '24 hours') migrated,
      (SELECT count(*) FROM mentions) mentions,
      (SELECT count(*) FROM mentions WHERE tweet_time >= now() - interval '24 hours') recent_mentions,
      (SELECT count(*) FROM kols) shillers, (SELECT count(*) FROM trades) trades,
      (SELECT max(tweet_time) FROM mentions) latest`),
    query<{ hours: string; label: string; count: string }>(`SELECT extract(epoch FROM horizon)/3600 AS hours, label, count(*) FROM token_outcomes WHERE rule_version = 'v2' AND reference = 'first_mention' AND horizon IN (interval '1 hour', interval '24 hours') GROUP BY horizon, label ORDER BY horizon, label`),
    query<{ provider: string; used: string }>(`SELECT provider, sum(credits) used FROM api_calls WHERE ts >= (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AND ts < (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') + interval '1 day' GROUP BY provider`),
    query<{ kind: string; count: string }>(`SELECT kind, count(*) FROM jobs WHERE status = 'pending' GROUP BY kind ORDER BY count(*) DESC, kind LIMIT 5`),
    getMentions(null, 20), getSignals({ limit: 10 }),
  ]);
  const s = stats[0];
  return { stats: { tokens: Number(s.tokens), seen: Number(s.seen), migrated: Number(s.migrated), mentions: Number(s.mentions), recentMentions: Number(s.recent_mentions), shillers: Number(s.shillers), trades: Number(s.trades), latest: s.latest?.toISOString() ?? null }, distribution: distribution.map(r => ({ hours: Number(r.hours), label: r.label, count: Number(r.count) })), credits: credits.map(r => ({ provider: r.provider, used: Number(r.used) })), jobs: jobs.map(r => ({ kind: r.kind, count: Number(r.count) })), mentions, signals };
}

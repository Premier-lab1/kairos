"""Read-only reports for shiller outcomes and collection coverage."""

import argparse
import asyncio
import math
import sys

import asyncpg

from kairos.config import get_settings

HORIZONS = {"1h": "1 hour", "24h": "24 hours", "7d": "7 days"}


async def overview(db, rule_version="v1"):
    """Return collection counts; accepts an asyncpg connection or pool."""
    result = {}
    result["tokens"] = dict(await db.fetchrow("""
        SELECT count(*) AS total, count(created_at) AS with_created_at,
               count(migrated_at) AS migrated,
               count(trades_synced_until) AS with_trades_synced_until FROM tokens
    """))
    result["mentions"] = dict(await db.fetchrow("""
        SELECT count(*) AS total, count(token_id) AS linked_to_tokens FROM mentions
    """))
    result["mentions"]["by_source"] = {
        r["source"]: r["count"] for r in await db.fetch(
            "SELECT source, count(*) FROM mentions GROUP BY source ORDER BY source"
        )
    }
    result["kols"] = dict(await db.fetchrow("""
        SELECT count(*) AS total, count(*) FILTER (WHERE tracked) AS tracked FROM kols
    """))
    result["outcomes"] = [dict(r) for r in await db.fetch("""
        SELECT reference, horizon, label, count(*) AS count FROM token_outcomes
        WHERE rule_version = $1 GROUP BY reference, horizon, label
        ORDER BY reference, horizon, label
    """, rule_version)]
    result["jobs"] = [dict(r) for r in await db.fetch("""
        SELECT kind, status, count(*) AS count FROM jobs
        GROUP BY kind, status ORDER BY kind, status
    """)]
    return result


def wilson_lower_bound(successes, total):
    """Lower endpoint of the two-sided Wilson 95% interval (z = 1.96)."""
    if not total:
        return 0.0
    p = successes / total
    z2 = 1.96 ** 2
    return max(0.0, (p + z2 / (2 * total) - 1.96 * math.sqrt(
        p * (1 - p) / total + z2 / (4 * total ** 2)
    )) / (1 + z2 / total))


async def kol_leaderboard(db, reference="first_mention", horizon="24 hours",
                          min_tokens=3, limit=30, rule_version="v1"):
    """Rank KOLs using selected final outcomes, with min_tokens final observations.

    first_mention is the token's first mention by anyone, not this KOL's
    first mention. Any mention attributes the token to a KOL, once only.
    Rates and median peak_multiple use final outcomes only. Undefined rates
    are None; the Wilson lower bound for an empty sample is zero.
    """
    if reference not in ("first_mention", "creation"):
        raise ValueError("invalid reference")
    if min_tokens < 0 or limit < 0:
        raise ValueError("min_tokens and limit must be nonnegative")
    rows = await db.fetch("""
        WITH mentioned AS (
            SELECT kol_id, token_id, max(tweet_time) AS last_mention_at
            FROM mentions GROUP BY kol_id, token_id
        )
        SELECT k.id AS kol_id, k.username, k.followers_count AS followers, k.tracked,
               count(m.token_id) AS tokens_shilled,
               count(o.token_id) AS tokens_with_final_outcome,
               count(*) FILTER (WHERE o.label = 'success') AS success,
               count(*) FILTER (WHERE o.label = 'rug') AS rug,
               count(*) FILTER (WHERE o.label = 'dead') AS dead,
               count(*) FILTER (WHERE o.label = 'neutral') AS neutral,
               percentile_cont(0.5) WITHIN GROUP
                   (ORDER BY o.peak_multiple::double precision) AS median_peak_multiple,
               max(m.last_mention_at) AS last_mention_at
        FROM kols k LEFT JOIN mentioned m ON m.kol_id = k.id
        LEFT JOIN token_outcomes o ON o.token_id = m.token_id
            AND o.reference = $1 AND o.horizon = $2::text::interval
            AND o.rule_version = $4
            AND o.label IN ('success', 'rug', 'dead', 'neutral')
        GROUP BY k.id HAVING count(o.token_id) >= $3
    """, reference, horizon, min_tokens, rule_version)
    result = []
    for row in rows:
        item = dict(row)
        n = item["tokens_with_final_outcome"]
        item["success_rate"] = item["success"] / n if n else None
        item["rug_rate"] = item["rug"] / n if n else None
        item["wilson_lower_bound"] = wilson_lower_bound(item["success"], n)
        result.append(item)
    result.sort(key=lambda r: (-r["wilson_lower_bound"],
                              -r["tokens_with_final_outcome"], r["kol_id"]))
    return result[:limit]


async def credits(db):
    """Credits since UTC midnight, rolling 7 days, and all time; all-time calls."""
    return [dict(r) for r in await db.fetch("""
        SELECT provider,
            coalesce(sum(credits) FILTER (WHERE ts >=
                (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                AND ts <= now()), 0) AS today,
            coalesce(sum(credits) FILTER (WHERE ts >= now() - interval '168 hours'
                AND ts <= now()), 0) AS last_7_days,
            sum(credits) AS total, count(*) AS calls,
            count(*) FILTER (WHERE NOT ok) AS failed_calls
        FROM api_calls GROUP BY provider ORDER BY provider
    """)]


def _table(headers, rows):
    def cell(value):
        if value is None:
            return "-"
        if isinstance(value, float):
            return f"{value:.4f}"
        return " ".join(str(value).split())

    rows = [[cell(v) for v in row] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    def line(row):
        return "  ".join(v.ljust(w) for v, w in zip(row, widths)).rstrip()
    return "\n".join([line(headers), line(["-" * w for w in widths]),
                      *map(line, rows)])


def format_report(summary, leaderboard, credit_rows, *, reference="first_mention",
                  horizon="24 hours", rule_version="v1"):
    """Format pre-fetched data as plain-text tables without printing."""
    counts = [(section, key, value)
              for section in ("tokens", "mentions", "kols")
              for key, value in summary[section].items() if key != "by_source"]
    counts.extend(("mentions source", key, value)
                  for key, value in summary["mentions"]["by_source"].items())
    columns = ["username", "followers", "tracked", "tokens_shilled",
               "tokens_with_final_outcome", "success", "rug", "dead", "neutral",
               "success_rate", "wilson_lower_bound", "rug_rate",
               "median_peak_multiple", "last_mention_at"]
    return "\n\n".join([
        "Overview\n" + _table(["Entity", "Metric", "Count"], counts),
        f"Outcomes ({rule_version})\n" + _table(["Reference", "Horizon", "Label", "Count"],
            [[r[k] for k in ("reference", "horizon", "label", "count")]
             for r in summary["outcomes"]]),
        "Jobs\n" + _table(["Kind", "Status", "Count"],
            [[r[k] for k in ("kind", "status", "count")] for r in summary["jobs"]]),
        f"KOL leaderboard ({rule_version}, {reference}, {horizon}; rates 0-1)\n" +
            _table(columns, [[r[k] for k in columns] for r in leaderboard]),
        "Credits\n" + _table(
            ["Provider", "Today (UTC)", "Last 7 days", "Total", "Calls", "Failed calls"],
            [[r[k] for k in ("provider", "today", "last_7_days", "total", "calls",
                             "failed_calls")] for r in credit_rows]),
    ])


def _nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", choices=("first_mention", "creation"),
                        default="first_mention")
    parser.add_argument("--horizon", choices=HORIZONS, default="24h")
    parser.add_argument("--min-tokens", type=_nonnegative, default=3,
                        help="minimum number of tokens with final outcomes")
    parser.add_argument("--limit", type=_nonnegative, default=30)
    parser.add_argument("--dsn")
    parser.add_argument("--rule-version", choices=("v1", "v2"), default="v2")
    args = parser.parse_args(argv)
    args.horizon = HORIZONS[args.horizon]
    return args


async def _main(args):
    conn = await asyncpg.connect(args.dsn or get_settings().database_url)
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            summary = await overview(conn, args.rule_version)
            leaders = await kol_leaderboard(conn, args.reference, args.horizon,
                                            args.min_tokens, args.limit, args.rule_version)
            costs = await credits(conn)
        print(format_report(summary, leaders, costs, reference=args.reference,
                            horizon=args.horizon, rule_version=args.rule_version))
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(_main(parse_args()))

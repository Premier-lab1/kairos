import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from dbutil import reset_schema
from kairos.db import migrate
from kairos.report import credits, format_report, kol_leaderboard, overview, parse_args
from kairos.report import wilson_lower_bound

DSN = os.environ.get("KAIROS_TEST_PG", "postgresql://kairos@localhost:5434") + "/kairos_test_report"
NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


@pytest.fixture
async def db():
    conn = await asyncpg.connect(DSN)
    try:
        await reset_schema(conn)
        await migrate(conn)
        yield conn
    finally:
        await conn.close()


async def seed(db, username="alice", labels=("success", "rug", "dead", "neutral",
                                            "pending", "no_data", None)):
    kol = await db.fetchval("""INSERT INTO kols (username, followers_count, tracked)
                              VALUES ($1, 100, true) RETURNING id""", username)
    tokens = []
    for i, label in enumerate(labels):
        token = await db.fetchval("""INSERT INTO tokens (mint, source)
                                    VALUES ($1, 'test') RETURNING id""", f"{kol}-{i}")
        tokens.append(token)
        await db.execute("""INSERT INTO mentions
            (kol_id, chain, ca, token_id, tweet_id, tweet_time, source)
            VALUES ($1, 'SOLANA', $2, $3, $2, $4, 'test')""",
            kol, str(token), token, NOW + timedelta(minutes=i))
        if label:
            await outcome(db, token, label, peak=i + 1)
    return kol, tokens


async def outcome(db, token, label, reference="first_mention", horizon="24 hours",
                  version="v1", peak=1):
    await db.execute("""INSERT INTO token_outcomes
        (token_id, horizon, rule_version, reference, reference_time, label, peak_multiple)
        VALUES ($1, $2::text::interval, $3, $4, $5, $6, $7)""",
        token, horizon, version, reference, NOW, label, Decimal(peak))


async def test_overview(db):
    kol, tokens = await seed(db)
    await db.execute("""UPDATE tokens SET created_at = now(), migrated_at = now(),
                        trades_synced_until = now() WHERE id = $1""", tokens[0])
    await db.execute("INSERT INTO kols (username) VALUES ('untracked')")
    await db.execute("""INSERT INTO mentions
        (kol_id, chain, ca, tweet_id, tweet_time, source)
        VALUES ($1, 'SOLANA', 'unknown', 'unlinked', now(), 'search')""", kol)
    await outcome(db, tokens[0], "success", version="v2")
    await db.execute("""INSERT INTO jobs (kind, key, status) VALUES
                        ('fetch', 'a', 'done'), ('fetch', 'b', 'done'),
                        ('fetch', 'c', 'failed'), ('sync', 'a', 'pending')""")
    data = await overview(db)
    assert data["tokens"] == dict(total=7, with_created_at=1, migrated=1,
                                  with_trades_synced_until=1)
    assert data["mentions"] == dict(total=8, linked_to_tokens=7,
                                    by_source={"test": 7, "search": 1})
    assert data["kols"] == dict(total=2, tracked=1)
    assert len(data["outcomes"]) == 6
    assert sum(r["count"] for r in data["outcomes"]) == 6
    assert data["jobs"] == [dict(kind="fetch", status="done", count=2),
                            dict(kind="fetch", status="failed", count=1),
                            dict(kind="sync", status="pending", count=1)]


async def test_final_labels_and_median(db):
    await seed(db)
    row, = await kol_leaderboard(db)
    assert row["tokens_shilled"] == 7
    assert row["tokens_with_final_outcome"] == 4
    assert [row[k] for k in ("success", "rug", "dead", "neutral")] == [1, 1, 1, 1]
    assert row["success_rate"] == row["rug_rate"] == 0.25
    assert row["median_peak_multiple"] == 2.5
    assert row["last_mention_at"] == NOW + timedelta(minutes=6)
    assert row["followers"] == 100 and row["tracked"] is True


async def test_min_tokens_uses_final_sample(db):
    await seed(db)
    assert len(await kol_leaderboard(db, min_tokens=4)) == 1
    assert await kol_leaderboard(db, min_tokens=5) == []


@pytest.mark.parametrize("successes,total,expected", [
    (3, 3, 0.4384939196), (10, 20, 0.2992949144), (0, 3, 0), (0, 0, 0),
])
def test_wilson_hand_computed(successes, total, expected):
    assert wilson_lower_bound(successes, total) == pytest.approx(expected, abs=1e-9)


async def test_ordering_and_limit(db):
    await seed(db, "half", ["success"] * 10 + ["rug"] * 10)
    await seed(db, "perfect", ["success"] * 3)
    await seed(db, "zero_small", ["rug"] * 3)
    await seed(db, "zero_large", ["rug"] * 4)
    rows = await kol_leaderboard(db)
    assert [r["username"] for r in rows] == ["perfect", "half", "zero_large", "zero_small"]
    assert [r["username"] for r in await kol_leaderboard(db, limit=1)] == ["perfect"]
    assert await kol_leaderboard(db, limit=0) == []


@pytest.mark.parametrize("reference,horizon,label", [
    ("creation", "24 hours", "rug"), ("first_mention", "1 hour", "dead"),
    ("first_mention", "7 days", "neutral"),
])
async def test_reference_horizon_and_version(db, reference, horizon, label):
    _, tokens = await seed(db, labels=["success"])
    await outcome(db, tokens[0], label, reference, horizon)
    await outcome(db, tokens[0], "success", reference, horizon, version="v2")
    row, = await kol_leaderboard(db, reference, horizon, min_tokens=1)
    assert row[label] == row["tokens_with_final_outcome"] == 1
    assert row["success"] == 0


async def test_duplicate_and_shared_mentions(db):
    kol, tokens = await seed(db, labels=["success"])
    other = await db.fetchval("INSERT INTO kols (username) VALUES ('other') RETURNING id")
    for who in (kol, other):
        await db.execute("""INSERT INTO mentions
            (kol_id, chain, ca, token_id, tweet_id, tweet_time, source)
            VALUES ($1, 'SOLANA', 'shared', $2, $3, $4, 'search')""",
            who, tokens[0], f"extra-{who}", NOW + timedelta(days=1))
    rows = await kol_leaderboard(db, min_tokens=1)
    assert len(rows) == 2
    assert all(r["tokens_shilled"] == r["success"] == 1 for r in rows)
    assert all(r["last_mention_at"] == NOW + timedelta(days=1) for r in rows)


async def test_empty_and_no_final(db):
    assert await kol_leaderboard(db) == []
    assert await credits(db) == []
    assert (await overview(db))["tokens"]["total"] == 0
    await seed(db, labels=["pending", "no_data"])
    row, = await kol_leaderboard(db, min_tokens=0)
    assert row["success_rate"] is row["rug_rate"] is row["median_peak_multiple"] is None
    assert row["wilson_lower_bound"] == 0


async def test_credits_utc_windows(db):
    async with db.transaction():
        await db.execute("SET LOCAL TIME ZONE 'Pacific/Honolulu'")
        now = await db.fetchval("SELECT now()")
        midnight = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        for ts, cost, ok in [(midnight, 2, True), (midnight - timedelta(seconds=1), 3, False),
                             (now - timedelta(days=7), 5, True),
                             (now - timedelta(days=7, seconds=1), 7, False)]:
            await db.execute("""INSERT INTO api_calls (ts, provider, endpoint, credits, ok)
                                VALUES ($1, 'helius', 'test', $2, $3)""", ts, cost, ok)
        await db.execute("""INSERT INTO api_calls (provider, endpoint, credits, ok)
                            VALUES ('frontrun', 'test', 11, true)""")
        rows = await credits(db)
    assert rows == [dict(provider="frontrun", today=11, last_7_days=11, total=11,
                         calls=1, failed_calls=0),
                    dict(provider="helius", today=2, last_7_days=10, total=17,
                         calls=4, failed_calls=2)]


async def test_format_and_cli_utf8(db):
    username = "kol_\U0001f680"
    await seed(db, username, ["success"] * 3)
    report = format_report(await overview(db), await kol_leaderboard(db), await credits(db))
    for text in ("Overview", "Outcomes", "Jobs", "KOL leaderboard", "Credits",
                 "wilson_lower_bound", "Today (UTC)", username):
        assert text in report
    process = subprocess.run([sys.executable, "-m", "kairos.report", "--dsn", DSN, "--rule-version", "v1"],
                             capture_output=True, timeout=30,
                             env={**os.environ, "PYTHONIOENCODING": "cp1251"})
    assert process.returncode == 0, process.stderr.decode("utf-8", errors="replace")
    assert username in process.stdout.decode("utf-8")


@pytest.mark.parametrize("option,interval", [("1h", "1 hour"), ("24h", "24 hours"),
                                            ("7d", "7 days")])
def test_cli_horizon_mapping(option, interval):
    assert parse_args(["--horizon", option]).horizon == interval


def test_cli_defaults_and_validation():
    args = parse_args([])
    assert (args.reference, args.horizon, args.min_tokens, args.limit) == (
        "first_mention", "24 hours", 3, 30)
    for argv in (["--horizon", "2h"], ["--min-tokens", "-1"], ["--limit", "-2"]):
        with pytest.raises(SystemExit):
            parse_args(argv)


async def test_pool_and_readonly_connection(db):
    await seed(db)
    async with db.transaction(readonly=True):
        assert len(await kol_leaderboard(db)) == 1
        assert (await overview(db))["kols"]["total"] == 1
        assert await credits(db) == []
    async with asyncpg.create_pool(DSN, min_size=1, max_size=1) as pool:
        assert len(await kol_leaderboard(pool)) == 1
        assert (await overview(pool))["tokens"]["total"] == 7
        assert await credits(pool) == []

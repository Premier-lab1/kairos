"""Разовый ремонт данных (kairos.repair). База kairos_test_spikes (своя, не общая)."""

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from dbutil import reset_schema
from kairos import repair
from kairos.db import migrate
from test_helius import (
    ATA, BUYER, MINT, OTHER_POOL, PUMP_AMM_PROGRAM, USDC, amm_event, make_client, make_tx, pool_balances, rpc_ok,
)

DSN = os.environ.get(
    "KAIROS_TEST_SPIKES_DATABASE_URL",
    os.environ.get("KAIROS_TEST_PG", "postgresql://kairos@localhost:5434") + "/kairos_test_spikes",
)
T = datetime(2026, 10, 18, 16, 0, tzinfo=timezone.utc)


@pytest.fixture
async def pool():
    conn = await asyncpg.connect(DSN)
    try:
        await reset_schema(conn)
        await migrate(conn)
    finally:
        await conn.close()
    p = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
    yield p
    await p.close()


async def add_token(pool, mint, *, created_at=T, sig=None, symbol=None, synced=None, migrated_at=None):
    return await pool.fetchval(
        """INSERT INTO tokens (chain, mint, symbol, name, create_signature, created_at, initial_market_cap_sol,
                               trades_synced_until, migrated_at, metadata, source)
           VALUES ('SOLANA', $1, $2, $2, $3, $4, 28, $5, $6, '{"helius_no_pump_trades": true, "x": 1}', 'pumpportal')
           RETURNING id""", mint, symbol, sig, created_at, synced, migrated_at)


async def add_mention(pool, token_id, ts, tweet):
    kol = await pool.fetchval("INSERT INTO kols (username) VALUES ($1) RETURNING id", f"k{tweet}")
    await pool.execute("""INSERT INTO mentions (kol_id, chain, ca, token_id, tweet_id, tweet_time, source)
                          VALUES ($1, 'SOLANA', 'ca', $2, $3, $4, 'test')""", kol, token_id, tweet, ts)


async def add_create_event(pool, mint, sig, sol_in_pool=False):
    payload = {"mint": mint, "pool": "bonk", "txType": "create", "signature": sig, "solAmount": 0}
    if sol_in_pool:
        payload.update(solAmount=1.5, solInPool=1.5)
    await pool.execute("INSERT INTO raw_events (received_at, source, kind, payload) VALUES ($1, 'pumpportal', 'create', $2::jsonb)",
                       T, json.dumps(payload))


async def test_bogus_creates_reset_only_tokens_with_mentions_and_bad_creation(pool):
    usdc = await add_token(pool, USDC, sig="s1", symbol="POTION", synced=T + timedelta(hours=2))
    for sig in ("s1", "s2", "s3"):  # mint котировки приходит в каждом запуске с этой котировкой
        await add_create_event(pool, USDC, sig)
    await add_mention(pool, usdc, T + timedelta(hours=1), "t-usdc")
    await pool.execute("""INSERT INTO trades (ts, signature, token_id, wallet_address, side, sol_amount, token_amount, source)
                          VALUES ($1, 'x', $2, 'w', 'buy', 1, 1, 'helius')""", T, usdc)
    await pool.execute("INSERT INTO token_snapshots (ts, token_id, market_cap_sol, source) VALUES ($1, $2, 28, 'pumpportal')", T, usdc)
    old = await add_token(pool, "OLDtokenMint1111111111111111111111111111111", sig="s9", symbol="OLD")
    await add_create_event(pool, "OLDtokenMint1111111111111111111111111111111", "s9")
    await add_mention(pool, old, T - timedelta(days=40), "t-old")  # твит за 40 дней до «создания»
    good = await add_token(pool, "GOODtokenMint111111111111111111111111111111", sig="g1", symbol="GOOD")
    await add_create_event(pool, "GOODtokenMint111111111111111111111111111111", "g1", sol_in_pool=True)
    await add_mention(pool, good, T + timedelta(minutes=3), "t-good")
    quiet = await add_token(pool, "QUIETMint111111111111111111111111111111111", sig="q1", symbol="Q")
    for sig in ("q1", "q2"):  # дубль, но упоминаний нет — на исходы не влияет, не трогаем
        await add_create_event(pool, "QUIETMint111111111111111111111111111111111", sig)

    async with pool.acquire() as conn:
        rows = await repair.repair_bogus_creates(conn, apply=False)
        assert sorted(r["id"] for r in rows) == sorted([usdc, old])
        assert await conn.fetchval("SELECT symbol FROM tokens WHERE id = $1", usdc) == "POTION"  # пробный прогон
        await repair.repair_bogus_creates(conn, apply=True)
        r = await conn.fetchrow("SELECT * FROM tokens WHERE id = $1", usdc)
        assert r["symbol"] is r["name"] is r["create_signature"] is r["created_at"] is r["trades_synced_until"] is None
        assert r["initial_market_cap_sol"] is None and json.loads(r["metadata"]) == {"x": 1}
        assert await conn.fetchval("SELECT count(*) FROM trades WHERE token_id = $1", usdc) == 0
        assert await conn.fetchval("SELECT count(*) FROM token_snapshots WHERE token_id = $1", usdc) == 0
        assert await conn.fetchval("SELECT count(*) FROM mentions WHERE token_id = $1", usdc) == 1  # упоминания остаются
        assert await conn.fetchval("SELECT created_at FROM tokens WHERE id = $1", old) is None
        for tid, sym in ((good, "GOOD"), (quiet, "Q")):
            assert await conn.fetchval("SELECT symbol FROM tokens WHERE id = $1", tid) == sym
        assert await repair.find_bogus_creates(conn) == []


async def test_mixed_pool_tokens_selected_by_liquidity_jumps(pool):
    mig = T
    mixed = await add_token(pool, "MIXEDMint11111111111111111111111111111111", synced=T + timedelta(hours=2), migrated_at=mig)
    clean = await add_token(pool, "CLEANMint11111111111111111111111111111111", synced=T + timedelta(hours=2), migrated_at=mig)
    rows = []
    for i in range(40):
        ts = mig + timedelta(minutes=i)
        rows.append((ts, mixed, Decimal(4) if i % 4 else Decimal(21702)))  # чужой пул каждый 4-й снимок
        rows.append((ts, clean, Decimal(80) + i))
    await pool.executemany("INSERT INTO token_snapshots (ts, token_id, liquidity_sol, source) VALUES ($1, $2, $3, 'helius')", rows)
    async with pool.acquire() as conn:
        assert await repair.find_mixed_pool_tokens(conn, 2.5) == [mixed]


class FakeBudget:
    def __init__(self, left):
        self.left = left

    async def remaining(self):
        return Decimal(self.left)


async def test_amm_rebackfill_replaces_post_migration_trades_with_fixed_parse(pool):
    mig = T
    synced = T + timedelta(hours=1)
    token_id = await add_token(pool, MINT, synced=synced, migrated_at=mig)
    await pool.execute("""INSERT INTO trades (ts, signature, token_id, wallet_address, side, sol_amount, token_amount,
                                              market_cap_sol, source)
                          VALUES ($1, 'old-bad', $2, 'w', 'sell', 0.14, 14, 9834854, 'helius'),
                                 ($3, 'pre-mig', $2, 'w', 'buy', 1, 1, 30, 'helius')""",
                       mig + timedelta(minutes=10), token_id, mig - timedelta(minutes=10))
    await pool.execute("INSERT INTO token_snapshots (ts, token_id, market_cap_sol, liquidity_sol, source) VALUES ($1, $2, 9834854, 21702, 'helius')",
                       mig + timedelta(minutes=10), token_id)
    buy = amm_event(True, BUYER, "zGEfU6iUtRoAqZtykHtQqZtUK8PVyzBhTJaipx3zJJS", int(mig.timestamp()) + 600,
                    base=1_000_000, quote=50_000_000, quote_x=50_100_000, pool_base=900_000_000_000, pool_quote=5_000_000_000)
    foreign = amm_event(False, BUYER, OTHER_POOL, int(mig.timestamp()) + 600, base=14_073_838, quote=138_414_624,
                        quote_x=138_068_587, pool_base=2_206_686_531_999, pool_quote=21_702_716_546_638)
    balances = [{"accountIndex": 3, "mint": MINT, "owner": BUYER, "uiTokenAmount": {"amount": "5"}}, *pool_balances(),
                *pool_balances(OTHER_POOL, USDC, 2_206_686_531_999, quote=21_702_716_546_638)]
    tx = make_tx(sig="new-sig", events=[buy, foreign], program=PUMP_AMM_PROGRAM, extra_keys=[ATA], token_balances=balances,
                 block_time=int(mig.timestamp()) + 600)
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return rpc_ok({"data": [tx], "paginationToken": None})

    client, _ = make_client(handler)
    report = await repair.rebackfill_amm(pool, client, [token_id], max_credits=100, budget=FakeBudget(1000), apply=False)
    assert report[0]["estimate_credits"] == 10 and bodies == []  # пробный прогон ничего не качает
    report = await repair.rebackfill_amm(pool, client, [token_id], max_credits=100, budget=FakeBudget(1000), apply=True)
    await client.aclose()
    assert report[0]["stopped"] == "exhausted" and report[0]["credits"] == 10
    cfg = bodies[0]["params"][1]
    assert cfg["filters"]["blockTime"] == {"gte": int((mig - repair.MIGRATION_SLACK).timestamp()), "lte": int(synced.timestamp())}
    rows = await pool.fetch("SELECT signature, market_cap_sol FROM trades WHERE token_id = $1 ORDER BY ts", token_id)
    assert [r["signature"] for r in rows] == ["pre-mig", "new-sig"]  # до миграции не трогали
    assert rows[1]["market_cap_sol"] == Decimal("5.0501") / Decimal("899999") * 10**9  # резервы своего пула
    liq = await pool.fetchval("SELECT max(liquidity_sol) FROM token_snapshots WHERE token_id = $1", token_id)
    assert liq == Decimal("5.0501")
    t = await pool.fetchrow("SELECT trades_synced_until, trades_backfill_truncated FROM tokens WHERE id = $1", token_id)
    assert t["trades_synced_until"] == synced and t["trades_backfill_truncated"] is False


async def test_amm_rebackfill_respects_credit_limit(pool):
    token_id = await add_token(pool, MINT, synced=T + timedelta(hours=1), migrated_at=T)
    await pool.execute("""INSERT INTO trades (ts, signature, token_id, wallet_address, side, sol_amount, token_amount, source)
                          VALUES ($1, 'keep', $2, 'w', 'buy', 1, 1, 'helius')""", T + timedelta(minutes=5), token_id)

    def handler(request):  # pragma: no cover - не должен вызываться
        raise AssertionError("no requests expected")

    client, _ = make_client(handler)
    report = await repair.rebackfill_amm(pool, client, [token_id], max_credits=5, budget=FakeBudget(1000), apply=True)
    await client.aclose()
    assert report[0]["skipped"].startswith("credits left 5")
    assert await pool.fetchval("SELECT count(*) FROM trades WHERE token_id = $1", token_id) == 1


async def test_zero_prices_are_removed_and_other_rows_kept(pool):
    token_id = await add_token(pool, MINT, synced=T + timedelta(hours=1))
    await pool.execute("""INSERT INTO trades (ts, signature, token_id, wallet_address, side, sol_amount, token_amount,
                                              market_cap_sol, source)
                          VALUES ($1, 'zero', $2, 'w', 'buy', 0, 5, 0, 'helius'), ($3, 'real', $2, 'w', 'buy', 1, 5, 30, 'helius')""",
                       T, token_id, T + timedelta(minutes=1))
    await pool.execute("""INSERT INTO token_snapshots (ts, token_id, price_sol, market_cap_sol, liquidity_sol, source)
                          VALUES ($1, $2, 0, 0, 0, 'helius'), ($3, $2, 0.00000003, 30, 5, 'helius'), ($4, $2, NULL, 28, 0, 'pumpportal')""",
                       T, token_id, T + timedelta(minutes=1), T - timedelta(minutes=1))
    async with pool.acquire() as conn:
        assert await repair.repair_zero_prices(conn, apply=False) == [token_id]
        assert await conn.fetchval("SELECT count(*) FROM trades WHERE market_cap_sol = 0") == 1
        await repair.repair_zero_prices(conn, apply=True)
        assert await repair.repair_zero_prices(conn, apply=False) == []
        caps = {r["signature"]: r["market_cap_sol"] for r in await conn.fetch("SELECT signature, market_cap_sol FROM trades")}
        assert caps == {"zero": None, "real": Decimal(30)}
        assert await conn.fetchval("SELECT count(*) FROM token_snapshots") == 2  # снимок pumpportal не трогаем

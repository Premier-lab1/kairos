from datetime import datetime, timedelta, timezone
from decimal import Decimal

import orjson

from kairos.collectors.pumpportal import Migration, NewToken, TokenStore, handle_message, parse_event

T0 = datetime(2026, 10, 18, 13, 0, tzinfo=timezone.utc)

# Реальное событие из потока PumpPortal (18.10.2026)
CREATE = {
    "signature": "3CZuVKeaq1QeA4rwDgQKYbB5xrwpRbuvDT4cfgVBv6N8yGQPMLXxyQCAabC2eyLHz63mkC5jM6fA6E6H5b8Lt1ur",
    "mint": "EWLaWR498Y7F9vBBj4q2QbQgvEHTAdznjj4AGHLupump",
    "traderPublicKey": "EghrVe4pWUd2SL4QwKJR6wV5zBb6wE1b1qUeZ2g5zBLf",
    "txType": "create",
    "initialBuy": 37170506.849894,
    "solAmount": 1.076543208,
    "bondingCurveKey": "4mBezHdtRXUGc47btvsJrUUu7S879oTL3qXGEiW3FHoo",
    "vTokensInBondingCurve": 1035829493.150106,
    "vSolInBondingCurve": 31.076543207999993,
    "marketCapSol": 30.00160104873223,
    "name": "amoeba",
    "symbol": "AMBA",
    "uri": "https://ipfs.io/ipfs/bafkreibf3t3vaz37sa4kctevriwsf3io274tw5mnf7a2fclu7ydpqtub4m",
    "is_mayhem_mode": False,
    "pool": "pump",
}
# Формат реального события миграции (18.10.2026), mint подменён на токен из CREATE
MIGRATE = {
    "signature": "3NJmgp2BeRGZP4noT5fHAxXJFWHb9cv2o5LBaEUiFeLvq1iDbED9z9pPU1PNRjzdVayYin5HhiLnzZrfMR1upFa",
    "mint": CREATE["mint"],
    "txType": "migrate",
    "pool": "pump-amm",
}


def test_parse_create():
    ev = parse_event(CREATE, T0)
    assert isinstance(ev, NewToken)
    assert ev.mint == CREATE["mint"]
    assert ev.creator == CREATE["traderPublicKey"]
    assert ev.launchpad == "pump"
    assert ev.initial_buy_sol == Decimal("1.076543208")
    assert ev.received_at == T0


def test_parse_migration_and_service_messages():
    ev = parse_event(MIGRATE, T0)
    assert isinstance(ev, Migration) and ev.pool == "pump-amm"
    assert parse_event({"message": "Successfully subscribed to token creation events."}, T0) is None
    assert parse_event({"mint": "x", "txType": "buy"}, T0) is None


async def test_new_token_is_stored_with_creator_and_snapshot(db_pool):
    store = TokenStore(db_pool)
    ev = await handle_message(store, orjson.dumps(CREATE), T0)
    assert isinstance(ev, NewToken)
    row = await db_pool.fetchrow(
        """SELECT t.*, w.address AS creator FROM tokens t JOIN wallets w ON w.id = t.creator_wallet_id
           WHERE t.mint = $1""", CREATE["mint"])
    assert row["symbol"] == "AMBA" and row["creator"] == CREATE["traderPublicKey"]
    assert row["created_at"] == T0 and row["source"] == "pumpportal"
    snap = await db_pool.fetchrow("SELECT * FROM token_snapshots WHERE token_id = $1", row["id"])
    assert snap["liquidity_sol"] == Decimal("1.076543207999993")  # 31,08 виртуальных − 30
    assert await db_pool.fetchval("SELECT count(*) FROM raw_events WHERE kind = 'create'") == 1


async def test_duplicate_create_is_idempotent(db_pool):
    store = TokenStore(db_pool)
    await handle_message(store, orjson.dumps(CREATE), T0)
    await handle_message(store, orjson.dumps(CREATE), T0 + timedelta(seconds=5))
    assert await db_pool.fetchval("SELECT count(*) FROM tokens") == 1
    assert await db_pool.fetchval("SELECT count(*) FROM wallets") == 1
    # время создания остаётся самым ранним
    assert await db_pool.fetchval("SELECT created_at FROM tokens") == T0


async def test_migration_updates_known_token(db_pool):
    store = TokenStore(db_pool)
    await handle_message(store, orjson.dumps(CREATE), T0)
    await handle_message(store, orjson.dumps(MIGRATE), T0 + timedelta(minutes=30))
    row = await db_pool.fetchrow("SELECT migrated_at, migration_pool, symbol FROM tokens")
    assert row["migrated_at"] == T0 + timedelta(minutes=30)
    assert row["migration_pool"] == "pump-amm" and row["symbol"] == "AMBA"


async def test_migration_of_unknown_token_creates_minimal_row(db_pool):
    store = TokenStore(db_pool)
    await handle_message(store, orjson.dumps({**MIGRATE, "mint": "UnknownMint111"}), T0)
    row = await db_pool.fetchrow("SELECT * FROM tokens WHERE mint = 'UnknownMint111'")
    assert row["migrated_at"] == T0 and row["created_at"] is None


async def test_create_after_migration_fills_in_details(db_pool):
    store = TokenStore(db_pool)
    await handle_message(store, orjson.dumps(MIGRATE), T0 + timedelta(minutes=30))
    await handle_message(store, orjson.dumps(CREATE), T0)
    row = await db_pool.fetchrow("SELECT * FROM tokens")
    assert row["symbol"] == "AMBA" and row["created_at"] == T0
    assert row["migrated_at"] == T0 + timedelta(minutes=30)


async def test_unknown_and_garbage_messages(db_pool):
    store = TokenStore(db_pool)
    assert await handle_message(store, b"not json", T0) is None
    assert await handle_message(store, orjson.dumps({"something": 1}), T0) is None
    assert await db_pool.fetchval("SELECT count(*) FROM raw_events WHERE kind = 'unknown'") == 1
    assert await db_pool.fetchval("SELECT count(*) FROM tokens") == 0


# Реальные события пула bonk (19.10.2026): с котировкой в SOL (есть solInPool) и с котировкой в USDC — в поле mint
# пришёл mint USDC, а создавался токен A9q1… (транзакция в tests/data/tx_create_bonk_usdc_quote.json).
BONK_SOL = {"mint": "9YDEwuQRkTLVJ2ksrNktTNAyJXswZ6LjCicJSYzBtZCN", "pool": "bonk", "txType": "create",
            "signature": "4aZ42wfFqjkv5fwHsLvZJC7xFwic73EprtcCSMN7N3LDjbSmvMb9n8jwSKKHXgUgBUvybjRLsJcK4QtfJaNehaNm",
            "solAmount": 1.6335, "solInPool": 1.6335, "initialBuy": 55275007.781388044, "marketCapSol": 31.07693964813306,
            "tokensInPool": 944724992.218612, "newTokenBalance": 55275007.781388,
            "traderPublicKey": "8wVRGVkaAs1iYEN2g8UTP5K9esv5qtRdgKR7anxBkvZ3"}
BONK_NON_SOL = {"mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "pool": "bonk", "txType": "create",
                "signature": "5reBwqSXUnLakcVfCqB32Gfh6u6g5gCy7QrpZ8CTdaVq4FiLwHrxwSzmCynbHXRpAq5TqT2QVcU61Det9erv6q6t",
                "solAmount": 0, "initialBuy": 1037924.6531419754, "marketCapSol": 28.01182390779727,
                "tokensInPool": 998962075.346858, "newTokenBalance": 11.216893,
                "traderPublicKey": "4qLkCRV6FzxP1fiKuRvJP4CLocgNTtch1qvEx7qhDsK9"}


async def test_non_sol_quoted_bonk_create_is_not_stored(db_pool):
    store = TokenStore(db_pool)
    assert parse_event(BONK_SOL, T0).sol_quoted and parse_event(CREATE, T0).sol_quoted
    other = dict(BONK_NON_SOL, mint="Xsv9hRk1z5ystj9MhnA7Lq4vjSsLwzL2nxrwmwtD3re")  # не из списка котировок
    assert not parse_event(other, T0).sol_quoted
    await handle_message(store, orjson.dumps(other), T0)
    await handle_message(store, orjson.dumps(BONK_SOL), T0)
    assert [r["mint"] for r in await db_pool.fetch("SELECT mint FROM tokens")] == [BONK_SOL["mint"]]
    assert await db_pool.fetchval("SELECT count(*) FROM raw_events WHERE kind = 'create'") == 2  # сырьё сохраняется

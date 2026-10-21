"""Creation parsing and persistence; all RPC responses are local fixtures."""
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from kairos.collectors.helius_trades import TradeStore, backfill_token
from kairos.collectors.pumpportal import QUOTE_MINTS, TokenStore, handle_message, parse_event
from kairos.pipeline import jobs
from kairos.pipeline.helius_tasks import KIND_CREATION, schedule_creation_checks
from kairos.sources.creation import METADATA_INITIALIZE, METAPLEX, TOKEN_PROGRAMS, find_creation, parse_creation
from kairos.sources.helius import _account_keys, b58encode
from test_helius import make_client, rpc_ok
from test_helius_tasks import add_mention, add_token, make_tasks, settings
from test_pumpportal import CREATE

DATA = Path(__file__).parent / "data"
PUMP = "5QZxnAqqRo6eZdQmGdsHMiHkio1tYxKJ1VS7AcWEnUJa"
BONK = "9YDEwuQRkTLVJ2ksrNktTNAyJXswZ6LjCicJSYzBtZCN"
BASE = "A9q1dasbLFq5JA4juc2sNY7xvQc6dhYeKNLuzWqxh1gt"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
NOW = datetime.now(timezone.utc)


def load(name):
    return json.loads((DATA / (name + ".json")).read_text())


@pytest.mark.parametrize("file,mint,name,symbol,creator,ts,pad", [
    ("tx_create_pump_v2", PUMP, "PILLDOG", "PILLDOG", "2qrdiD2pwGKQCbswJTQZyo5Wur5DtGRAfKsMrzDbNamU", 1792436000, "pump"),
    ("tx_create_bonk", BONK, "Muzfeli", "Muzfeli", "8wVRGVkaAs1iYEN2g8UTP5K9esv5qtRdgKR7anxBkvZ3", 1792435982, "bonk"),
    # The supplied fixture actually encodes Worm/Worm, not Love Potion/POTION.
    ("tx_create_bonk_usdc_quote", BASE, "Worm", "Worm", "4qLkCRV6FzxP1fiKuRvJP4CLocgNTtch1qvEx7qhDsK9", 1792340672, "bonk"),
])
def test_real_creation(file, mint, name, symbol, creator, ts, pad):
    tx = load(file)
    c = parse_creation(tx, mint)
    assert (c.mint, c.name, c.symbol, c.creator, c.ts.timestamp(), c.launchpad) == (mint, name, symbol, creator, ts, pad)
    assert c.uri and c.signature == tx["transaction"]["signatures"][0]
    assert find_creation([{}, tx], mint) == c
    assert parse_creation(tx, USDC) is None


def test_multihop_and_discriminator():
    tx = load("tx_multihop_amm")
    for mint in _account_keys(tx) + [b["mint"] for b in tx["meta"]["postTokenBalances"]]:
        assert parse_creation(tx, mint) is None
    assert hashlib.sha256(b"spl_token_metadata_interface:initialize_account").digest()[:8] == METADATA_INITIALIZE


def synthetic(tag=b"\x21", strings=(b" name \0", b" SYMBOL ", b" uri "), initialized=True):
    data = tag + b"".join(len(s).to_bytes(4, "little") + s for s in strings)
    ins = [{"programId": METAPLEX, "accounts": ["meta", PUMP] if tag != b"\x2a\x00" else ["meta", "authority", PUMP], "data": b58encode(data)}]
    if initialized:
        ins.append({"programId": next(iter(TOKEN_PROGRAMS)), "accounts": [PUMP], "data": b58encode(b"\x14")})
    return {"blockTime": 1792436000, "meta": {"err": None}, "transaction": {"signatures": ["sig"], "message": {"accountKeys": [{"pubkey": "payer"}], "instructions": ins}}}


@pytest.mark.parametrize("tag", [b"\x21", b"\x10", b"\x2a\x00"])
def test_metaplex(tag):
    c = parse_creation(synthetic(tag), PUMP)
    assert (c.name, c.symbol, c.uri, c.creator) == ("name", "SYMBOL", "uri", "payer")
    assert parse_creation(synthetic(tag, initialized=False), PUMP) is None


def test_bad_data_and_limits():
    for value in (None, [], {}, {"meta": []}, {"meta": {"err": "failed"}}):
        assert parse_creation(value, PUMP) is None
    tx = synthetic(strings=(b"x" * 300, b"y" * 70, b"z" * 600))
    c = parse_creation(tx, PUMP)
    assert (len(c.name), len(c.symbol), len(c.uri)) == (200, 50, 500)
    for raw in (b"\x21\xff\xff\xff\xff", b"\x21\x01", b"\x21\x01\0\0\0x"):
        tx["transaction"]["message"]["instructions"][0]["data"] = b58encode(raw)
        c = parse_creation(tx, PUMP)
        assert c and (c.name, c.symbol, c.uri) == (None, None, None)
    tx["meta"]["err"] = {"InstructionError": [0, "bad"]}
    assert parse_creation(tx, PUMP) is None


@pytest.mark.parametrize("same", [True, False])
async def test_save_creation(db_pool, same):
    c = replace(parse_creation(synthetic(), PUMP), mint=USDC, name=None, symbol=None, uri="real-uri")
    token_id = await db_pool.fetchval(
        """INSERT INTO tokens (mint, name, symbol, create_signature, initial_buy_sol, initial_market_cap_sol, bonding_curve, metadata, source)
           VALUES ($1, 'POTION', 'POTION', $2, 1, 2, 'false-curve', '[]', 'test') RETURNING id""", USDC, c.signature if same else "false-sig")
    await db_pool.execute("INSERT INTO token_snapshots (token_id, ts, source) VALUES ($1, $2, 'pumpportal'), ($1, $2 + interval '1 second', 'helius')", token_id, NOW)
    store = TradeStore(db_pool)
    await store.save_creation(token_id, c)
    await store.save_creation(token_id, c)
    row = await db_pool.fetchrow("SELECT * FROM tokens WHERE id = $1", token_id)
    assert row["name"] == ("POTION" if same else None)
    assert row["symbol"] == ("POTION" if same else None)
    assert row["uri"] == "real-uri" and row["created_at"] == c.ts
    assert row["create_signature"] == c.signature
    assert row["initial_buy_sol"] == (1 if same else None)
    assert row["initial_market_cap_sol"] == (2 if same else None)
    assert row["bonding_curve"] == ("false-curve" if same else None)
    assert json.loads(row["metadata"])["helius_creation_checked"]
    assert await db_pool.fetchval("SELECT address FROM wallets WHERE id = $1", row["creator_wallet_id"]) == c.creator
    assert await db_pool.fetchval("SELECT count(*) FROM token_snapshots WHERE token_id = $1", token_id) == (2 if same else 1)


async def test_backfill_creation(db_pool):
    tx = load("tx_create_pump_v2")
    client, _ = make_client(lambda _: rpc_ok({"data": [tx]}))
    async with client:
        result = await backfill_token(db_pool, client, PUMP, NOW + timedelta(days=2))
    c = result.creation
    row = await db_pool.fetchrow("SELECT * FROM tokens WHERE mint = $1", PUMP)
    assert c and row["created_at"] == c.ts and row["name"] == "PILLDOG"
    assert json.loads(row["metadata"])["helius_creation_checked"]


@pytest.mark.parametrize("found,daily", [(True, 100), (False, 100), (True, 0)])
async def test_creation_job(db_pool, make_tasks, found, daily):
    await add_token(db_pool, PUMP, created_at=None)
    bodies = []
    def handler(req):
        bodies.append(json.loads(req.content))
        return rpc_ok({"data": [load("tx_create_pump_v2")] if found else []})
    tasks = make_tasks(handler, daily=daily, now=NOW)
    await jobs.enqueue(db_pool, KIND_CREATION, "creation", {"mint": PUMP})
    job = await jobs.claim(db_pool, [KIND_CREATION])
    await tasks.run_job(job)
    row = await db_pool.fetchrow("SELECT * FROM tokens WHERE mint = $1", PUMP)
    assert await db_pool.fetchval("SELECT count(*) FROM trades") == 0
    if daily == 0:
        assert not bodies
        assert not json.loads(row["metadata"] or "{}").get("helius_creation_checked")
        assert await db_pool.fetchval("SELECT status FROM jobs") == "pending"
    else:
        assert len(bodies) == 1
        cfg = bodies[0]["params"][1]
        assert cfg["limit"] == 20 and cfg["sortOrder"] == "asc"
        assert "blockTime" not in cfg.get("filters", {})
        assert json.loads(row["metadata"])["helius_creation_checked"]
        assert (row["created_at"] is not None) == found
        call = await db_pool.fetchrow("SELECT * FROM api_calls")
        assert call["credits"] == 10 and call["note"] == f"creation mint={PUMP}"
        assert await db_pool.fetchval("SELECT status FROM jobs") == "done"


async def test_schedule(db_pool):
    kol = await db_pool.fetchval("INSERT INTO kols (username) VALUES ('creation') RETURNING id")
    for i in range(9):
        mint = USDC if i == 8 else f"creation-mint-{i}"
        tid = await add_token(db_pool, mint, synced=None if i == 1 else NOW)
        if i != 2:
            await add_mention(db_pool, kol, mint, NOW, token_id=tid)
        if i == 3:
            await db_pool.execute("UPDATE tokens SET name = 'known' WHERE id = $1", tid)
        if i in (4, 5, 6):
            flag = ("helius_creation_checked", "helius_no_pump_trades", "helius_cost_capped")[i - 4]
            await db_pool.execute("UPDATE tokens SET metadata = jsonb_build_object($2::text, true) WHERE id = $1", tid, flag)
        if i == 7:
            await db_pool.execute("UPDATE tokens SET chain = 'EVM' WHERE id = $1", tid)
    assert await schedule_creation_checks(db_pool, NOW) == 1
    assert await schedule_creation_checks(db_pool, NOW) == 0
    row = await db_pool.fetchrow("SELECT * FROM jobs")
    assert row["key"] == "creation-mint-0:creation" and row["priority"] == 0


@pytest.mark.parametrize("mint", sorted(QUOTE_MINTS))
async def test_quote_create(db_pool, mint):
    store = TokenStore(db_pool)
    assert await handle_message(store, json.dumps({**CREATE, "mint": mint}), NOW) is None
    assert await db_pool.fetchval("SELECT count(*) FROM tokens") == 0
    assert await db_pool.fetchval("SELECT count(*) FROM wallets") == 0
    assert await db_pool.fetchval("SELECT count(*) FROM raw_events") == 1


async def test_old_create(db_pool):
    tid = await add_token(db_pool, CREATE["mint"], created_at=None)
    await db_pool.execute("UPDATE tokens SET first_seen_at = $2 WHERE id = $1", tid, NOW - timedelta(minutes=11))
    store = TokenStore(db_pool)
    before = await db_pool.fetchrow("SELECT * FROM tokens WHERE id = $1", tid)
    assert await store.save_new_token(parse_event(CREATE, NOW)) == tid
    assert await db_pool.fetchrow("SELECT * FROM tokens WHERE id = $1", tid) == before
    assert await db_pool.fetchval("SELECT count(*) FROM token_snapshots") == 0


@pytest.mark.parametrize("status,expected", [(401, "pending"), (403, "pending"), (400, "skipped"), (429, "pending"), (500, "pending")])
async def test_creation_job_errors(db_pool, make_tasks, status, expected):
    await add_token(db_pool, PUMP, created_at=None)
    tasks = make_tasks(lambda _: httpx.Response(status, json={"error": "failed"}), now=NOW)
    await jobs.enqueue(db_pool, KIND_CREATION, "error", {"mint": PUMP})
    job = await jobs.claim(db_pool, [KIND_CREATION])
    await tasks.run_job(job)
    row = await db_pool.fetchrow("SELECT * FROM jobs")
    assert row["status"] == expected
    if expected == "pending":
        assert row["run_after"] > job.run_after
    assert await db_pool.fetchval("SELECT metadata->>'helius_creation_checked' FROM tokens") is None
    call = await db_pool.fetchrow("SELECT * FROM api_calls")
    assert call["http_status"] == status and not call["ok"]


async def test_legacy_creation(db_pool):
    from kairos.collectors.helius_trades import BackfillResult, _backfill_legacy
    tx = load("tx_create_bonk")
    def handler(req):
        method = json.loads(req.content)["method"]
        if method == "getSignaturesForAddress":
            return rpc_ok([{"signature": tx["transaction"]["signatures"][0], "blockTime": tx["blockTime"], "err": None}])
        assert method == "getTransaction"
        return rpc_ok(tx)
    store = TradeStore(db_pool)
    tid = await store.ensure_token(BONK)
    client, _ = make_client(handler)
    result = BackfillResult()
    async with client:
        await _backfill_legacy(store, client, tid, BONK, BONK, None, NOW + timedelta(days=2), 2, result)
    assert result.creation and result.creation.name == "Muzfeli"
    assert await db_pool.fetchval("SELECT created_at FROM tokens WHERE id = $1", tid) == result.creation.ts


async def test_initial_job_uses_creation_time(db_pool, make_tasks):
    from test_helius_tasks import run_one
    tx = load("tx_create_pump_v2")
    await add_token(db_pool, PUMP, created_at=None)
    tasks = make_tasks(lambda _: rpc_ok({"data": [tx]}), now=NOW + timedelta(days=2))
    row, _ = await run_one(db_pool, tasks, "initial", {"mint": PUMP, "until": (NOW + timedelta(days=1)).isoformat()})
    assert row["status"] == "done"
    token = await db_pool.fetchrow("SELECT * FROM tokens WHERE mint = $1", PUMP)
    assert token["created_at"].timestamp() == tx["blockTime"]
    assert token["symbol"] == "PILLDOG"
    assert json.loads(token["metadata"])["helius_creation_checked"]


@pytest.mark.parametrize("same", [True, False])
async def test_metadata_replacement_policy(db_pool, same):
    c = parse_creation(load("tx_create_pump_v2"), PUMP)
    tid = await add_token(db_pool, PUMP)
    old_wallet = await db_pool.fetchval("INSERT INTO wallets (chain, address) VALUES ('SOLANA', 'old') RETURNING id")
    await db_pool.execute(
        """UPDATE tokens SET name = 'old', symbol = 'old', uri = 'old', creator_wallet_id = $2,
           launchpad = 'old', create_signature = $3 WHERE id = $1""", tid, old_wallet, c.signature if same else None)
    await TradeStore(db_pool).save_creation(tid, c)
    row = await db_pool.fetchrow("SELECT * FROM tokens WHERE id = $1", tid)
    assert (row["name"], row["symbol"], row["uri"], row["launchpad"]) == (("old",) * 4 if same else (c.name, c.symbol, c.uri, c.launchpad))
    assert (row["creator_wallet_id"] == old_wallet) == same


async def test_missing_metadata_without_previous_signature(db_pool):
    c = replace(parse_creation(synthetic(), PUMP), name=None, symbol=None, uri=None)
    tid = await add_token(db_pool, PUMP)
    await db_pool.execute("UPDATE tokens SET name = 'known', symbol = 'known', uri = 'known' WHERE id = $1", tid)
    await TradeStore(db_pool).save_creation(tid, c)
    row = await db_pool.fetchrow("SELECT name, symbol, uri FROM tokens WHERE id = $1", tid)
    assert tuple(row) == ("known", "known", "known")

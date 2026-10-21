"""Тесты задач Helius: регистрация токенов из истории, планировщик догрузки и воркер.

База kairos_test_helius (отдельная от общей kairos_test); Helius мокается через httpx.MockTransport.
"""

import os

import json
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import pytest

from dbutil import reset_schema
from kairos.config import Settings
from kairos.db import migrate
from kairos.outcomes import RULESETS
from kairos.pipeline import jobs
from kairos.pipeline.budget import CreditBudget, record_call
from kairos.pipeline.helius_tasks import (
    KIND_BACKFILL, PROVIDER, HeliusTasks, register_history_tokens, schedule_backfills,
)
from kairos.sources.helius import b58encode
from test_helius import BUY, MINT, SELL, T0, make_client, make_tx, rpc_ok

TEST_DSN = os.environ.get("KAIROS_TEST_DATABASE_URL", os.environ.get("KAIROS_TEST_PG", "postgresql://kairos@localhost:5434") + "/kairos_test_helius")
H = timedelta(hours=1)
SLACK = timedelta(minutes=5)
CREATED = datetime.fromtimestamp(T0, tz=timezone.utc) - timedelta(minutes=1)
NOW = CREATED + timedelta(days=2)  # 1 ч и 24 ч уже позади, 7 д - нет


def mint_of(i: int) -> str:
    return b58encode(bytes([i + 1]) * 32)


def epoch_min(ts: datetime) -> int:
    return int(ts.timestamp() // 60)


@pytest.fixture
async def db_pool():
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await reset_schema(conn)
        await migrate(conn)
    finally:
        await conn.close()
    pool = await asyncpg.create_pool(TEST_DSN, min_size=1, max_size=4)
    yield pool
    await pool.close()


@pytest.fixture
async def kol(db_pool):
    return await db_pool.fetchval("INSERT INTO kols (username) VALUES ('shiller') RETURNING id")


async def add_token(pool, mint, *, created_at=CREATED, migrated_at=None, synced=None, source="pumpportal"):
    return await pool.fetchval(
        """INSERT INTO tokens (chain, mint, created_at, migrated_at, trades_synced_until, source)
           VALUES ('SOLANA', $1, $2, $3, $4, $5) RETURNING id""", mint, created_at, migrated_at, synced, source)


_tweet_seq = iter(range(10**6))


async def add_mention(pool, kol_id, ca, tweet_time, *, chain="SOLANA", token_id=None):
    await pool.execute(
        """INSERT INTO mentions (kol_id, chain, ca, token_id, tweet_id, tweet_time, source)
           VALUES ($1, $2, $3, $4, $5, $6, 'frontrun_ca_history')""",
        kol_id, chain, ca, token_id, f"tw{next(_tweet_seq)}", tweet_time)


async def pending_jobs(pool):
    rows = await pool.fetch("SELECT key, payload, priority, run_after FROM jobs WHERE kind = $1 ORDER BY key", KIND_BACKFILL)
    return {r["key"]: (json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"], r["priority"], r["run_after"])
            for r in rows}


def settings(**kw):
    return Settings(helius_api_key="test-key-0123456789abcdef", **kw)


# ───────────── register_history_tokens ─────────────


async def test_register_creates_valid_solana_tokens_and_links_mentions(db_pool, kol):
    good, short, bad_char = mint_of(1), "abc123", "0" * 40  # '0' нет в алфавите base58
    for i, ca in enumerate([good, good, short, bad_char]):
        await add_mention(db_pool, kol, ca, NOW - timedelta(hours=i))
    assert await register_history_tokens(db_pool) == 1
    row = await db_pool.fetchrow("SELECT * FROM tokens WHERE mint = $1", good)
    assert row["chain"] == "SOLANA" and row["source"] == "frontrun_ca_history" and row["created_at"] is None
    assert await db_pool.fetchval("SELECT count(*) FROM tokens") == 1
    linked = await db_pool.fetch("SELECT ca, token_id FROM mentions ORDER BY id")
    assert [(r["ca"], r["token_id"]) for r in linked] == [(good, row["id"]), (good, row["id"]), (short, None), (bad_char, None)]


async def test_register_ignores_evm_and_other_chains(db_pool, kol):
    evm = "0x" + "ab" * 20
    await add_mention(db_pool, kol, evm, NOW, chain="EVM")
    await add_mention(db_pool, kol, mint_of(2), NOW, chain="ETH")  # base58, но чужая сеть
    assert await register_history_tokens(db_pool) == 0
    assert await db_pool.fetchval("SELECT count(*) FROM tokens") == 0
    assert await db_pool.fetchval("SELECT count(*) FROM mentions WHERE token_id IS NOT NULL") == 0


async def test_register_links_existing_token_and_is_idempotent(db_pool, kol):
    mint = mint_of(3)
    token_id = await add_token(db_pool, mint)
    await add_mention(db_pool, kol, mint, NOW)
    assert await register_history_tokens(db_pool) == 0  # токен уже есть: новых строк нет, упоминание привязано
    assert await db_pool.fetchval("SELECT token_id FROM mentions") == token_id
    assert await db_pool.fetchval("SELECT source FROM tokens WHERE id = $1", token_id) == "pumpportal"
    assert await register_history_tokens(db_pool) == 0


# ───────────── schedule_backfills ─────────────


async def test_schedule_mentioned_token_with_both_references(db_pool, kol):
    mint = mint_of(1)
    token_id = await add_token(db_pool, mint)
    first_mention = CREATED + timedelta(hours=3)
    await add_mention(db_pool, kol, mint, first_mention + timedelta(hours=1), token_id=token_id)
    await add_mention(db_pool, kol, mint, first_mention, token_id=token_id)
    created = await schedule_backfills(db_pool, settings(), NOW)
    cps = {
        CREATED + H + SLACK: 5, CREATED + 24 * H + SLACK: 3,                 # 7 д после creation - в будущем
        first_mention + H + SLACK: 5, first_mention + 24 * H + SLACK: 3,
    }
    jobs_ = await pending_jobs(db_pool)
    assert created == len(cps) == len(jobs_)
    for cp, priority in cps.items():
        payload, prio, run_after = jobs_[f"{mint}:{epoch_min(cp)}"]
        assert payload == {"mint": mint, "until": cp.isoformat()} and prio == priority and run_after == NOW


async def test_schedule_migrated_token_without_mentions_and_priorities(db_pool):
    mint = mint_of(2)
    created = NOW - timedelta(days=8)
    await add_token(db_pool, mint, created_at=created, migrated_at=created + timedelta(minutes=30))
    assert await schedule_backfills(db_pool, settings(), NOW) == 3
    jobs_ = await pending_jobs(db_pool)
    prio = {key: p for key, (_, p, _) in jobs_.items()}
    assert prio == {f"{mint}:{epoch_min(created + h + SLACK)}": p for h, p in [(H, 5), (24 * H, 3), (7 * 24 * H, 1)]}


async def test_schedule_only_past_checkpoints(db_pool):
    mint = mint_of(3)
    created = NOW - timedelta(minutes=30)
    await add_token(db_pool, mint, created_at=created, migrated_at=created)
    assert await schedule_backfills(db_pool, settings(), NOW) == 0  # 1 ч + 5 мин ещё не наступило
    assert await schedule_backfills(db_pool, settings(), created + H + SLACK) == 1
    assert list(await pending_jobs(db_pool)) == [f"{mint}:{epoch_min(created + H + SLACK)}"]


async def test_schedule_ignores_uninteresting_and_non_solana(db_pool, kol):
    await add_token(db_pool, mint_of(4))  # ни упоминаний, ни миграции
    await db_pool.execute("INSERT INTO tokens (chain, mint, created_at, migrated_at, source) VALUES ('EVM', '0xabc', $1, $1, 'x')",
                          CREATED)
    await add_mention(db_pool, kol, "0x" + "cd" * 20, CREATED, chain="EVM")
    assert await schedule_backfills(db_pool, settings(), NOW) == 0
    assert await db_pool.fetchval("SELECT count(*) FROM jobs") == 0


async def test_schedule_unknown_created_at_gets_initial_job(db_pool, kol):
    mint = mint_of(5)
    await add_mention(db_pool, kol, mint, NOW - timedelta(days=30))  # токен известен только по истории шиллера
    now = NOW.replace(second=37, microsecond=123)
    created = await schedule_backfills(db_pool, settings(), now)
    jobs_ = await pending_jobs(db_pool)
    # first_mention даёт свои контрольные точки (7 д тоже позади), плюс одна задача initial
    assert f"{mint}:initial" in jobs_ and created == len(jobs_) == 4
    payload, prio, _ = jobs_[f"{mint}:initial"]
    # старое упоминание: until не дальше first_mention + 7 д + 5 мин
    assert payload == {"mint": mint, "until": (NOW - timedelta(days=30) + 7 * 24 * H + SLACK).isoformat()} and prio == 4


async def test_initial_until_is_now_for_recent_or_missing_mention(db_pool, kol):
    recent, migrated = mint_of(11), mint_of(12)
    await add_mention(db_pool, kol, recent, NOW - timedelta(days=1))
    await add_token(db_pool, migrated, created_at=None, migrated_at=CREATED)  # упоминаний нет
    now = NOW.replace(second=37, microsecond=123)
    await schedule_backfills(db_pool, settings(), now)
    jobs_ = await pending_jobs(db_pool)
    expect = now.replace(second=0, microsecond=0).isoformat()
    assert jobs_[f"{recent}:initial"][0]["until"] == expect
    assert jobs_[f"{migrated}:initial"][0]["until"] == expect


async def test_schedule_unknown_created_at_with_coverage_has_no_initial(db_pool):
    mint = mint_of(6)
    await add_token(db_pool, mint, created_at=None, migrated_at=CREATED, synced=NOW - H)
    assert await schedule_backfills(db_pool, settings(), NOW) == 0


async def test_schedule_is_idempotent(db_pool, kol):
    mint = mint_of(7)
    token_id = await add_token(db_pool, mint)
    await add_mention(db_pool, kol, mint, CREATED + H, token_id=token_id)
    await add_token(db_pool, mint_of(8), created_at=None, migrated_at=CREATED)
    first = await schedule_backfills(db_pool, settings(), NOW)
    assert first > 0
    assert await schedule_backfills(db_pool, settings(), NOW) == 0
    assert await db_pool.fetchval("SELECT count(*) FROM jobs") == first


async def test_schedule_skips_checkpoints_covered_by_sync(db_pool):
    mint = mint_of(9)
    await add_token(db_pool, mint, migrated_at=CREATED, synced=CREATED + 24 * H + SLACK)
    assert await schedule_backfills(db_pool, settings(), NOW) == 0  # 1 ч и 24 ч покрыты, 7 д впереди
    await add_token(db_pool, mint_of(10), migrated_at=CREATED, synced=CREATED + H + SLACK)  # покрыт только 1 ч
    assert await schedule_backfills(db_pool, settings(), NOW) == 1
    assert list(await pending_jobs(db_pool)) == [f"{mint_of(10)}:{epoch_min(CREATED + 24 * H + SLACK)}"]


async def test_schedule_respects_limit_and_continues(db_pool):
    for i in range(5):
        await add_token(db_pool, mint_of(20 + i), migrated_at=CREATED)  # по 2 задачи (1 ч, 24 ч) на токен
    assert await schedule_backfills(db_pool, settings(), NOW, limit=3) == 3
    assert await schedule_backfills(db_pool, settings(), NOW, limit=3) == 3
    assert await schedule_backfills(db_pool, settings(), NOW, limit=100) == 4
    assert await db_pool.fetchval("SELECT count(*) FROM jobs") == 10


# ───────────── run_job ─────────────


@pytest.fixture
async def make_tasks(db_pool):
    clients = []

    def factory(handler, *, daily=20000, now=NOW, **kw):
        client, _ = make_client(handler, max_retries=kw.pop("max_retries", 0))
        clients.append(client)
        s = settings(helius_daily_credits=daily, **kw)
        budget = CreditBudget(db_pool, PROVIDER, daily, clock=lambda: now)
        return HeliusTasks(db_pool, client, budget, s, clock=lambda: now)

    yield factory
    for c in clients:
        await c.aclose()


async def run_one(pool, tasks, key, payload, priority=5):
    assert await jobs.enqueue(pool, KIND_BACKFILL, key, payload, priority=priority)
    job = await jobs.claim(pool, [KIND_BACKFILL])
    assert job is not None and job.key == key
    await tasks.run_job(job)
    row = await pool.fetchrow("SELECT * FROM jobs WHERE id = $1", job.id)
    result = json.loads(row["result"]) if isinstance(row["result"], str) else row["result"]
    return row, result


def history_handler(pages, bodies=None):
    def handler(request):
        body = json.loads(request.content)
        if bodies is not None:
            bodies.append(body)
        items, nxt = pages[body["params"][1].get("paginationToken")]
        return rpc_ok({"data": items, "paginationToken": nxt})
    return handler


def two_trades():
    return {None: ([make_tx(sig="sig-1", events=[BUY], block_time=T0, idx=1),
                    make_tx(sig="sig-2", events=[SELL], block_time=T0 + 30, idx=2)], None)}


async def test_run_exhausted_sets_coverage_and_computes_outcomes(db_pool, kol, make_tasks):
    token_id = await add_token(db_pool, MINT)
    await add_mention(db_pool, kol, MINT, CREATED + H, token_id=token_id)
    until = CREATED + H + SLACK
    bodies = []
    tasks = make_tasks(history_handler(two_trades(), bodies))
    row, result = await run_one(db_pool, tasks, f"{MINT}:x", {"mint": MINT, "until": until.isoformat()})
    assert row["status"] == "done"
    assert result["trades"] == 2 and result["snapshots"] == 2 and result["credits"] == 10
    assert result["exhausted"] is True and result["truncated"] is False and result["continuation"] is False
    tok = await db_pool.fetchrow("SELECT trades_synced_until, trades_backfill_truncated FROM tokens WHERE id = $1", token_id)
    assert tok["trades_synced_until"] == until and tok["trades_backfill_truncated"] is False
    cfg = bodies[0]["params"][1]
    assert cfg["filters"]["blockTime"]["lte"] == int(until.timestamp())
    assert cfg["filters"]["blockTime"]["gte"] == int(CREATED.timestamp()) - 300  # created_at - 5 мин (по умолчанию backfill_token)
    # исходы посчитаны: creation и first_mention x 3 горизонта
    refs = {r["reference"] for r in await db_pool.fetch("SELECT reference FROM token_outcomes WHERE token_id = $1", token_id)}
    assert refs == {"creation", "first_mention"}
    for rules in RULESETS:
        assert await db_pool.fetchval(
            "SELECT count(*) FROM token_outcomes WHERE token_id = $1 AND rule_version = $2",
            token_id, rules.rule_version,
        ) == 6
    assert result["outcomes"] == 6 * len(RULESETS)


async def test_run_second_job_starts_from_synced_until(db_pool, make_tasks):
    token_id = await add_token(db_pool, MINT)
    first_until, second_until = CREATED + H + SLACK, CREATED + 24 * H + SLACK
    bodies = []
    tasks = make_tasks(history_handler(two_trades(), bodies))
    await run_one(db_pool, tasks, "a", {"mint": MINT, "until": first_until.isoformat()})
    await run_one(db_pool, tasks, "b", {"mint": MINT, "until": second_until.isoformat()})
    assert bodies[1]["params"][1]["filters"]["blockTime"] == {"gte": int(first_until.timestamp()), "lte": int(second_until.timestamp())}
    assert await db_pool.fetchval("SELECT trades_synced_until FROM tokens WHERE id = $1", token_id) == second_until


async def test_run_until_is_capped_by_clock(db_pool, make_tasks):
    token_id = await add_token(db_pool, MINT)
    now = CREATED + H
    tasks = make_tasks(history_handler(two_trades()), now=now)
    await run_one(db_pool, tasks, "a", {"mint": MINT, "until": (now + 5 * H).isoformat()})
    assert await db_pool.fetchval("SELECT trades_synced_until FROM tokens WHERE id = $1", token_id) == now


async def test_run_truncated_sets_flag_and_enqueues_continuation(db_pool, make_tasks):
    token_id = await add_token(db_pool, MINT)
    until = CREATED + H + SLACK
    counter = iter(range(1000))

    def endless(request):
        n = next(counter)
        return rpc_ok({"data": [make_tx(sig=f"e{n}", events=[BUY], block_time=T0 + n)], "paginationToken": f"t{n}"})

    tasks = make_tasks(endless, helius_backfill_max_pages=3)
    row, result = await run_one(db_pool, tasks, f"{MINT}:x", {"mint": MINT, "until": until.isoformat()}, priority=3)
    assert row["status"] == "done" and result["truncated"] is True and result["exhausted"] is False
    assert result["pages"] == 3 and result["trades"] == 3 and result["credits"] == 30
    last = datetime.fromtimestamp(T0 + 2, tz=timezone.utc)
    tok = await db_pool.fetchrow("SELECT trades_synced_until, trades_backfill_truncated FROM tokens WHERE id = $1", token_id)
    assert tok["trades_synced_until"] == last and tok["trades_backfill_truncated"] is True
    cont = await pending_jobs(db_pool)
    key = f"{MINT}:cont:{T0 + 2}"
    assert result["continuation"] is True and key in cont
    assert cont[key][0] == {"mint": MINT, "until": until.isoformat()} and cont[key][1] == 3


async def test_run_continuation_completes_and_clears_flag(db_pool, make_tasks):
    last = datetime.fromtimestamp(T0 + 2, tz=timezone.utc)
    token_id = await add_token(db_pool, MINT, synced=last)
    await db_pool.execute("UPDATE tokens SET trades_backfill_truncated = true WHERE id = $1", token_id)
    until = CREATED + H + SLACK
    bodies = []
    tasks = make_tasks(history_handler(two_trades(), bodies))
    _, result = await run_one(db_pool, tasks, f"{MINT}:cont:{T0 + 2}", {"mint": MINT, "until": until.isoformat()})
    assert result["exhausted"] is True
    assert bodies[0]["params"][1]["filters"]["blockTime"]["gte"] == int(last.timestamp())
    tok = await db_pool.fetchrow("SELECT trades_synced_until, trades_backfill_truncated FROM tokens WHERE id = $1", token_id)
    assert tok["trades_synced_until"] == until and tok["trades_backfill_truncated"] is False


async def test_run_truncated_without_progress_does_not_loop(db_pool, make_tasks):
    token_id = await add_token(db_pool, MINT)
    counter = iter(range(1000))

    def noise_only(request):  # транзакции без событий Pump: сделок нет
        n = next(counter)
        return rpc_ok({"data": [make_tx(sig=f"n{n}", events=[])], "paginationToken": f"t{n}"})

    tasks = make_tasks(noise_only, helius_backfill_max_pages=2)
    row, result = await run_one(db_pool, tasks, "a", {"mint": MINT, "until": (CREATED + H).isoformat()})
    assert row["status"] == "done" and result["truncated"] is True and result["continuation"] is False
    tok = await db_pool.fetchrow("SELECT trades_synced_until, trades_backfill_truncated FROM tokens WHERE id = $1", token_id)
    assert tok["trades_synced_until"] is None and tok["trades_backfill_truncated"] is True
    assert await db_pool.fetchval("SELECT count(*) FROM jobs") == 1


async def test_run_discovers_created_at_from_first_trade(db_pool, make_tasks):
    token_id = await add_token(db_pool, MINT, created_at=None, source="frontrun_ca_history")
    bodies = []
    tasks = make_tasks(history_handler(two_trades(), bodies))
    now_minute = NOW.replace(second=0, microsecond=0)
    _, result = await run_one(db_pool, tasks, f"{MINT}:initial", {"mint": MINT, "until": now_minute.isoformat()})
    assert result["trades"] == 2
    tok = await db_pool.fetchrow("SELECT created_at, trades_synced_until FROM tokens WHERE id = $1", token_id)
    assert tok["created_at"] == datetime.fromtimestamp(T0, tz=timezone.utc) and tok["trades_synced_until"] == now_minute
    assert "gte" not in bodies[0]["params"][1]["filters"]["blockTime"]  # без created_at окно открыто слева
    # creation-исходы появились уже в этом проходе
    for rules in RULESETS:
        assert await db_pool.fetchval(
            "SELECT count(*) FROM token_outcomes WHERE token_id = $1 AND reference = 'creation' AND rule_version = $2",
            token_id, rules.rule_version,
        ) == 3


async def test_run_budget_exhausted_reschedules_to_next_reset(db_pool, make_tasks):
    await add_token(db_pool, MINT)
    await record_call(db_pool, PROVIDER, "getTransactionsForAddress", 19_995, ok=True, ts=NOW)  # остаток 5 < 10
    called = []
    tasks = make_tasks(lambda r: called.append(r) or rpc_ok({"data": [], "paginationToken": None}))
    row, _ = await run_one(db_pool, tasks, "a", {"mint": MINT, "until": (CREATED + H).isoformat()})
    assert row["status"] == "pending" and not called
    assert row["run_after"] == datetime(2025, 10, 21, tzinfo=timezone.utc) and "budget" in row["last_error"]
    assert row["attempts"] == 0  # попытка не засчитана


async def test_run_budget_limits_pages(db_pool, make_tasks):
    await add_token(db_pool, MINT)
    await record_call(db_pool, PROVIDER, "x", 19_970, ok=True, ts=NOW)  # остаток 30 -> максимум 3 страницы
    counter = iter(range(1000))

    def endless(request):
        n = next(counter)
        return rpc_ok({"data": [make_tx(sig=f"e{n}", events=[BUY], block_time=T0 + n)], "paginationToken": f"t{n}"})

    tasks = make_tasks(endless, helius_backfill_max_pages=50)
    _, result = await run_one(db_pool, tasks, "a", {"mint": MINT, "until": (CREATED + H).isoformat()})
    assert result["pages"] == 3 and result["truncated"] is True


async def test_run_noop_when_already_synced(db_pool, make_tasks):
    synced = CREATED + 2 * H
    token_id = await add_token(db_pool, MINT, synced=synced)
    called = []
    tasks = make_tasks(lambda r: called.append(r) or rpc_ok({"data": [], "paginationToken": None}))
    row, result = await run_one(db_pool, tasks, "a", {"mint": MINT, "until": (CREATED + H).isoformat()})
    assert row["status"] == "done" and result == {"noop": True} and not called
    assert await db_pool.fetchval("SELECT trades_synced_until FROM tokens WHERE id = $1", token_id) == synced
    assert await db_pool.fetchval("SELECT count(*) FROM api_calls") == 0
    # until ровно на границе покрытия - тоже noop
    row, result = await run_one(db_pool, tasks, "b", {"mint": MINT, "until": synced.isoformat()})
    assert result == {"noop": True}


async def test_run_server_error_is_retried_in_10_minutes(db_pool, make_tasks):
    token_id = await add_token(db_pool, MINT)
    tasks = make_tasks(lambda r: httpx.Response(500, json={"error": "boom"}), max_retries=0)
    before = datetime.now(timezone.utc)
    row, _ = await run_one(db_pool, tasks, "a", {"mint": MINT, "until": (CREATED + H).isoformat()})
    assert row["status"] == "pending" and row["attempts"] == 1 and "500" in row["last_error"]
    assert timedelta(minutes=9) < row["run_after"] - before < timedelta(minutes=11)
    assert await db_pool.fetchval("SELECT trades_synced_until FROM tokens WHERE id = $1", token_id) is None
    assert await db_pool.fetchval("SELECT count(*) FROM api_calls WHERE ok") == 0


async def test_run_rate_limit_and_transport_errors_are_retried(db_pool, make_tasks):
    await add_token(db_pool, MINT)
    tasks = make_tasks(lambda r: httpx.Response(429, json={"error": "slow down"}))
    row, _ = await run_one(db_pool, tasks, "a", {"mint": MINT, "until": (CREATED + H).isoformat()})
    assert row["status"] == "pending"

    def broken(request):
        raise httpx.ConnectError("no route", request=request)

    tasks = make_tasks(broken)
    row, _ = await run_one(db_pool, tasks, "b", {"mint": MINT, "until": (CREATED + H).isoformat()})
    assert row["status"] == "pending" and "Transport" in row["last_error"]


async def test_run_client_error_is_skipped_and_key_not_leaked(db_pool, make_tasks):
    await add_token(db_pool, MINT)
    key = "test-key-0123456789abcdef"
    tasks = make_tasks(lambda r: httpx.Response(400, json={"jsonrpc": "2.0", "error": {"code": -32602, "message": f"bad {key}"}}))
    row, _ = await run_one(db_pool, tasks, "a", {"mint": MINT, "until": (CREATED + H).isoformat()})
    assert row["status"] == "skipped" and "400" in row["last_error"] and key not in row["last_error"]
    call = await db_pool.fetchrow("SELECT * FROM api_calls")
    assert key not in (call["note"] or "")


async def test_run_error_midway_keeps_partial_progress(db_pool, make_tasks):
    token_id = await add_token(db_pool, MINT)
    until = CREATED + H + SLACK
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return rpc_ok({"data": [make_tx(sig="s1", events=[BUY], block_time=T0)], "paginationToken": "t1"})
        return httpx.Response(500, json={"error": "boom"})

    tasks = make_tasks(handler, max_retries=0)
    row, _ = await run_one(db_pool, tasks, "a", {"mint": MINT, "until": until.isoformat()})
    assert row["status"] == "pending"
    tok = await db_pool.fetchrow("SELECT trades_synced_until, trades_backfill_truncated FROM tokens WHERE id = $1", token_id)
    assert tok["trades_synced_until"] == datetime.fromtimestamp(T0, tz=timezone.utc) and tok["trades_backfill_truncated"] is True


async def test_run_credits_recorded_exactly_once(db_pool, make_tasks):
    await add_token(db_pool, MINT)
    pages = {None: ([make_tx(sig="s1", events=[BUY], block_time=T0)], "t1"),
             "t1": ([make_tx(sig="s2", events=[SELL], block_time=T0 + 30)], None)}
    tasks = make_tasks(history_handler(pages))
    _, result = await run_one(db_pool, tasks, "a", {"mint": MINT, "until": (CREATED + H).isoformat()})
    assert result["credits"] == 20 == tasks.client.credits_spent
    assert await db_pool.fetchval("SELECT count(*) FROM api_calls WHERE provider = 'helius'") == 2
    assert await db_pool.fetchval("SELECT sum(credits) FROM api_calls WHERE provider = 'helius'") == 20
    assert await tasks.budget.spent_today() == 20


async def test_run_unknown_token_and_bad_payload_are_skipped(db_pool, make_tasks):
    tasks = make_tasks(lambda r: rpc_ok({"data": [], "paginationToken": None}))
    row, _ = await run_one(db_pool, tasks, "a", {"mint": MINT, "until": CREATED.isoformat()})
    assert row["status"] == "skipped" and "unknown token" in row["last_error"]
    row, _ = await run_one(db_pool, tasks, "b", {"mint": MINT})
    assert row["status"] == "skipped" and "payload" in row["last_error"]


async def test_scheduler_and_worker_together_cover_token(db_pool, make_tasks):
    """Полный круг: планировщик ставит задачи, воркер их исполняет, повторный планировщик молчит."""
    await add_token(db_pool, MINT, migrated_at=CREATED)
    assert await schedule_backfills(db_pool, settings(), NOW) == 2
    tasks = make_tasks(history_handler(two_trades()))
    while (job := await jobs.claim(db_pool, [KIND_BACKFILL])) is not None:
        await tasks.run_job(job)
    assert await db_pool.fetchval("SELECT count(*) FROM jobs WHERE status = 'done'") == 2
    assert await db_pool.fetchval("SELECT trades_synced_until FROM tokens") == CREATED + 24 * H + SLACK
    assert await schedule_backfills(db_pool, settings(), NOW) == 0


# ───────────── EXCLUDED_MINTS, проба initial, 401/403 ─────────────


async def test_register_excludes_wellknown_non_meme_mints(db_pool, kol):
    wsol = "So11111111111111111111111111111111111111112"
    usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    usdt = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
    for ca in (wsol, usdc, usdt, mint_of(1)):
        await add_mention(db_pool, kol, ca, NOW)
    assert await register_history_tokens(db_pool) == 1
    assert await db_pool.fetchval("SELECT array_agg(mint) FROM tokens") == [mint_of(1)]
    assert await db_pool.fetchval("SELECT count(*) FROM mentions WHERE token_id IS NULL") == 3


def noise_pages():
    counter = iter(range(1000))
    calls = []

    def handler(request):
        n = next(counter)
        calls.append(n)
        return rpc_ok({"data": [make_tx(sig=f"n{n}", events=[])], "paginationToken": f"t{n}"})

    return handler, calls


async def test_probe_without_pump_trades_flags_token(db_pool, kol, make_tasks):
    token_id = await add_token(db_pool, MINT, created_at=None, source="frontrun_ca_history")
    await db_pool.execute("UPDATE tokens SET metadata = '{\"keep\": 1}' WHERE id = $1", token_id)
    await add_mention(db_pool, kol, MINT, NOW - timedelta(days=1), token_id=token_id)
    handler, calls = noise_pages()
    tasks = make_tasks(handler, helius_backfill_max_pages=50)
    row, result = await run_one(db_pool, tasks, f"{MINT}:initial", {"mint": MINT, "until": NOW.isoformat()})
    assert row["status"] == "done" and result["no_pump_trades"] is True and len(calls) == 2 and result["credits"] == 20
    meta = await db_pool.fetchval("SELECT metadata FROM tokens WHERE id = $1", token_id)
    meta = json.loads(meta) if isinstance(meta, str) else meta
    assert meta == {"keep": 1, "helius_no_pump_trades": True, "helius_creation_checked": True}
    assert await db_pool.fetchval("SELECT trades_synced_until FROM tokens WHERE id = $1", token_id) is None
    assert await db_pool.fetchval("SELECT count(*) FROM token_outcomes") == 0
    # планировщик больше не ставит задачи по этому токену, а уже стоящие задачи не тратят кредиты
    await db_pool.execute("DELETE FROM jobs")
    assert await schedule_backfills(db_pool, settings(), NOW) == 0
    row, result = await run_one(db_pool, tasks, f"{MINT}:other", {"mint": MINT, "until": NOW.isoformat()})
    assert row["status"] == "done" and result["no_pump_trades"] is True and len(calls) == 2


async def test_probe_with_trades_continues_normally_with_full_page_limit(db_pool, make_tasks):
    token_id = await add_token(db_pool, MINT, created_at=None, source="frontrun_ca_history")
    counter = iter(range(1000))
    seen = []

    def endless(request):
        n = next(counter)
        seen.append(n)
        return rpc_ok({"data": [make_tx(sig=f"e{n}", events=[BUY], block_time=T0 + n)], "paginationToken": f"t{n}"})

    tasks = make_tasks(endless, helius_backfill_max_pages=5)
    row, result = await run_one(db_pool, tasks, f"{MINT}:initial", {"mint": MINT, "until": NOW.isoformat()})
    assert result["pages"] == 2 and result["truncated"] is True and result["continuation"] is True and "no_pump_trades" not in result
    tok = await db_pool.fetchrow("SELECT created_at, trades_synced_until, metadata FROM tokens WHERE id = $1", token_id)
    assert tok["created_at"] == datetime.fromtimestamp(T0, tz=timezone.utc) and json.loads(tok["metadata"])["helius_creation_checked"] is True
    assert tok["trades_synced_until"] == datetime.fromtimestamp(T0 + 1, tz=timezone.utc)
    assert f"{MINT}:cont:{T0 + 1}" in await pending_jobs(db_pool)
    cont_job = await jobs.claim(db_pool, [KIND_BACKFILL])
    assert cont_job.key == f"{MINT}:cont:{T0 + 1}"
    await tasks.run_job(cont_job)
    result2 = await db_pool.fetchval("SELECT result FROM jobs WHERE id = $1", cont_job.id)
    result2 = json.loads(result2) if isinstance(result2, str) else result2
    assert result2["pages"] == 5  # продолжение без пробного лимита


async def test_known_created_at_is_not_probed(db_pool, make_tasks):
    await add_token(db_pool, MINT)  # created_at известен, сделок Pump нет - флаг не ставим
    handler, calls = noise_pages()
    tasks = make_tasks(handler, helius_backfill_max_pages=4)
    _, result = await run_one(db_pool, tasks, "a", {"mint": MINT, "until": (CREATED + H).isoformat()})
    assert len(calls) == 4 and "no_pump_trades" not in result
    assert await db_pool.fetchval("SELECT metadata FROM tokens") is None


@pytest.mark.parametrize("status", [401, 403])
async def test_run_auth_error_retries_in_30_minutes_and_logs_error(db_pool, make_tasks, caplog, status):
    await add_token(db_pool, MINT)
    tasks = make_tasks(lambda r: httpx.Response(status, json={"error": "denied"}))
    before = datetime.now(timezone.utc)
    with caplog.at_level("ERROR", logger="kairos.pipeline.helius_tasks"):
        row, _ = await run_one(db_pool, tasks, "a", {"mint": MINT, "until": (CREATED + H).isoformat()})
    assert row["status"] == "pending" and str(status) in row["last_error"]
    assert timedelta(minutes=29) < row["run_after"] - before < timedelta(minutes=31)
    assert any("check HELIUS_API_KEY" in r.getMessage() and r.levelname == "ERROR" for r in caplog.records)
    assert "test-key-0123456789abcdef" not in caplog.text


async def test_run_other_4xx_still_skipped(db_pool, make_tasks):
    await add_token(db_pool, MINT)
    tasks = make_tasks(lambda r: httpx.Response(404, json={"error": "nope"}))
    row, _ = await run_one(db_pool, tasks, "a", {"mint": MINT, "until": (CREATED + H).isoformat()})
    assert row["status"] == "skipped"

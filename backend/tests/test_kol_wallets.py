import json
from datetime import datetime, timedelta, timezone

import pytest

from kairos.config import Settings
from kairos.pipeline import jobs
from kairos.pipeline.budget import CreditBudget, record_call
from kairos.pipeline.kol_wallets import (
    KIND_POLL, KIND_WALLETS, KolWalletTasks, associated_wallets, changed_mints,
    schedule_kol_wallet_polls, schedule_kol_wallets,
)
from test_helius import BUYER, MINT, OTHER_MINT, T0, make_client, make_tx, pool_balances, rpc_ok, trade_event

NOW = datetime.fromtimestamp(T0 + 3600, timezone.utc)


def settings(**kwargs):
    return Settings(_env_file=None, **kwargs)


@pytest.mark.parametrize("data,expected,skipped", [
    ([{"chain": "sol", "address": BUYER}], [("SOLANA", BUYER)], 0),
    ({"data": {"network": "solana", "wallets": [{"wallet": BUYER}, {"address": BUYER}]}}, [("SOLANA", BUYER)], 0),
    ({"data": [{"network": "base", "wallet": "0x" + "AB" * 20}, {"address": BUYER}]},
     [("EVM", "0x" + "ab" * 20), ("SOLANA", BUYER)], 0),
    ([None, "junk", {"chain": "ETH", "address": BUYER}, {"wallet": "bad"}, {"metadata": 1}], [], 5),
    ({"data": {"wallet": {"network": "SOLANA", "address": BUYER}}}, [("SOLANA", BUYER)], 0),
])
def test_associated_wallet_shapes(data, expected, skipped):
    assert associated_wallets(data) == (expected, skipped)


async def seed(pool, username="alice", cas=1, smarts=5):
    kol = await pool.fetchval("INSERT INTO kols(username,smart_followers_count) VALUES ($1,$2) RETURNING id", username, smarts)
    for i in range(cas):
        await pool.execute("""INSERT INTO mentions(kol_id,chain,ca,tweet_id,tweet_time,source)
            VALUES ($1,'SOLANA',$2,$3,$4,'test')""", kol, f"mint{i}", f"{username}{i}", NOW)
    return kol


async def linked(pool, chain="SOLANA"):
    kol = await seed(pool)
    wid = await pool.fetchval("INSERT INTO wallets(chain,address) VALUES ($1,$2) RETURNING id", chain, BUYER)
    await pool.execute("INSERT INTO kol_wallets VALUES ($1,$2,'associated','test',now())", kol, wid)
    return kol, wid


async def test_schedule_top_refresh_and_duplicates(db_pool):
    a = await seed(db_pool, "a", cas=2)
    await seed(db_pool, "b", cas=1)
    await seed(db_pool, "unknown_smarts", cas=9, smarts=None)
    cfg = settings(kol_wallets_top_n=1)
    assert await schedule_kol_wallets(db_pool, NOW, cfg) == 1
    job = await jobs.claim(db_pool, [KIND_WALLETS], NOW)
    assert job.payload["kol_id"] == a and job.priority == 2
    assert await schedule_kol_wallets(db_pool, NOW + timedelta(days=1), cfg) == 0
    await jobs.complete(db_pool, job.id)
    await db_pool.execute("UPDATE kols SET wallets_fetched_at=$2 WHERE id=$1", a, NOW)
    assert await schedule_kol_wallets(db_pool, NOW + timedelta(days=29), cfg) == 0
    assert await schedule_kol_wallets(db_pool, NOW + timedelta(days=30), cfg) == 1


async def test_poll_scheduler_intervals_and_solana_only(db_pool):
    _, wid = await linked(db_pool)
    evm = await db_pool.fetchval("INSERT INTO wallets(chain,address) VALUES ('EVM','0x123') RETURNING id")
    await db_pool.execute("INSERT INTO kol_wallets(kol_id,wallet_id,relation,source) SELECT id,$1,'manual','test' FROM kols", evm)
    cfg = settings()
    assert await schedule_kol_wallet_polls(db_pool, NOW, cfg) == 1
    assert await schedule_kol_wallet_polls(db_pool, NOW, cfg) == 0
    job = await jobs.claim(db_pool, [KIND_POLL], NOW)
    assert job.key == f"{wid}:{int(NOW.timestamp() // 3600)}" and job.priority == 4
    await jobs.complete(db_pool, job.id)
    assert await schedule_kol_wallet_polls(db_pool, NOW, cfg) == 0
    assert await schedule_kol_wallet_polls(db_pool, NOW + timedelta(hours=1), cfg) == 1


async def test_discovery_upserts_and_records_skipped(db_pool):
    kol = await seed(db_pool)
    class Client:
        credits_spent = 0
        async def associated_wallets(self, username):
            self.credits_spent += 400
            return {"data": [{"address": BUYER, "network": "solana"}, {"wallet": "bad"}]}
    budget = CreditBudget(db_pool, "frontrun", 5000, clock=lambda: NOW)
    handler = KolWalletTasks(db_pool, Client(), budget, settings(), clock=lambda: NOW)
    for n in range(2):
        await jobs.enqueue(db_pool, KIND_WALLETS, str(n), {"kol_id": kol, "username": "alice"}, run_after=NOW)
        job = await jobs.claim(db_pool, [KIND_WALLETS], NOW)
        await handler.run_job(job)
        result = json.loads(await db_pool.fetchval("SELECT result FROM jobs WHERE id=$1", job.id))
        assert result == {"wallets": 1, "skipped": 1}
    assert await db_pool.fetchval("SELECT count(*) FROM wallets") == 1
    assert await db_pool.fetchval("SELECT count(*) FROM kol_wallets") == 1
    assert await db_pool.fetchval("SELECT wallets_fetched_at FROM kols WHERE id=$1", kol) == NOW
    assert await budget.spent_today() == 800


def tx(sig="sig", mint=MINT, buy=True, ts=T0):
    event = trade_event(mint, 2_000_000_000, 10_000_000, buy, BUYER, ts,
                        40_000_000_000, 800_000_000_000_000, 10_000_000_000, 600_000_000_000_000)
    return make_tx(sig=sig, events=[event], block_time=ts, token_balances=[
        {"mint": mint, "owner": BUYER, "uiTokenAmount": {"amount": "0" if not buy else "10"}}])


async def poll_job(pool, wid):
    await jobs.enqueue(pool, KIND_POLL, "poll", {"wallet_id": wid, "address": BUYER}, run_after=NOW)
    return await jobs.claim(pool, [KIND_POLL], NOW)


async def test_poll_records_trades_pagination_and_coverage(db_pool):
    kol, wid = await linked(db_pool)
    token = await db_pool.fetchval("INSERT INTO tokens(mint,source) VALUES ($1,'test') RETURNING id", MINT)
    requests = []
    def transport(request):
        config = json.loads(request.content)["params"][1]
        requests.append(config)
        if len(requests) == 1:
            return rpc_ok({"data": [tx(), tx("other", OTHER_MINT)], "paginationToken": "next"})
        sale = tx("sale", buy=False, ts=T0+10)
        sale["meta"]["preTokenBalances"] = [{"mint": MINT, "owner": BUYER, "uiTokenAmount": {"amount": "10"}}]
        return rpc_ok({"data": [sale, tx()], "paginationToken": None})
    client, _ = make_client(transport)
    async with client:
        budget = CreditBudget(db_pool, "helius", 10000, clock=lambda: NOW)
        await KolWalletTasks(db_pool, client, budget, settings(), clock=lambda: NOW).run_job(await poll_job(db_pool, wid))
    assert requests[0]["sortOrder"] == "asc"
    assert requests[0]["filters"]["blockTime"]["gte"] == int((NOW-timedelta(hours=24)).timestamp())
    assert requests[1]["paginationToken"] == "next"
    rows = await db_pool.fetch("SELECT * FROM kol_trades ORDER BY signature")
    assert len(rows) == 3 and {r["side"] for r in rows} == {"buy", "sell"}
    assert all(r["kol_id"] == kol and r["sol_amount"] == 2 and r["market_cap_sol"] > 0 for r in rows)
    assert next(r for r in rows if r["mint"] == MINT)["token_id"] == token
    assert next(r for r in rows if r["mint"] == OTHER_MINT)["token_id"] is None
    assert await db_pool.fetchval("SELECT kol_trades_synced_until FROM wallets WHERE id=$1", wid) == NOW
    assert await budget.spent_today() == 20
    assert await db_pool.fetchval("SELECT count(*) FROM api_calls WHERE note LIKE 'kol_wallet%' AND ok") == 2


@pytest.mark.parametrize("own_cap,shared_cap", [(10, 1000), (1000, 10)])
async def test_daily_and_shared_ceilings(db_pool, own_cap, shared_cap):
    _, wid = await linked(db_pool)
    await record_call(db_pool, "helius", "test", 10, ok=True, note="kol_wallet previous", ts=NOW)
    client, _ = make_client(lambda r: pytest.fail("must not call Helius"))
    job = await poll_job(db_pool, wid)
    async with client:
        await KolWalletTasks(db_pool, client, CreditBudget(db_pool, "helius", shared_cap, clock=lambda: NOW),
                             settings(kol_wallet_daily_credits=own_cap), clock=lambda: NOW).run_job(job)
    row = await db_pool.fetchrow("SELECT status,run_after FROM jobs WHERE id=$1", job.id)
    assert row["status"] == "pending"
    assert row["run_after"] == NOW.replace(hour=0, minute=0, second=0) + timedelta(days=1)
    assert await db_pool.fetchval("SELECT kol_trades_synced_until FROM wallets WHERE id=$1", wid) is None


async def test_page_cap_resumes_cursor_without_skipping_same_second(db_pool):
    _, wid = await linked(db_pool)
    calls = []
    def transport(request):
        calls.append(json.loads(request.content)["params"][1])
        return rpc_ok({"data": [tx("first" if len(calls) == 1 else "second")],
                       "paginationToken": "cursor" if len(calls) == 1 else None})
    client, _ = make_client(transport)
    async with client:
        handler = KolWalletTasks(db_pool, client, CreditBudget(db_pool, "helius", 1000, clock=lambda: NOW),
                                 settings(kol_wallet_max_pages=1), clock=lambda: NOW)
        job = await poll_job(db_pool, wid)
        await handler.run_job(job)
        assert await db_pool.fetchval("SELECT kol_trades_synced_until FROM wallets WHERE id=$1", wid) == datetime.fromtimestamp(T0, timezone.utc)
        job = await jobs.claim(db_pool, [KIND_POLL], NOW+timedelta(hours=1))
        await handler.run_job(job)
    assert calls[1]["paginationToken"] == "cursor"
    assert calls[0]["filters"]["blockTime"]["gte"] == calls[1]["filters"]["blockTime"]["gte"]
    assert await db_pool.fetchval("SELECT count(*) FROM kol_trades") == 2


def test_changed_balances_ignore_stables_other_owners_and_unchanged():
    balances = pool_balances()
    balances += [{"owner": BUYER, "mint": MINT, "uiTokenAmount": {"amount": "3"}}]
    balances += [{"owner": BUYER, "mint": "So11111111111111111111111111111111111111112",
                  "uiTokenAmount": {"amount": "1234"}}]
    transaction = make_tx(token_balances=balances)
    assert changed_mints(transaction, BUYER) == {MINT}
    transaction["meta"]["preTokenBalances"] = transaction["meta"]["postTokenBalances"]
    assert changed_mints(transaction, BUYER) == set()


async def test_frontrun_budget_defers_without_request(db_pool):
    kol = await seed(db_pool)
    class Client:
        credits_spent = 0
        async def associated_wallets(self, username):
            pytest.fail("insufficient budget must not call Frontrun")
    await jobs.enqueue(db_pool, KIND_WALLETS, "budget", {"kol_id": kol, "username": "alice"}, run_after=NOW)
    job = await jobs.claim(db_pool, [KIND_WALLETS], NOW)
    await KolWalletTasks(db_pool, Client(), CreditBudget(db_pool, "frontrun", 399, clock=lambda: NOW),
                         settings(), clock=lambda: NOW).run_job(job)
    assert await db_pool.fetchval("SELECT status FROM jobs WHERE id=$1", job.id) == "pending"
    assert await db_pool.fetchval("SELECT count(*) FROM api_calls") == 0


async def test_frontrun_wallets_use_their_guaranteed_share(db_pool):
    """Поиск CA съел всё, кроме доли кошельков: кошельки всё равно ищутся; без доли — ждали бы."""
    kol = await seed(db_pool)
    class Client:
        credits_spent = 0
        async def associated_wallets(self, username):
            self.credits_spent += 400
            return [{"chain": "sol", "address": BUYER}]
    await jobs.enqueue(db_pool, "frontrun_search_ca", "hog", {"mint": "m"}, priority=10, run_after=NOW)
    await record_call(db_pool, "frontrun", "frontrun_search_ca", 2550, ok=True, ts=NOW)
    await jobs.enqueue(db_pool, KIND_WALLETS, "w", {"kol_id": kol, "username": "alice"}, run_after=NOW)
    job = await jobs.claim(db_pool, [KIND_WALLETS], NOW)
    shares = {"frontrun_search_ca": .85, KIND_WALLETS: .15}
    handler = KolWalletTasks(db_pool, Client(), CreditBudget(db_pool, "frontrun", 3000, clock=lambda: NOW, shares=shares),
                             settings(), clock=lambda: NOW)
    await handler.run_job(job)
    assert await db_pool.fetchval("SELECT status FROM jobs WHERE id=$1", job.id) == "done"
    assert await db_pool.fetchval("SELECT count(*) FROM kol_wallets") == 1
    # Своя доля потрачена, остаток принадлежит поиску — следующий поиск кошельков ждёт.
    await jobs.enqueue(db_pool, KIND_WALLETS, "w2", {"kol_id": kol, "username": "alice"}, run_after=NOW)
    job = await jobs.claim(db_pool, [KIND_WALLETS], NOW)
    await handler.run_job(job)
    row = await db_pool.fetchrow("SELECT status, last_error FROM jobs WHERE id=$1", job.id)
    assert tuple(row) == ("pending", "daily credit budget exhausted")


async def test_failed_helius_call_is_logged(db_pool):
    import httpx
    _, wid = await linked(db_pool)
    client, _ = make_client(lambda request: httpx.Response(401, json={"error": {"code": -1, "message": "unauthorized"}}))
    job = await poll_job(db_pool, wid)
    async with client:
        await KolWalletTasks(db_pool, client, CreditBudget(db_pool, "helius", 1000, clock=lambda: NOW),
                             settings(), clock=lambda: NOW).run_job(job)
    row = await db_pool.fetchrow("SELECT * FROM api_calls")
    assert not row["ok"] and row["http_status"] == 401 and row["note"].startswith("kol_wallet")
    assert await db_pool.fetchval("SELECT status FROM jobs WHERE id=$1", job.id) == "pending"
    assert await db_pool.fetchval("SELECT kol_trades_synced_until FROM wallets WHERE id=$1", wid) is None

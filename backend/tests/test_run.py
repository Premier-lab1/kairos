import asyncio

import pytest

from kairos.config import Settings
from kairos.pipeline import jobs
from kairos.pipeline.frontrun_tasks import KIND_HISTORY, KIND_SEARCH, KIND_SMARTS
from kairos.pipeline.kol_wallets import KIND_WALLETS
from kairos.pipeline.run import frontrun_shares, worker_loop


def test_frontrun_shares_from_settings():
    """Доли по умолчанию: смарты 10 %, кошельки 15 %, история 20 %, поиску — остаток 55 %."""
    assert frontrun_shares(Settings(_env_file=None)) == {KIND_SMARTS: .10, KIND_WALLETS: .15, KIND_HISTORY: .20, KIND_SEARCH: .55}
    custom = frontrun_shares(Settings(_env_file=None, frontrun_share_smart_followers_pct=0,
                                      frontrun_share_kol_wallets_pct=50, frontrun_share_ca_history_pct=50))
    assert custom == {KIND_SMARTS: 0, KIND_WALLETS: .5, KIND_HISTORY: .5, KIND_SEARCH: 0}
    with pytest.raises(ValueError):
        frontrun_shares(Settings(_env_file=None, frontrun_share_ca_history_pct=80))
    with pytest.raises(ValueError):
        frontrun_shares(Settings(_env_file=None, frontrun_share_kol_wallets_pct=-1))


class FakeTasks:
    def __init__(self, pool, stop, crash_on=None):
        self.pool, self.stop, self.crash_on, self.seen = pool, stop, crash_on, []

    async def run_job(self, job):
        self.seen.append(job.key)
        if job.key == self.crash_on:
            raise RuntimeError("boom")
        await jobs.complete(self.pool, job.id, {"ok": True})
        if len(self.seen) >= 2:
            self.stop.set()


async def test_worker_processes_only_its_kinds_and_survives_crash(db_pool):
    await jobs.enqueue(db_pool, "a", "1", priority=2)
    await jobs.enqueue(db_pool, "a", "2", priority=1)
    await jobs.enqueue(db_pool, "b", "other")
    stop = asyncio.Event()
    tasks = FakeTasks(db_pool, stop, crash_on="1")
    await asyncio.wait_for(worker_loop(db_pool, tasks, ["a"], stop), timeout=10)
    assert tasks.seen == ["1", "2"]
    rows = {r["key"]: (r["status"], r["last_error"]) for r in await db_pool.fetch("SELECT key, status, last_error FROM jobs")}
    assert rows["1"][0] == "pending" and "boom" in rows["1"][1]     # упавшая задача уходит на повтор
    assert rows["2"][0] == "done"
    assert rows["other"][0] == "pending"                             # чужой вид не тронут


async def test_signals_loop_scores_fresh_token(db_pool):
    from datetime import datetime, timezone

    from kairos.config import Settings
    from kairos.pipeline.run import signals_loop

    now = datetime.now(timezone.utc)
    token = await db_pool.fetchval("INSERT INTO tokens (mint, source, created_at) VALUES ('m1', 'test', $1) RETURNING id", now)
    kol = await db_pool.fetchval("INSERT INTO kols (username) VALUES ('alpha') RETURNING id")
    await db_pool.execute("""INSERT INTO mentions (kol_id, chain, ca, token_id, tweet_id, tweet_time, source)
                             VALUES ($1, 'SOLANA', 'm1', $2, 't1', $3, 'test')""", kol, token, now)
    stop = asyncio.Event()
    asyncio.get_running_loop().call_later(1.0, stop.set)
    settings = Settings(_env_file=None, signal_interval_s=0.2)
    await asyncio.wait_for(signals_loop(db_pool, settings, stop), timeout=10)
    row = await db_pool.fetchrow("SELECT score, confidence, n_shillers FROM signals WHERE token_id = $1", token)
    assert row is not None and row["n_shillers"] == 1 and row["confidence"] == "low"

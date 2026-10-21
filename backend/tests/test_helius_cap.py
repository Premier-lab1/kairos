"""Потолок кредитов Helius на один токен (helius_max_credits_per_token)."""

import json
from datetime import datetime, timedelta, timezone

from kairos.collectors.helius_trades import BackfillResult
from kairos.config import Settings
from kairos.pipeline import helius_tasks, jobs
from kairos.pipeline.budget import CreditBudget
from kairos.pipeline.helius_tasks import KIND_BACKFILL, HeliusTasks, schedule_backfills

NOW = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)
MINT = "CapMint1111111111111111111111111111111pump"


def settings(cap=100):
    return Settings(helius_api_key="k", helius_max_credits_per_token=cap, helius_backfill_max_pages=50)


async def setup_token(pool):
    token_id = await pool.fetchval(
        "INSERT INTO tokens (mint, created_at, migrated_at, source) VALUES ($1, $2, $2, 'test') RETURNING id",
        MINT, NOW - timedelta(hours=3))
    return token_id


async def add_done_job(pool, key, credits):
    await pool.execute(
        "INSERT INTO jobs (kind, key, status, result) VALUES ($1, $2, 'done', $3::jsonb)",
        KIND_BACKFILL, key, json.dumps({"credits": credits}))


async def claim_and_run(pool, tasks):
    job = await jobs.claim(pool, [KIND_BACKFILL], now=NOW)
    await tasks.run_job(job)
    return job


async def test_cap_reached_marks_token_and_skips_download(db_pool, monkeypatch):
    await setup_token(db_pool)
    await add_done_job(db_pool, f"{MINT}:1", 60)
    await add_done_job(db_pool, f"{MINT}:2", 50)
    calls = []

    async def fake_backfill(*a, **kw):
        calls.append(kw)

    monkeypatch.setattr(helius_tasks, "backfill_token", fake_backfill)
    await jobs.enqueue(db_pool, KIND_BACKFILL, f"{MINT}:3", {"mint": MINT, "until": NOW.isoformat()}, run_after=NOW)
    tasks = HeliusTasks(db_pool, client=None, budget=CreditBudget(db_pool, "helius", 10_000, clock=lambda: NOW),
                        settings=settings(cap=100), clock=lambda: NOW)
    job = await claim_and_run(db_pool, tasks)
    assert calls == []
    row = await db_pool.fetchrow("SELECT status, result FROM jobs WHERE id = $1", job.id)
    assert row["status"] == "done" and json.loads(row["result"])["cost_capped"] is True
    meta = await db_pool.fetchval("SELECT metadata FROM tokens WHERE mint = $1", MINT)
    assert json.loads(meta)["helius_cost_capped"] is True
    # планировщик больше не ставит задачи этому токену
    assert await schedule_backfills(db_pool, settings(), NOW + timedelta(days=8)) == 0


async def test_pages_limited_by_remaining_token_allowance(db_pool, monkeypatch):
    await setup_token(db_pool)
    await add_done_job(db_pool, f"{MINT}:1", 70)
    seen = {}

    async def fake_backfill(pool, client, mint, until, *, since=None, max_pages=None, result=None, **kw):
        seen["max_pages"] = max_pages
        return result or BackfillResult()

    monkeypatch.setattr(helius_tasks, "backfill_token", fake_backfill)
    await jobs.enqueue(db_pool, KIND_BACKFILL, f"{MINT}:2", {"mint": MINT, "until": NOW.isoformat()}, run_after=NOW)
    tasks = HeliusTasks(db_pool, client=None, budget=CreditBudget(db_pool, "helius", 10_000, clock=lambda: NOW),
                        settings=settings(cap=100), clock=lambda: NOW)
    await claim_and_run(db_pool, tasks)
    assert seen["max_pages"] == 3   # осталось 30 кредитов = 3 страницы по 10

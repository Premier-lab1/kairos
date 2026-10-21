from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kairos.pipeline.budget import CreditBudget, record_call, spent_since
from kairos.pipeline.jobs import claim, complete, enqueue


NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
MIDNIGHT = NOW.replace(hour=0)


async def test_record_call_and_spent_since(db_pool):
    async with db_pool.acquire() as conn:
        await record_call(conn, 'api', '/lookup', Decimal('1.25'), ok=True,
                          http_status=200, note='success', ts=NOW)
        assert await spent_since(conn, 'api', NOW) == Decimal('1.25')
    await record_call(db_pool, 'api', '/lookup', 0.1, ok=False, http_status=429, ts=NOW)
    await record_call(db_pool, 'api', '/lookup', 2, ok=True, ts=NOW - timedelta(seconds=1))
    spent = await spent_since(db_pool, 'api', NOW)
    assert isinstance(spent, Decimal)
    assert spent == Decimal('1.35')
    assert await spent_since(db_pool, 'api', NOW + timedelta(seconds=1)) == Decimal(0)
    row = await db_pool.fetchrow("SELECT * FROM api_calls WHERE note = 'success'")
    assert row['endpoint'] == '/lookup'
    assert row['http_status'] == 200
    assert row['ok'] is True
    assert row['ts'] == NOW


async def test_record_call_default_timestamp(db_pool):
    before = await db_pool.fetchval('SELECT now()')
    await record_call(db_pool, 'api', '/lookup', 1, ok=True)
    after = await db_pool.fetchval('SELECT now()')
    row = await db_pool.fetchrow('SELECT * FROM api_calls')
    assert before <= row['ts'] <= after
    assert row['http_status'] is None
    assert row['note'] is None


async def test_providers_are_separate(db_pool):
    await record_call(db_pool, 'first', '/lookup', 3, ok=True, ts=NOW)
    await record_call(db_pool, 'second', '/lookup', 7, ok=False, ts=NOW)
    assert await spent_since(db_pool, 'first', MIDNIGHT) == Decimal(3)
    assert await spent_since(db_pool, 'second', MIDNIGHT) == Decimal(7)
    assert await spent_since(db_pool, 'missing', MIDNIGHT) == Decimal(0)


async def test_spent_today_ignores_yesterday(db_pool):
    await record_call(db_pool, 'api', '/', 90, ok=True, ts=MIDNIGHT - timedelta(microseconds=1))
    await record_call(db_pool, 'api', '/', 3, ok=True, ts=MIDNIGHT)
    await record_call(db_pool, 'api', '/', 2, ok=False, ts=NOW)
    budget = CreditBudget(db_pool, 'api', 10, clock=lambda: NOW)
    assert await budget.spent_today() == Decimal(5)
    assert await budget.remaining() == Decimal(5)


async def test_remaining_never_negative(db_pool):
    budget = CreditBudget(db_pool, 'api', 10, clock=lambda: NOW)
    assert await budget.remaining() == Decimal(10)
    await record_call(db_pool, 'api', '/', 12, ok=True, ts=NOW)
    remaining = await budget.remaining()
    assert isinstance(remaining, Decimal)
    assert remaining == Decimal(0)
    assert not await budget.can_spend(1)


async def test_can_spend_boundary(db_pool):
    await record_call(db_pool, 'api', '/', 4, ok=True, ts=NOW)
    budget = CreditBudget(db_pool, 'api', 10, clock=lambda: NOW)
    assert await budget.can_spend(6)
    assert not await budget.can_spend(7)
    assert await budget.can_spend(0)


async def test_next_reset_at_midnight_and_during_day(db_pool):
    for timestamp in (MIDNIGHT, NOW, MIDNIGHT + timedelta(days=1, microseconds=-1)):
        budget = CreditBudget(db_pool, 'api', 10, clock=lambda: timestamp)
        assert budget.next_reset() == MIDNIGHT + timedelta(days=1)
        assert budget.next_reset().tzinfo == timezone.utc


async def test_clock_is_normalized_to_utc(db_pool):
    local_time = datetime(2026, 10, 19, 1, tzinfo=timezone(timedelta(hours=4)))
    await record_call(db_pool, 'api', '/', 3, ok=True, ts=MIDNIGHT)
    budget = CreditBudget(db_pool, 'api', 10, clock=lambda: local_time)
    assert await budget.spent_today() == Decimal(3)
    assert budget.next_reset() == MIDNIGHT + timedelta(days=1)


async def test_paced_budget_spreads_daily_limit(db_pool):
    """С графиком к 12:00 UTC доступно (12 ч + 1 ч запаса) / 24 дневного лимита."""
    budget = CreditBudget(db_pool, 'paced', 2400, clock=lambda: NOW, pace=True)
    assert budget.allowed_now() == Decimal(1300)
    await record_call(db_pool, 'paced', '/x', 1000, ok=True, ts=NOW - timedelta(hours=1))
    assert await budget.remaining() == Decimal(300)
    assert await budget.can_spend(300) and not await budget.can_spend(301)
    early = CreditBudget(db_pool, 'paced', 2400, clock=lambda: MIDNIGHT + timedelta(minutes=8), pace=True)
    assert early.allowed_now() == Decimal(113)   # первые 8 минут суток: не больше ~1 ч бюджета
    late = CreditBudget(db_pool, 'paced', 2400, clock=lambda: MIDNIGHT + timedelta(hours=23, minutes=30), pace=True)
    assert late.allowed_now() == Decimal(2400)


async def test_paced_budget_retries_soon_unless_day_is_spent(db_pool):
    budget = CreditBudget(db_pool, 'paced', 2400, clock=lambda: NOW, pace=True)
    await record_call(db_pool, 'paced', '/x', 1300, ok=True, ts=NOW - timedelta(minutes=1))
    assert not await budget.can_spend(1)
    assert budget.next_reset() == NOW + timedelta(minutes=15)
    await record_call(db_pool, 'paced', '/x', 1100, ok=True, ts=NOW - timedelta(minutes=1))
    assert not await budget.can_spend(1)
    assert budget.next_reset() == MIDNIGHT + timedelta(days=1)
    near_midnight = CreditBudget(db_pool, 'other', 2400, clock=lambda: MIDNIGHT + timedelta(hours=23, minutes=55), pace=True)
    assert await near_midnight.can_spend(1)
    assert near_midnight.next_reset() == MIDNIGHT + timedelta(days=1)


async def test_unpaced_budget_unchanged(db_pool):
    budget = CreditBudget(db_pool, 'flat', 2400, clock=lambda: MIDNIGHT + timedelta(minutes=8))
    assert budget.allowed_now() == Decimal(2400)
    assert await budget.remaining() == Decimal(2400)
    assert budget.next_reset() == MIDNIGHT + timedelta(days=1)


# ───────────── гарантированные доли по видам задач ─────────────

SHARES = {'search': .55, 'smarts': .10, 'wallets': .15, 'history': .20}


async def pending(pool, *kinds):
    for kind in kinds:
        await enqueue(pool, kind, f'{kind}:job', run_after=NOW)


def test_shares_validated(db_pool):
    for bad in ({'a': -.1}, {'a': .6, 'b': .5}):
        with pytest.raises(ValueError):
            CreditBudget(db_pool, 'fr', 3000, shares=bad)
    assert CreditBudget(db_pool, 'fr', 3000, shares={'a': .4, 'b': .6}).shares == {'a': Decimal('0.4'), 'b': Decimal('0.6')}


async def test_shares_reserve_other_kinds_work(db_pool):
    """Поиск не может залезть в неизрасходованные доли видов, у которых есть ждущие задачи;
    каждому виду доступна своя доля плюс всё, что не зарезервировано."""
    await pending(db_pool, 'smarts', 'wallets', 'history')
    budget = CreditBudget(db_pool, 'fr', 3000, clock=lambda: NOW, shares=SHARES)
    assert budget.allowed_now() == 3000 and budget.allowed_now('wallets') == 450 and budget.allowed_now('other') == 0
    assert await budget.remaining() == 3000                       # без вида — весь провайдер, как раньше
    assert await budget.remaining('search') == 3000 - 300 - 450 - 600
    assert await budget.remaining('wallets') == 3000 - 300 - 600  # своя доля 450 + доля поиска (у него нет задач)
    assert await budget.can_spend(400, 'wallets') and not await budget.can_spend(1651, 'search')
    await pending(db_pool, 'search')
    assert await budget.remaining('wallets') == 450
    assert await budget.remaining('other') == 0                   # вид без доли живёт только на общем остатке


async def test_shares_spent_reduce_reserve_and_own_share(db_pool):
    await pending(db_pool, 'search', 'smarts', 'wallets', 'history')
    budget = CreditBudget(db_pool, 'fr', 3000, clock=lambda: NOW, shares=SHARES)
    await record_call(db_pool, 'fr', 'history', 100, ok=True, ts=NOW)
    await record_call(db_pool, 'fr', 'search', 1000, ok=True, ts=NOW)
    assert await budget.remaining('search') == 3000 - 1100 - (300 + 450 + 500)
    assert await budget.remaining('history') == 3000 - 1100 - (300 + 450 + 650)
    # Перерасход вида сверх своей доли резерва не создаёт (резерв не бывает отрицательным).
    await record_call(db_pool, 'fr', 'history', 900, ok=True, ts=NOW)
    assert await budget.remaining('search') == 3000 - 2000 - (300 + 450 + 0)
    assert await budget.remaining('history') == 0
    assert budget.next_reset() == MIDNIGHT + timedelta(days=1)   # без графика — до полуночи


async def test_shares_released_when_kind_has_no_jobs(db_pool):
    """Доля вида без ждущих и выполняемых задач уходит остальным — ничего не пропадает."""
    await pending(db_pool, 'search', 'wallets')
    budget = CreditBudget(db_pool, 'fr', 3000, clock=lambda: NOW, shares=SHARES)
    assert await budget.remaining('search') == 3000 - 450
    job = await claim(db_pool, ['wallets'], NOW)
    assert await budget.remaining('search') == 3000 - 450         # выполняемая задача тоже держит резерв
    await complete(db_pool, job.id)
    assert await budget.remaining('search') == 3000
    await enqueue(db_pool, 'wallets', 'later', run_after=NOW + timedelta(hours=5))
    assert await budget.remaining('search') == 3000 - 450         # отложенная (например, по бюджету) — держит


async def test_shares_follow_pacing(db_pool):
    """С графиком доля копится по суткам: кошельки (400) при 15 % от 3000 дождутся своей очереди к ~20:00,
    а поиск в это время не трогает их резерв; к концу суток остаток общий."""
    await pending(db_pool, 'search', 'wallets')
    at = lambda hours: CreditBudget(db_pool, 'fr', 3000, clock=lambda: MIDNIGHT + timedelta(hours=hours),
                                    pace=True, shares=SHARES)
    early = at(0)
    assert early.allowed_now() == 125 and early.allowed_now('wallets') == 19 and early.allowed_now('search') == 69
    assert await early.remaining('search') == 125 - 19 and await early.remaining('wallets') == 125 - 69
    assert not await early.can_spend(400, 'wallets')
    assert early.next_reset() == MIDNIGHT + timedelta(minutes=15)
    assert await at(19).can_spend(400, 'wallets')      # доли smarts и history без задач — свободны для кошельков
    await pending(db_pool, 'smarts', 'history')
    assert not await at(19).can_spend(400, 'wallets') and await at(20.5).can_spend(400, 'wallets')
    await record_call(db_pool, 'fr', 'search', 1400, ok=True, ts=MIDNIGHT + timedelta(hours=20))
    late = at(20.5)
    assert late.allowed_now() == 2688 and late.allowed_now('wallets') == 403
    assert await late.remaining('wallets') == 403 and await late.remaining('search') == 78
    assert await late.can_spend(400, 'wallets')

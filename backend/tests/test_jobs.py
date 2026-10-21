import asyncio
import json
from datetime import datetime, timedelta, timezone

from kairos.pipeline.jobs import (
    claim, complete, counts, enqueue, fail, requeue_stale, reschedule, skip,
)


NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


async def test_enqueue_new_and_duplicate(db_pool):
    async with db_pool.acquire() as conn:
        assert await enqueue(conn, 'lookup', 'one', {'nested': {'ids': [1, 2]}})
    assert not await enqueue(db_pool, 'lookup', 'one', {'changed': True}, priority=10)
    row = await db_pool.fetchrow('SELECT * FROM jobs')
    assert json.loads(row['payload']) == {'nested': {'ids': [1, 2]}}
    assert row['priority'] == 0
    assert row['run_after'].utcoffset() == timedelta(0)
    assert await enqueue(db_pool, 'other', 'one')


async def test_claim_priority_then_run_after_then_id(db_pool):
    for key, priority, due in [
        ('low', 0, NOW - timedelta(days=1)),
        ('late', 5, NOW),
        ('first', 5, NOW - timedelta(hours=1)),
        ('second', 5, NOW - timedelta(hours=1)),
    ]:
        await enqueue(db_pool, 'lookup', key, priority=priority, run_after=due)
    claimed = [await claim(db_pool, now=NOW) for _ in range(4)]
    assert [job.key for job in claimed] == ['first', 'second', 'late', 'low']
    assert await claim(db_pool, now=NOW) is None


async def test_claim_filters_future_and_other_kinds(db_pool):
    await enqueue(db_pool, 'lookup', 'future', priority=100, run_after=NOW + timedelta(seconds=1))
    await enqueue(db_pool, 'other', 'due', priority=100, run_after=NOW)
    await enqueue(db_pool, 'lookup', 'due', run_after=NOW)
    assert (await claim(db_pool, kinds=('lookup',), now=NOW)).key == 'due'
    assert await claim(db_pool, kinds=['lookup'], now=NOW) is None
    assert await claim(db_pool, kinds=[], now=NOW) is None
    assert (await claim(db_pool, now=NOW)).kind == 'other'


async def test_claim_marks_running_and_increments_attempts(db_pool):
    await enqueue(db_pool, 'lookup', 'one', {'name': 'sample'}, run_after=NOW)
    job = await claim(db_pool, now=NOW)
    assert job.attempts == 1
    assert job.payload == {'name': 'sample'}
    assert job.run_after == NOW
    row = await db_pool.fetchrow('SELECT * FROM jobs WHERE id = $1', job.id)
    assert row['status'] == 'running'
    assert row['attempts'] == 1
    assert row['locked_at'] == row['updated_at'] == NOW


async def test_concurrent_claims_one_job(db_pool):
    await enqueue(db_pool, 'lookup', 'one', run_after=NOW)
    results = await asyncio.gather(claim(db_pool, now=NOW), claim(db_pool, now=NOW))
    assert sum(job is not None for job in results) == 1
    assert await db_pool.fetchval('SELECT attempts FROM jobs') == 1


async def test_concurrent_claims_two_jobs(db_pool):
    await enqueue(db_pool, 'lookup', 'one', run_after=NOW)
    await enqueue(db_pool, 'lookup', 'two', run_after=NOW)
    first, second = await asyncio.gather(claim(db_pool, now=NOW), claim(db_pool, now=NOW))
    assert first is not None and second is not None
    assert first.id != second.id
    assert first.attempts == second.attempts == 1


async def test_complete_stores_result(db_pool):
    await enqueue(db_pool, 'lookup', 'one', run_after=NOW)
    job = await claim(db_pool, now=NOW)
    result = {'found': True, 'data': {'ids': [1, 2]}}
    await complete(db_pool, job.id, result)
    row = await db_pool.fetchrow('SELECT * FROM jobs WHERE id = $1', job.id)
    assert row['status'] == 'done'
    assert json.loads(row['result']) == result
    assert row['locked_at'] is None
    assert await claim(db_pool, now=NOW) is None


async def test_complete_default_result_is_null(db_pool):
    await enqueue(db_pool, 'lookup', 'one', run_after=NOW)
    job = await claim(db_pool, now=NOW)
    await complete(db_pool, job.id)
    assert await db_pool.fetchval('SELECT result FROM jobs') is None


async def test_skip(db_pool):
    await enqueue(db_pool, 'lookup', 'one', run_after=NOW)
    job = await claim(db_pool, now=NOW)
    await skip(db_pool, job.id, 'not applicable')
    row = await db_pool.fetchrow('SELECT * FROM jobs')
    assert row['status'] == 'skipped'
    assert row['last_error'] == 'not applicable'
    assert row['locked_at'] is None


async def test_reschedule_refunds_claim_attempt_and_waits(db_pool):
    await enqueue(db_pool, 'lookup', 'one', run_after=NOW)
    job = await claim(db_pool, now=NOW)
    later = NOW + timedelta(days=1)
    await reschedule(db_pool, job.id, later, 'budget exhausted')
    row = await db_pool.fetchrow('SELECT * FROM jobs')
    assert row['status'] == 'pending'
    assert row['attempts'] == job.attempts - 1 == 0
    assert row['last_error'] == 'budget exhausted'
    assert row['locked_at'] is None
    assert await claim(db_pool, now=later - timedelta(microseconds=1)) is None
    retried = await claim(db_pool, now=later)
    assert retried.id == job.id
    assert retried.attempts == job.attempts


async def test_fail_retries_then_stops_at_max_attempts(db_pool):
    now = datetime.now(timezone.utc)
    await enqueue(db_pool, 'lookup', 'one', run_after=now)
    job = await claim(db_pool, now=now)
    before = await db_pool.fetchval('SELECT now()')
    assert await fail(db_pool, job.id, 'temporary', timedelta(minutes=5), max_attempts=2)
    after = await db_pool.fetchval('SELECT now()')
    row = await db_pool.fetchrow('SELECT * FROM jobs')
    assert row['status'] == 'pending'
    assert before + timedelta(minutes=5) <= row['run_after'] <= after + timedelta(minutes=5)
    assert row['attempts'] == 1
    assert row['last_error'] == 'temporary'
    assert row['locked_at'] is None
    assert await claim(db_pool, now=before) is None
    job = await claim(db_pool, now=row['run_after'])
    assert job.attempts == 2
    assert not await fail(db_pool, job.id, 'permanent', timedelta(minutes=5), max_attempts=2)
    row = await db_pool.fetchrow('SELECT * FROM jobs')
    assert row['status'] == 'failed'
    assert row['last_error'] == 'permanent'
    assert row['locked_at'] is None


async def test_fail_without_retry_and_truncates_error(db_pool):
    await enqueue(db_pool, 'lookup', 'one', run_after=NOW)
    job = await claim(db_pool, now=NOW)
    assert not await fail(db_pool, job.id, 'x' * 2500)
    row = await db_pool.fetchrow('SELECT * FROM jobs')
    assert row['status'] == 'failed'
    assert row['last_error'] == 'x' * 2000


async def test_requeue_stale_only_running_before_cutoff(db_pool):
    for key in ('stale', 'boundary', 'fresh', 'done', 'pending'):
        await enqueue(db_pool, 'lookup', key, run_after=NOW - timedelta(hours=2))
    stale = await claim(db_pool, now=NOW - timedelta(hours=2))
    boundary = await claim(db_pool, now=NOW - timedelta(hours=1))
    fresh = await claim(db_pool, now=NOW)
    done = await claim(db_pool, now=NOW - timedelta(hours=2))
    await complete(db_pool, done.id)
    assert await requeue_stale(db_pool, timedelta(hours=1), now=NOW) == 1
    rows = {row['id']: row for row in await db_pool.fetch('SELECT * FROM jobs')}
    assert rows[stale.id]['status'] == 'pending'
    assert rows[stale.id]['locked_at'] is None
    assert rows[stale.id]['attempts'] == 1
    assert rows[stale.id]['updated_at'] == NOW
    assert rows[boundary.id]['status'] == rows[fresh.id]['status'] == 'running'
    assert rows[done.id]['status'] == 'done'
    assert await requeue_stale(db_pool, timedelta(hours=1), now=NOW) == 0


async def test_counts(db_pool):
    assert await counts(db_pool) == {}
    for key in ('done', 'skipped', 'failed', 'running', 'pending', 'pending2'):
        await enqueue(db_pool, 'lookup', key, run_after=NOW)
    await complete(db_pool, (await claim(db_pool, now=NOW)).id)
    await skip(db_pool, (await claim(db_pool, now=NOW)).id, 'skip')
    await fail(db_pool, (await claim(db_pool, now=NOW)).id, 'fail')
    await claim(db_pool, now=NOW)
    assert await counts(db_pool) == {
        'done': 1, 'skipped': 1, 'failed': 1, 'running': 1, 'pending': 2,
    }

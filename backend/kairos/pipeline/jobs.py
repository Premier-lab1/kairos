"""Durable PostgreSQL jobs with atomic claims and explicit retry handling."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg


@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    key: str
    payload: dict
    priority: int
    attempts: int
    run_after: datetime


async def enqueue(
    db: asyncpg.Pool | asyncpg.Connection,
    kind: str,
    key: str,
    payload: dict | None = None,
    priority: int = 0,
    run_after: datetime | None = None,
) -> bool:
    job_id = await db.fetchval(
        """INSERT INTO jobs (kind, key, payload, priority, run_after)
           VALUES ($1, $2, $3::jsonb, $4, COALESCE($5::timestamptz, now()))
           ON CONFLICT (kind, key) DO NOTHING RETURNING id""",
        kind, key, json.dumps(payload if payload is not None else {}), priority, run_after,
    )
    return job_id is not None


async def claim(
    pool: asyncpg.Pool,
    kinds: Sequence[str] | None = None,
    now: datetime | None = None,
) -> Job | None:
    timestamp = now if now is not None else datetime.now(timezone.utc)
    row = await pool.fetchrow(
        """WITH due AS (
               SELECT id FROM jobs
               WHERE status = 'pending' AND run_after <= $1
                 AND ($2::text[] IS NULL OR kind = ANY($2::text[]))
               ORDER BY priority DESC, run_after, id
               LIMIT 1 FOR UPDATE SKIP LOCKED
           )
           UPDATE jobs AS j SET status = 'running', attempts = j.attempts + 1,
               locked_at = $1, updated_at = $1
           FROM due WHERE j.id = due.id
           RETURNING j.id, j.kind, j.key, j.payload, j.priority, j.attempts, j.run_after""",
        timestamp, list(kinds) if kinds is not None else None,
    )
    if row is None:
        return None
    values = dict(row)
    if isinstance(values['payload'], str):
        values['payload'] = json.loads(values['payload'])
    values['run_after'] = values['run_after'].astimezone(timezone.utc)
    return Job(**values)


async def complete(pool: asyncpg.Pool, job_id: int, result: dict | None = None) -> None:
    await pool.execute(
        """UPDATE jobs SET status = 'done', result = $2::jsonb,
           locked_at = NULL, updated_at = now() WHERE id = $1""",
        job_id, json.dumps(result) if result is not None else None,
    )


async def skip(pool: asyncpg.Pool, job_id: int, reason: str) -> None:
    await pool.execute(
        """UPDATE jobs SET status = 'skipped', last_error = $2,
           locked_at = NULL, updated_at = now() WHERE id = $1""",
        job_id, reason,
    )


async def reschedule(
    pool: asyncpg.Pool, job_id: int, run_after: datetime, note: str | None = None,
) -> None:
    await pool.execute(
        """UPDATE jobs SET status = 'pending', run_after = $2,
           attempts = GREATEST(0, attempts - 1), last_error = $3,
           locked_at = NULL, updated_at = now() WHERE id = $1""",
        job_id, run_after, note,
    )


async def fail(
    pool: asyncpg.Pool,
    job_id: int,
    error: str,
    retry_in: timedelta | None = None,
    max_attempts: int = 5,
) -> bool:
    status = await pool.fetchval(
        """UPDATE jobs SET
               status = CASE WHEN attempts < $4 AND $3::interval IS NOT NULL
                             THEN 'pending' ELSE 'failed' END,
               run_after = CASE WHEN attempts < $4 AND $3::interval IS NOT NULL
                                THEN now() + $3::interval ELSE run_after END,
               last_error = $2, locked_at = NULL, updated_at = now()
           WHERE id = $1 RETURNING status""",
        job_id, error[:2000], retry_in, max_attempts,
    )
    return status == 'pending'


async def requeue_stale(
    pool: asyncpg.Pool, older_than: timedelta, now: datetime | None = None,
) -> int:
    timestamp = now if now is not None else datetime.now(timezone.utc)
    count = await pool.fetchval(
        """WITH requeued AS (
               UPDATE jobs SET status = 'pending', locked_at = NULL, updated_at = $1
               WHERE status = 'running' AND locked_at < $1::timestamptz - $2::interval
               RETURNING id
           ) SELECT count(*) FROM requeued""",
        timestamp, older_than,
    )
    return count


async def counts(pool: asyncpg.Pool) -> dict[str, int]:
    rows = await pool.fetch('SELECT status, count(*) AS count FROM jobs GROUP BY status')
    return {row['status']: row['count'] for row in rows}

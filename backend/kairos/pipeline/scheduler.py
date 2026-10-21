"""Планировщик: какие токены искать во Frontrun.

Новых токенов на Pump.fun ≈13 в минуту (замер 18.10.2026), а поиск стоит 2 кредита: искать все —
≈37 000 кредитов в день при бюджете ≈3 300. Поэтому:
- все мигрировавшие токены (ушли с кривой — значит, на них уже смотрят) — приоритет 10;
- детерминированная выборка новых токенов (search_new_token_sample_pct процентов, по хешу mint),
  через search_new_token_delay_s после создания — приоритет 1.
Бюджет всё равно ограничивает воркер: лишние задачи ждут следующих суток.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import asyncpg

from kairos.config import Settings
from kairos.pipeline.frontrun_tasks import KIND_SEARCH

LOOKBACK = timedelta(hours=1)  # дальше в прошлое планировщик не заглядывает


async def schedule_searches(pool: asyncpg.Pool, settings: Settings, now: datetime) -> int:
    """Ставит задачи поиска CA. Возвращает число новых задач."""
    created = 0
    if settings.search_on_migration:
        created += _count(await pool.execute(
            """INSERT INTO jobs (kind, key, payload, priority, run_after)
               SELECT $1, mint || ':migrate',
                      jsonb_build_object('mint', mint, 'trigger', 'migrate'), 10, $2
               FROM tokens
               WHERE chain = 'SOLANA' AND migrated_at > $2::timestamptz - $3::interval
               ON CONFLICT (kind, key) DO NOTHING""",
            KIND_SEARCH, now, LOOKBACK))
    pct = max(0, min(100, settings.search_new_token_sample_pct))
    if pct:
        delay = timedelta(seconds=settings.search_new_token_delay_s)
        created += _count(await pool.execute(
            """INSERT INTO jobs (kind, key, payload, priority, run_after)
               SELECT $1, mint || ':create',
                      jsonb_build_object('mint', mint, 'trigger', 'create'), 1, created_at + $4::interval
               FROM tokens
               WHERE chain = 'SOLANA'
                 AND created_at > $2::timestamptz - $4::interval - $3::interval
                 AND created_at <= $2::timestamptz - $4::interval
                 AND (hashtext(mint) & 2147483647) % 100 < $5
               ON CONFLICT (kind, key) DO NOTHING""",
            KIND_SEARCH, now, LOOKBACK, delay, pct))
    return created


async def link_mentions(pool: asyncpg.Pool) -> int:
    """Привязывает упоминания к токенам, которые появились в базе позже твита."""
    return _count(await pool.execute(
        """UPDATE mentions m SET token_id = t.id
           FROM tokens t
           WHERE m.token_id IS NULL AND t.chain = m.chain AND t.mint = m.ca"""))


def _count(status: str) -> int:
    # asyncpg возвращает тег команды: 'INSERT 0 5' / 'UPDATE 3'
    return int(status.rsplit(" ", 1)[-1])

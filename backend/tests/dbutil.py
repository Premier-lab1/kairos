"""Общая подготовка тестовой БД."""

import asyncio

import asyncpg


async def reset_schema(conn: asyncpg.Connection, attempts: int = 5) -> None:
    """Пересоздаёт схему public. С TimescaleDB снос схемы иногда сталкивается с фоновым
    процессом расширения (deadlock detected) — тогда повторяем."""
    for attempt in range(attempts):
        try:
            await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            return
        except asyncpg.exceptions.DeadlockDetectedError:
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))

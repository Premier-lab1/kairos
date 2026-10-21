"""Подключение к PostgreSQL и применение миграций из backend/migrations/*.sql."""

import asyncio
import sys
from pathlib import Path

import asyncpg

from kairos.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


async def create_pool(dsn: str | None = None, **kwargs) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn or get_settings().database_url, **kwargs)


async def migrate(conn: asyncpg.Connection) -> list[str]:
    """Применяет ещё не применённые миграции по порядку имён. Возвращает список применённых."""
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               name text PRIMARY KEY,
               applied_at timestamptz NOT NULL DEFAULT now()
           )"""
    )
    done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in done:
            continue
        async with conn.transaction():
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
        applied.append(path.name)
    return applied


async def _main() -> None:
    dsn = sys.argv[1] if len(sys.argv) > 1 else None
    conn = await asyncpg.connect(dsn or get_settings().database_url)
    try:
        applied = await migrate(conn)
        print("applied:", ", ".join(applied) if applied else "nothing new")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(_main())

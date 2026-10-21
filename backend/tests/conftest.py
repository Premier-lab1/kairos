import os

import asyncpg
import pytest

from dbutil import reset_schema
from kairos.db import migrate

TEST_DSN = os.environ.get("KAIROS_TEST_DATABASE_URL", os.environ.get("KAIROS_TEST_PG", "postgresql://kairos@localhost:5434") + "/kairos_test")


@pytest.fixture
async def db_pool():
    """Чистая схема на каждый тест: public пересоздаётся и мигрируется заново."""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await reset_schema(conn)
        await migrate(conn)
    finally:
        await conn.close()
    pool = await asyncpg.create_pool(TEST_DSN, min_size=1, max_size=4)
    yield pool
    await pool.close()

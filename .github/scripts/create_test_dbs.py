"""Create the per-module test databases used by backend/tests (CI helper).

Test modules connect to databases named kairos_test, kairos_test_bot, kairos_test_helius, ...
The names are read from the test sources, so a new module needs no CI change.
Run from the backend/ directory: python ../.github/scripts/create_test_dbs.py
"""

import asyncio
import pathlib
import re

import asyncpg

ADMIN_DSN = "postgresql://kairos@localhost:5434/postgres"


def database_names(tests_dir: pathlib.Path) -> set[str]:
    names = {"kairos_test"}
    for path in tests_dir.glob("*.py"):
        names.update(re.findall(r"kairos_test_[a-z0-9_]+", path.read_text(encoding="utf-8")))
    return names


async def main() -> None:
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        for name in sorted(database_names(pathlib.Path("tests"))):
            if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
                await conn.execute(f'CREATE DATABASE "{name}"')
                print("created", name)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

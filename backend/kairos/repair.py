"""Разовый ремонт данных после исправлений 19.10.2026 (docs/fix-2026-10-19.md).

1. ``bogus-creates`` — токены, испорченные ложными событиями PumpPortal ``create`` пула bonk. Если токен LaunchLab
   котируется не в SOL (USDC, PUMP, xStocks…), PumpPortal кладёт в поле ``mint`` mint КОТИРОВКИ, а у события нет
   ``solInPool``. Так USDC получил символ POTION, а давно существующие токены — «время создания» 18.10.2026 (твиты
   по ним выглядели на месяцы раньше создания). Такие токены возвращаются в состояние «ничего не известно»: поля
   из ложного события стираются, сделки/снимки/исходы/сигналы удаляются, ``trades_synced_until`` = NULL. Дальше
   конвейер сам делает пробный бэкфилл с начала истории mint и берёт время создания и метаданные из транзакции
   создания (kairos/sources/creation.py). Отбор (только токены с упоминаниями — остальные на исходы шиллеров не
   влияют): есть упоминание раньше ``created_at`` больше чем на 10 минут (твит не может быть раньше создания), или по
   mint в ``raw_events`` больше одного события ``create`` с разными подписями (у настоящего токена оно одно, а mint
   котировки вроде USDC приходит в каждом запуске с этой котировкой).

2. ``amm-rebackfill`` — перекачка сделок после миграции у токенов, где разбор PumpSwap брал резервы чужого пула
   (сделка-маршрут) или пула, котируемого не в SOL (kairos/sources/helius.py). Одиночные выбросы исходы уже
   отфильтровывают (``outcomes.despike``), поэтому перекачиваются только токены, где ряд перемешан: доля соседних
   снимков Helius после миграции с прыжком ликвидности больше чем в 3 раза ≥ ``--min-jump-pct``. Тратит кредиты
   Helius из того же дневного бюджета (``api_calls``), не больше ``--max-credits``.

3. ``zero-prices`` — сделки и снимки Helius с нулевой ценой. Это кривые Pump.fun с котировкой не в SOL (``BuyV2``:
   все SOL-поля события нулевые); разбор теперь не даёт им цены, а старые нули среди настоящих цен давали ложные
   обвалы. ``market_cap_sol`` таких сделок → NULL, такие снимки удаляются. Кредитов не тратит.

После ремонта пересчитываются исходы затронутых токенов. Без ``--apply`` — только список и оценка.

Запуск: python -m kairos.repair bogus-creates [--apply]
        python -m kairos.repair amm-rebackfill [--token-id N ...] [--min-jump-pct 2.5] [--max-credits 3000] [--apply]
        python -m kairos.repair zero-prices [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from kairos import outcomes

log = logging.getLogger(__name__)

MENTION_BEFORE_CREATION = timedelta(minutes=10)
MIGRATION_SLACK = timedelta(minutes=1)
CREDITS_PER_PAGE = 10
JUMP_RATIO = 3
RESET_FLAGS = ("helius_no_pump_trades", "helius_cost_capped", "helius_creation_checked")

_BOGUS_SQL = """
WITH creates AS (
    SELECT payload->>'mint' AS mint, count(DISTINCT payload->>'signature') AS n FROM raw_events
    WHERE source = 'pumpportal' AND kind = 'create' GROUP BY 1 HAVING count(DISTINCT payload->>'signature') > 1
)
SELECT t.id, t.mint, t.symbol, t.created_at, t.trades_synced_until AS synced,
       (SELECT min(m.tweet_time) FROM mentions m WHERE m.token_id = t.id) AS first_mention,
       coalesce((SELECT n FROM creates c WHERE c.mint = t.mint), 1) AS create_events
FROM tokens t
WHERE t.chain = 'SOLANA' AND t.created_at IS NOT NULL
  AND EXISTS (SELECT 1 FROM mentions m WHERE m.token_id = t.id)
  AND (t.mint IN (SELECT mint FROM creates)
       OR EXISTS (SELECT 1 FROM mentions m WHERE m.token_id = t.id AND m.tweet_time < t.created_at - $1::interval))
ORDER BY t.id
"""

_JUMPS_SQL = """
WITH s AS (
    SELECT s.token_id, s.liquidity_sol AS l,
           lag(s.liquidity_sol) OVER (PARTITION BY s.token_id ORDER BY s.ts) AS pl
    FROM token_snapshots s JOIN tokens t ON t.id = s.token_id
    WHERE s.source = 'helius' AND t.migrated_at IS NOT NULL AND s.ts >= t.migrated_at
      AND t.trades_synced_until IS NOT NULL
)
SELECT token_id, count(*) AS n,
       count(*) FILTER (WHERE pl > 0 AND l > 0 AND (l / pl > $1 OR pl / l > $1)) AS jumps
FROM s GROUP BY token_id
"""


async def find_bogus_creates(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in await conn.fetch(_BOGUS_SQL, MENTION_BEFORE_CREATION)]


async def reset_token(conn: asyncpg.Connection, token_id: int) -> None:
    """Токен как только что найденный в истории шиллеров: создание и сделки неизвестны, всё производное удалено."""
    async with conn.transaction():
        for table in ("token_outcomes", "signals", "trades", "token_snapshots"):
            await conn.execute(f"DELETE FROM {table} WHERE token_id = $1", token_id)
        await conn.execute(
            """UPDATE tokens SET name = NULL, symbol = NULL, uri = NULL, launchpad = NULL, creator_wallet_id = NULL,
                      bonding_curve = NULL, create_signature = NULL, created_at = NULL, initial_buy_sol = NULL,
                      initial_market_cap_sol = NULL, migrated_at = NULL, migration_pool = NULL,
                      trades_synced_until = NULL, trades_backfill_truncated = false,
                      metadata = CASE WHEN jsonb_typeof(metadata) = 'object' THEN metadata - $2::text[] ELSE metadata END
               WHERE id = $1""", token_id, list(RESET_FLAGS))


async def repair_bogus_creates(conn: asyncpg.Connection, apply: bool) -> list[dict[str, Any]]:
    rows = await find_bogus_creates(conn)
    for r in rows:
        log.info("bogus create: id=%s %s symbol=%s created_at=%s first_mention=%s create_events=%s",
                 r["id"], r["mint"], r["symbol"], r["created_at"], r["first_mention"], r["create_events"])
        if apply:
            await reset_token(conn, r["id"])
    return rows


async def find_mixed_pool_tokens(conn: asyncpg.Connection, min_jump_pct: float) -> list[int]:
    out = []
    for r in await conn.fetch(_JUMPS_SQL, JUMP_RATIO):
        if r["n"] and 100.0 * r["jumps"] / r["n"] >= min_jump_pct:
            out.append(r["token_id"])
    return sorted(out)


async def rebackfill_amm(pool: asyncpg.Pool, client: Any, token_ids: list[int], *, max_credits: int,
                         budget: Any, apply: bool) -> list[dict[str, Any]]:
    """Удаляет сделки и снимки Helius после миграции и качает их заново исправленным разбором."""
    from kairos.collectors.helius_trades import BackfillResult, backfill_token

    report = []
    spent = 0
    for token_id in token_ids:
        t = await pool.fetchrow(
            """SELECT id, mint, migrated_at, trades_synced_until AS synced,
                      (SELECT count(*) FROM trades WHERE token_id = tokens.id AND ts >= tokens.migrated_at - $2::interval) AS n
               FROM tokens WHERE id = $1""", token_id, MIGRATION_SLACK)
        if t is None or t["migrated_at"] is None or t["synced"] is None or t["synced"] <= t["migrated_at"]:
            report.append({"token_id": token_id, "skipped": "not migrated or not synced"})
            continue
        estimate = max(1, -(-int(t["n"]) // 100)) * CREDITS_PER_PAGE
        item: dict[str, Any] = {"token_id": token_id, "mint": t["mint"], "trades_after_migration": t["n"],
                                "estimate_credits": estimate}
        report.append(item)
        if not apply:
            continue
        left = min(max_credits - spent, int(await budget.remaining()))
        if left < estimate:
            item["skipped"] = f"credits left {left} < estimate {estimate}"
            continue
        start, until = t["migrated_at"] - MIGRATION_SLACK, t["synced"]
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM trades WHERE token_id = $1 AND ts >= $2", token_id, start)
            await conn.execute("DELETE FROM token_snapshots WHERE token_id = $1 AND ts >= $2 AND source = 'helius'",
                               token_id, start)
            await conn.execute("UPDATE tokens SET trades_synced_until = $2, trades_backfill_truncated = true WHERE id = $1",
                               token_id, start)
        result = BackfillResult()
        try:
            await backfill_token(pool, client, t["mint"], until, since=start,
                                 max_pages=max(1, left // CREDITS_PER_PAGE), result=result)
        finally:
            spent += result.credits
            exhausted = result.stopped in ("exhausted", "fallback_exhausted")
            synced = until if exhausted else (result.last_ts or start)
            await pool.execute("UPDATE tokens SET trades_synced_until = $2, trades_backfill_truncated = $3 WHERE id = $1",
                               token_id, synced, not exhausted)
            item.update(credits=result.credits, trades=result.trades, pump_trades=result.pump_trades,
                        stopped=result.stopped, synced_until=synced)
    return report


async def repair_zero_prices(conn: asyncpg.Connection, apply: bool) -> list[int]:
    """Токены со сделками/снимками Helius с нулевой ценой; при ``apply`` нули убираются (см. пункт 3 выше)."""
    ids = [r["token_id"] for r in await conn.fetch(
        """SELECT token_id FROM trades WHERE source = 'helius' AND market_cap_sol = 0
           UNION SELECT token_id FROM token_snapshots WHERE source = 'helius' AND (price_sol = 0 OR market_cap_sol = 0)
           ORDER BY 1""")]
    if apply and ids:
        async with conn.transaction():
            await conn.execute("UPDATE trades SET market_cap_sol = NULL WHERE source = 'helius' AND market_cap_sol = 0")
            await conn.execute(
                "DELETE FROM token_snapshots WHERE source = 'helius' AND (price_sol = 0 OR market_cap_sol = 0)")
    return ids


async def recompute(pool: asyncpg.Pool, token_ids: list[int]) -> int:
    n = 0
    now = datetime.now(timezone.utc)
    for token_id in token_ids:
        for rules in outcomes.RULESETS:
            n += len(await outcomes.compute_token_outcomes(pool, token_id, now=now, rules=rules))
    return n


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="One-off data repair after the 2026-10-19 fixes.")
    parser.add_argument("command", choices=("bogus-creates", "amm-rebackfill", "zero-prices"))
    parser.add_argument("--apply", action="store_true", help="change data (default: dry run)")
    parser.add_argument("--dsn")
    parser.add_argument("--token-id", type=int, action="append", default=[])
    parser.add_argument("--min-jump-pct", type=float, default=2.5)
    parser.add_argument("--max-credits", type=int, default=3000)
    return parser.parse_args(argv)


async def _main(args) -> None:
    from kairos.config import get_settings

    settings = get_settings()
    pool = await asyncpg.create_pool(args.dsn or settings.database_url, min_size=1, max_size=4)
    try:
        if args.command == "bogus-creates":
            async with pool.acquire() as conn:
                rows = await repair_bogus_creates(conn, args.apply)
            print(f"bogus creates: {len(rows)} tokens ({sum(r['synced'] is not None for r in rows)} with trades)"
                  + ("" if args.apply else " - dry run, nothing changed"))
            for r in rows:
                print(f"  {r['id']:>6} {r['mint']} {r['symbol'] or '-'} created_at={r['created_at']:%Y-%m-%d %H:%M} "
                      f"first_mention={r['first_mention'] or '-'} create_events={r['create_events']}")
            return
        if args.command == "zero-prices":
            async with pool.acquire() as conn:
                ids = await repair_zero_prices(conn, args.apply)
            print(f"zero prices: {len(ids)} tokens" + ("" if args.apply else " - dry run, nothing changed"))
            if args.apply:
                print(f"outcomes recomputed: {await recompute(pool, ids)}")
            return
        from kairos.pipeline.budget import CreditBudget
        from kairos.sources.helius import HeliusClient

        async with pool.acquire() as conn:
            ids = args.token_id or await find_mixed_pool_tokens(conn, args.min_jump_pct)
        budget = CreditBudget(pool, "helius", settings.helius_daily_credits)
        client = HeliusClient.from_settings(settings) if args.apply else None
        try:
            report = await rebackfill_amm(pool, client, ids, max_credits=args.max_credits, budget=budget,
                                          apply=args.apply)
        finally:
            if client is not None:
                await client.aclose()
        for item in report:
            print("  " + " ".join(f"{k}={v}" for k, v in item.items()))
        print(f"estimate: {sum(i.get('estimate_credits', 0) for i in report)} credits"
              + ("" if args.apply else " - dry run, nothing changed"))
        if args.apply:
            print(f"outcomes recomputed: {await recompute(pool, [i['token_id'] for i in report if 'credits' in i])}")
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_main(parse_args()))

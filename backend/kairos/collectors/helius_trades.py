"""Запись сделок и снимков токена из Helius в БД и бэкфилл истории токена.

Как это работает
================
``backfill_token(pool, client, mint, until)`` листает историю mint через getTransactionsForAddress
(по возрастанию, с created_at токена минус запас до ``until``), разбирает сделки (kairos.sources.helius) и
идемпотентно пишет их в ``trades`` и ``token_snapshots``. Повторный запуск ничего не дублирует.

Снимок после каждой сделки (одна строка на секунду, при совпадении ts побеждает последняя сделка):
  * price_sol       = virtual_sol / virtual_tokens          (резервы ПОСЛЕ сделки из TradeEvent)
  * market_cap_sol  = price_sol * 1e9                        (весь выпуск Pump.fun - 1 млрд токенов)
  * liquidity_sol   = РЕАЛЬНЫЙ SOL в кривой (real_sol_reserves; для PumpSwap - SOL в пуле).
    Почему реальный, а не виртуальный: виртуальный резерв содержит фиктивные 30 SOL и после рага не падает до нуля,
    поэтому по нему нельзя отличить рага/смерть. Реальный стартует с 0, к миграции ~85 SOL и обнуляется при выводе.
    Замечание: сборщик PumpPortal пишет в liquidity_sol для снимка создания ``vSolInBondingCurve`` (виртуальный),
    поэтому самая первая строка токена на ~30 SOL выше, чем по этой схеме.
Строки снимков пишутся только поверх своих же (``source = 'helius'``), чужие источники не перетираются.

Каждая страница фиксируется в ``api_calls`` (provider='helius', кредиты - по счётчику клиента).
Если getTransactionsForAddress недоступен на плане (``HeliusError.is_unsupported``), бэкфилл уходит на
getSignaturesForAddress + getTransaction (``fallback=True``).

Live-режим: вызвать ``backfill_token(..., since=<ts последней сделки>, until=now)`` с нужной периодичностью.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import asyncpg

from kairos.sources.creation import TokenCreation, find_creation
from kairos.sources.helius import HeliusClient, HeliusError, TradeRecord, parse_transactions

log = logging.getLogger(__name__)

SOURCE = "helius"
CREATED_AT_SLACK = timedelta(minutes=5)  # created_at от PumpPortal - момент приёма, он позже блока создания


@dataclass(frozen=True)
class Snapshot:
    ts: datetime
    price_sol: Decimal | None
    market_cap_sol: Decimal | None
    liquidity_sol: Decimal | None


def snapshot_from_trade(rec: TradeRecord) -> Snapshot | None:
    """Снимок по резервам после сделки; None, если резервов нет (запасной разбор по балансам)."""
    price = rec.price_sol
    if price is None:
        return None
    return Snapshot(rec.ts, price, price * rec.supply, rec.real_sol)


@dataclass
class BackfillResult:
    creation: TokenCreation | None = None
    pages: int = 0
    transactions: int = 0
    trades: int = 0  # новые строки trades
    snapshots: int = 0
    credits: int = 0
    stopped: str = ""  # exhausted | stalled | max_pages | fallback_exhausted
    used_fallback: bool = False
    pump_trades: int = 0  # разобранные сделки Pump.fun / PumpSwap (без запасного разбора по балансам)
    last_ts: datetime | None = None  # время блока последней разобранной сделки этого прохода (точка возобновления)
    last_token: str | None = None  # paginationToken, с которого продолжается обход (None - обход закончен)

    def note_trades(self, records: list[TradeRecord]) -> None:
        """Учитывает разобранные страницей сделки (записи идут по возрастанию времени)."""
        if not records:
            return
        self.pump_trades += sum(1 for r in records if r.program in ("pump", "pump-amm"))
        last = max(r.ts for r in records)
        self.last_ts = last if self.last_ts is None else max(self.last_ts, last)


class TradeStore:
    """Идемпотентная запись сделок/снимков. Повтор тех же данных ничего не меняет."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get_token(self, mint: str) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            "SELECT id, created_at, bonding_curve FROM tokens WHERE chain = 'SOLANA' AND mint = $1", mint)

    async def ensure_token(self, mint: str) -> int:
        return await self.pool.fetchval(
            """INSERT INTO tokens (chain, mint, source) VALUES ('SOLANA', $1, $2)
               ON CONFLICT (chain, mint) DO UPDATE SET mint = EXCLUDED.mint RETURNING id""", mint, SOURCE)

    async def save_creation(self, token_id: int, c: TokenCreation) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow("SELECT create_signature FROM tokens WHERE id = $1 FOR UPDATE", token_id)
            if row is None:
                return
            same = row["create_signature"] == c.signature
            false_event = row["create_signature"] is not None and not same
            wallet_id = None
            if c.creator:
                wallet_id = await conn.fetchval(
                    """INSERT INTO wallets (chain, address) VALUES ('SOLANA', $1)
                       ON CONFLICT (chain, address) DO UPDATE SET address = EXCLUDED.address RETURNING id""",
                    c.creator)
            await conn.execute(
                """UPDATE tokens SET created_at = $2, create_signature = $3,
                       name = CASE WHEN $9 THEN COALESCE(name, $4) WHEN $10 THEN $4 ELSE COALESCE($4, name) END,
                       symbol = CASE WHEN $9 THEN COALESCE(symbol, $5) WHEN $10 THEN $5 ELSE COALESCE($5, symbol) END,
                       uri = CASE WHEN $9 THEN COALESCE(uri, $6) WHEN $10 THEN $6 ELSE COALESCE($6, uri) END,
                       creator_wallet_id = CASE WHEN $9 THEN COALESCE(creator_wallet_id, $7) ELSE $7 END,
                       launchpad = CASE WHEN $9 THEN COALESCE(launchpad, $8) ELSE $8 END,
                       initial_buy_sol = CASE WHEN $10 THEN NULL ELSE initial_buy_sol END,
                       initial_market_cap_sol = CASE WHEN $10 THEN NULL ELSE initial_market_cap_sol END,
                       bonding_curve = CASE WHEN $10 THEN NULL ELSE bonding_curve END,
                       narrative = CASE WHEN name IS DISTINCT FROM COALESCE($4, name) OR symbol IS DISTINCT FROM COALESCE($5, symbol)
                                        OR $10 THEN NULL ELSE narrative END,
                       metadata = (CASE WHEN jsonb_typeof(metadata) = 'object' THEN metadata ELSE '{}'::jsonb END)
                                  || '{"helius_creation_checked": true}'::jsonb
                   WHERE id = $1""",
                token_id, c.ts, c.signature, c.name, c.symbol, c.uri, wallet_id, c.launchpad, same, false_event)
            if false_event:
                await conn.execute("DELETE FROM token_snapshots WHERE token_id = $1 AND source = 'pumpportal'", token_id)

    async def save_trades(self, token_id: int, records: list[TradeRecord]) -> tuple[int, int]:
        """Возвращает (новых сделок, записанных снимков)."""
        if not records:
            return 0, 0
        snaps: dict[datetime, Snapshot] = {}
        for rec in records:  # записи по возрастанию: при равных ts остаётся последняя
            snap = snapshot_from_trade(rec)
            if snap is not None:
                snaps[snap.ts] = snap
        async with self.pool.acquire() as conn, conn.transaction():
            new_trades = await conn.fetchval(
                """WITH ins AS (
                       INSERT INTO trades (ts, signature, token_id, wallet_address, side, sol_amount,
                                           token_amount, market_cap_sol, source)
                       SELECT t.ts, t.sig, $1, t.wallet, t.side, t.sol, t.tok, t.mc, t.src
                       FROM unnest($2::timestamptz[], $3::text[], $4::text[], $5::text[], $6::numeric[],
                                   $7::numeric[], $8::numeric[], $9::text[])
                            AS t(ts, sig, wallet, side, sol, tok, mc, src)
                       ON CONFLICT (signature, token_id, ts) DO NOTHING RETURNING 1)
                   SELECT count(*) FROM ins""",
                token_id, [r.ts for r in records], [r.signature for r in records], [r.wallet for r in records],
                [r.side for r in records], [r.sol_amount for r in records], [r.token_amount for r in records],
                [r.market_cap_sol for r in records], [r.source for r in records])
            n_snaps = 0
            if snaps:
                n_snaps = await conn.fetchval(
                    """WITH up AS (
                           INSERT INTO token_snapshots (ts, token_id, price_sol, market_cap_sol, liquidity_sol, source)
                           SELECT s.ts, $1, s.price, s.mc, s.liq, $6
                           FROM unnest($2::timestamptz[], $3::numeric[], $4::numeric[], $5::numeric[])
                                AS s(ts, price, mc, liq)
                           ON CONFLICT (token_id, ts) DO UPDATE SET
                               price_sol = EXCLUDED.price_sol, market_cap_sol = EXCLUDED.market_cap_sol,
                               liquidity_sol = EXCLUDED.liquidity_sol
                           WHERE token_snapshots.source = EXCLUDED.source RETURNING 1)
                       SELECT count(*) FROM up""",
                    token_id, list(snaps), [s.price_sol for s in snaps.values()],
                    [s.market_cap_sol for s in snaps.values()], [s.liquidity_sol for s in snaps.values()], SOURCE)
            amm = [r.ts for r in records if r.program == "pump-amm"]
            if amm:  # первая сделка в пуле = миграция состоялась (PumpPortal-значения не перетираем)
                await conn.execute(
                    """UPDATE tokens SET migrated_at = COALESCE(migrated_at, $2),
                                         migration_pool = COALESCE(migration_pool, 'pump-amm') WHERE id = $1""",
                    token_id, min(amm))
        return new_trades, n_snaps

    async def log_call(self, endpoint: str, credits: int, ok: bool, http_status: int | None, note: str) -> None:
        await self.pool.execute(
            "INSERT INTO api_calls (provider, endpoint, credits, http_status, ok, note) VALUES ('helius', $1, $2, $3, $4, $5)",
            endpoint, credits, http_status, ok, note[:500])


async def _backfill_legacy(store: TradeStore, client: HeliusClient, token_id: int, mint: str, address: str,
                           since: datetime | None, until: datetime, max_pages: int, result: BackfillResult) -> None:
    """getSignaturesForAddress (от новых к старым) -> окно [since, until] -> getTransaction по каждой, по возрастанию.

    Если страницы подписей кончились раньше, чем окно (лимит ``max_pages``), самая старая часть окна не видна:
    сделки не качаем (покрытие не было бы непрерывным от ``since``), ``stopped = 'max_pages'``, ``last_ts`` пуст.
    """
    since_ts, until_ts = (since.timestamp() if since else 0), until.timestamp()
    wanted: list[str] = []
    before: str | None = None
    window_reached = False
    for _ in range(max_pages):
        c0 = client.credits_spent
        sigs = await client.get_signatures_for_address(address, before=before, limit=1000)
        await store.log_call("getSignaturesForAddress", client.credits_spent - c0, True, 200, f"mint={mint} n={len(sigs)}")
        result.pages += 1
        for s in sigs:
            bt = s.get("blockTime")
            if s.get("err") is None and isinstance(bt, (int, float)) and since_ts <= bt <= until_ts and s.get("signature"):
                wanted.append(s["signature"])
        oldest = sigs[-1].get("blockTime") if sigs else None
        if len(sigs) < 1000 or (isinstance(oldest, (int, float)) and oldest < since_ts):
            window_reached = True
            break
        before = sigs[-1]["signature"]
    if not window_reached:
        result.stopped = "max_pages"
        return
    wanted.reverse()
    for i in range(0, len(wanted), 50):
        c0 = client.credits_spent
        txs = []
        ok = False
        try:
            for sig in wanted[i:i + 50]:
                tx = await client.get_transaction(sig)
                if tx is not None:
                    txs.append(tx)
            ok = True
        finally:  # кредиты уже списанных вызовов записываем и при исключении
            await store.log_call("getTransaction", client.credits_spent - c0, ok, 200 if ok else None,
                                 f"mint={mint} n={len(txs)}")
        result.transactions += len(txs)
        if result.creation is None:
            c = find_creation(txs, mint)
            if c is not None:
                await store.save_creation(token_id, c)
                result.creation = c
        records = parse_transactions(txs, mint)
        n_trades, n_snaps = await store.save_trades(token_id, records)
        result.trades += n_trades
        result.snapshots += n_snaps
        result.note_trades(records)
    result.stopped = "fallback_exhausted"


async def backfill_token(pool: asyncpg.Pool, client: HeliusClient, mint: str, until: datetime, *,
                         since: datetime | None = None, address: str | None = None, page_limit: int = 100,
                         max_pages: int = 500, fallback: bool = True,
                         result: BackfillResult | None = None) -> BackfillResult:
    """Выкачивает историю токена с ``since`` (по умолчанию created_at - 5 мин) по ``until`` и сохраняет её.

    ``result`` можно передать снаружи: при исключении посреди обхода вызывающий видит, докуда дошли (``last_ts``).
    """
    store = TradeStore(pool)
    row = await store.get_token(mint)
    token_id = row["id"] if row else await store.ensure_token(mint)
    if since is None and row is not None and row["created_at"] is not None:
        since = row["created_at"] - CREATED_AT_SLACK
    address = address or mint
    result = result if result is not None else BackfillResult()
    credits_start = client.credits_spent
    token: str | None = None
    seen_tokens: set[str] = set()
    try:
        while result.pages < max_pages:
            c0 = client.credits_spent
            try:
                page = await client.get_transactions_for_address(
                    address, since=since, until=until, sort_order="asc", limit=page_limit, pagination_token=token)
            except HeliusError as exc:
                await store.log_call("getTransactionsForAddress", 0, False, exc.http_status, f"mint={mint} {exc.message}")
                if fallback and exc.is_unsupported and result.pages == 0:
                    log.warning("getTransactionsForAddress unavailable (%s), using signatures + getTransaction", exc.message)
                    result.used_fallback = True
                    await _backfill_legacy(store, client, token_id, mint, address, since, until, max_pages, result)
                    return result
                raise
            result.pages += 1
            result.transactions += len(page.items)
            if result.creation is None:
                c = find_creation(page.items, mint)
                if c is not None:
                    await store.save_creation(token_id, c)
                    result.creation = c
            records = parse_transactions(page.items, mint)
            n_trades, n_snaps = await store.save_trades(token_id, records)
            result.trades += n_trades
            result.snapshots += n_snaps
            result.note_trades(records)
            result.last_token = page.pagination_token
            await store.log_call("getTransactionsForAddress", client.credits_spent - c0, True, 200,
                                 f"mint={mint} txs={len(page.items)} trades_new={n_trades}")
            if page.pagination_token is None:
                result.stopped = "exhausted"
                break
            if page.pagination_token in seen_tokens:
                result.stopped = "stalled"
                break
            seen_tokens.add(page.pagination_token)
            token = page.pagination_token
        else:
            result.stopped = "max_pages"
    finally:
        result.credits = client.credits_spent - credits_start
    return result

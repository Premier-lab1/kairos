"""Сборщик новых токенов и миграций Pump.fun через бесплатный поток PumpPortal.

Одно WebSocket-соединение (так требует PumpPortal), подписки subscribeNewToken и subscribeMigration,
переподключение с нарастающей паузой. В событиях нет времени блока, поэтому created_at = время приёма.

Запуск: python -m kairos.collectors.pumpportal [--duration СЕКУНД]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import asyncpg
import orjson
import websockets

from kairos.config import get_settings
from kairos.db import create_pool

log = logging.getLogger(__name__)

SOURCE = "pumpportal"
QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB",
}
# В кривой Pump.fun 30 SOL виртуальные: реальная ликвидность = vSolInBondingCurve − 30 (сверено с Helius 18.10.2026).
# Пишем реальную, как и сборщик Helius, иначе правила исхода не увидят вывод ликвидности.
PUMP_VIRTUAL_SOL = Decimal(30)
SUBSCRIPTIONS = ({"method": "subscribeNewToken"}, {"method": "subscribeMigration"})


@dataclass(frozen=True)
class NewToken:
    mint: str
    creator: str
    signature: str
    name: str | None
    symbol: str | None
    uri: str | None
    launchpad: str | None
    bonding_curve: str | None
    initial_buy_sol: Decimal | None
    market_cap_sol: Decimal | None
    v_sol_in_curve: Decimal | None
    received_at: datetime
    # Запуск LaunchLab (pool = bonk) с котировкой не в SOL: в событии нет solInPool, а в поле mint иногда приходит
    # mint котировки (USDC, PUMP, xStocks…), а не нового токена (сверено по транзакциям 19.10.2026). Цену сделок
    # таких токенов в SOL мы всё равно не считаем, поэтому событие не записываем.
    sol_quoted: bool = True


@dataclass(frozen=True)
class Migration:
    mint: str
    signature: str | None
    pool: str | None
    received_at: datetime


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def parse_event(msg: dict[str, Any], received_at: datetime) -> NewToken | Migration | None:
    """Разбирает сообщение PumpPortal. Служебные сообщения ({"message": ...}) и сделки возвращают None."""
    tx_type = msg.get("txType")
    mint = msg.get("mint")
    if not mint:
        return None
    if tx_type == "create":
        return NewToken(
            mint=mint,
            creator=msg["traderPublicKey"],
            signature=msg["signature"],
            name=msg.get("name"),
            symbol=msg.get("symbol"),
            uri=msg.get("uri"),
            launchpad=msg.get("pool"),
            bonding_curve=msg.get("bondingCurveKey"),
            initial_buy_sol=_dec(msg.get("solAmount")),
            market_cap_sol=_dec(msg.get("marketCapSol")),
            v_sol_in_curve=_dec(msg.get("vSolInBondingCurve")),
            received_at=received_at,
            sol_quoted=not (msg.get("pool") == "bonk" and "solInPool" not in msg),
        )
    if tx_type == "migrate":
        return Migration(
            mint=mint, signature=msg.get("signature"), pool=msg.get("pool"), received_at=received_at
        )
    return None


class TokenStore:
    """Запись событий в БД. Все операции идемпотентны: повтор события ничего не ломает."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def save_raw(self, kind: str, payload: bytes, received_at: datetime) -> None:
        await self.pool.execute(
            "INSERT INTO raw_events (received_at, source, kind, payload) VALUES ($1, $2, $3, $4::jsonb)",
            received_at, SOURCE, kind, payload.decode(),
        )

    async def save_new_token(self, ev: NewToken) -> int | None:
        if ev.mint in QUOTE_MINTS:
            log.warning("ignoring create for quote mint %s", ev.mint)
            return None
        if not ev.sol_quoted:
            log.info("ignoring create of a non-SOL-quoted bonk launch (mint field unreliable): %s", ev.mint)
            return None
        async with self.pool.acquire() as conn, conn.transaction():
            existing = await conn.fetchrow(
                "SELECT id, first_seen_at FROM tokens WHERE chain = 'SOLANA' AND mint = $1 FOR UPDATE", ev.mint)
            if existing and ev.received_at - existing["first_seen_at"] > timedelta(minutes=10):
                log.warning("ignoring create for previously seen mint %s", ev.mint)
                return existing["id"]
            wallet_id = await conn.fetchval(
                """INSERT INTO wallets (chain, address) VALUES ('SOLANA', $1)
                   ON CONFLICT (chain, address) DO UPDATE SET address = EXCLUDED.address
                   RETURNING id""",
                ev.creator,
            )
            token_id = await conn.fetchval(
                """INSERT INTO tokens (chain, mint, name, symbol, uri, launchpad, creator_wallet_id,
                                       bonding_curve, create_signature, created_at, initial_buy_sol,
                                       initial_market_cap_sol, source)
                   VALUES ('SOLANA', $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                   ON CONFLICT (chain, mint) DO UPDATE SET
                       name = COALESCE(tokens.name, EXCLUDED.name),
                       symbol = COALESCE(tokens.symbol, EXCLUDED.symbol),
                       uri = COALESCE(tokens.uri, EXCLUDED.uri),
                       launchpad = COALESCE(tokens.launchpad, EXCLUDED.launchpad),
                       creator_wallet_id = COALESCE(tokens.creator_wallet_id, EXCLUDED.creator_wallet_id),
                       bonding_curve = COALESCE(tokens.bonding_curve, EXCLUDED.bonding_curve),
                       create_signature = COALESCE(tokens.create_signature, EXCLUDED.create_signature),
                       created_at = LEAST(tokens.created_at, EXCLUDED.created_at),
                       initial_buy_sol = COALESCE(tokens.initial_buy_sol, EXCLUDED.initial_buy_sol),
                       initial_market_cap_sol = COALESCE(tokens.initial_market_cap_sol, EXCLUDED.initial_market_cap_sol)
                   RETURNING id""",
                ev.mint, ev.name, ev.symbol, ev.uri, ev.launchpad, wallet_id, ev.bonding_curve,
                ev.signature, ev.received_at, ev.initial_buy_sol, ev.market_cap_sol, SOURCE,
            )
            await conn.execute(
                """INSERT INTO token_snapshots (ts, token_id, market_cap_sol, liquidity_sol, source)
                   VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING""",
                ev.received_at, token_id, ev.market_cap_sol,
                None if ev.v_sol_in_curve is None else max(Decimal(0), ev.v_sol_in_curve - PUMP_VIRTUAL_SOL), SOURCE,
            )
        return token_id

    async def save_migration(self, ev: Migration) -> int:
        # Токен мог появиться до запуска сборщика: тогда заводим минимальную запись.
        return await self.pool.fetchval(
            """INSERT INTO tokens (chain, mint, migrated_at, migration_pool, source)
               VALUES ('SOLANA', $1, $2, $3, $4)
               ON CONFLICT (chain, mint) DO UPDATE SET
                   migrated_at = COALESCE(tokens.migrated_at, EXCLUDED.migrated_at),
                   migration_pool = COALESCE(tokens.migration_pool, EXCLUDED.migration_pool)
               RETURNING id""",
            ev.mint, ev.received_at, ev.pool, SOURCE,
        )


async def handle_message(store: TokenStore, raw: bytes | str, received_at: datetime) -> NewToken | Migration | None:
    data = raw.encode() if isinstance(raw, str) else raw
    try:
        msg = orjson.loads(data)
    except orjson.JSONDecodeError:
        log.warning("not json: %r", data[:200])
        return None
    if not isinstance(msg, dict):
        return None
    ev = parse_event(msg, received_at)
    if ev is None:
        if "message" in msg:
            log.info("pumpportal: %s", msg["message"])
        else:
            await store.save_raw("unknown", data, received_at)
        return None
    kind = "create" if isinstance(ev, NewToken) else "migrate"
    await store.save_raw(kind, data, received_at)
    if isinstance(ev, NewToken):
        if await store.save_new_token(ev) is None:
            return None
    else:
        await store.save_migration(ev)
    return ev


async def run(store: TokenStore, url: str, stop: asyncio.Event) -> None:
    backoff = 1.0
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=2**20) as ws:
                for sub in SUBSCRIPTIONS:
                    await ws.send(orjson.dumps(sub).decode())
                backoff = 1.0
                log.info("connected to %s", url)
                while not stop.is_set():
                    recv = asyncio.ensure_future(ws.recv())
                    stopper = asyncio.ensure_future(stop.wait())
                    done, _ = await asyncio.wait({recv, stopper}, return_when=asyncio.FIRST_COMPLETED)
                    if stopper in done:
                        recv.cancel()
                        break
                    stopper.cancel()
                    await handle_message(store, recv.result(), datetime.now(timezone.utc))
        except (OSError, websockets.ConnectionClosed, websockets.InvalidHandshake) as exc:
            if stop.is_set():
                break
            log.warning("connection lost (%s), reconnect in %.0fs", exc, backoff)
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except TimeoutError:
                pass
            backoff = min(backoff * 2, 60.0)


async def _main(duration: float | None) -> None:
    settings = get_settings()
    pool = await create_pool(min_size=1, max_size=4)
    stop = asyncio.Event()
    if duration:
        asyncio.get_running_loop().call_later(duration, stop.set)
    try:
        await run(TokenStore(pool), settings.pumpportal_ws_url, stop)
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--duration", type=float, default=None, help="остановиться через N секунд")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(_main(args.duration))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

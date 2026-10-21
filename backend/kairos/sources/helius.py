"""Клиент Helius и разбор сделок Pump.fun / PumpSwap из сырых транзакций.

Как это работает
================
Задача: достать сделки токена за первые 24 часа (backfill) и дальше следить за ним, дёшево по кредитам.

Выбранный эндпоинт: JSON-RPC ``getTransactionsForAddress`` (эксклюзивный метод Helius) c
``transactionDetails="full"``, ``sortOrder="asc"``, фильтром ``blockTime`` и адресом = mint токена.
  * Стоимость: 10 кредитов за каждые 100 полных транзакций (округление вверх, минимум 10);
    только подписи - 10 кредитов за вызов; неуспешные ответы бесплатны.
  * Сравнение на 100 транзакций: Enhanced Transactions (``/v0/addresses/{a}/transactions``) = 100 кредитов за запрос
    (legacy, режим поддержки); Parsed Events (``POST /v1/parsed-events/transaction-history``) = 10 за запрос,
    но его ответ - IDL-разбор инструкций, а нужное нам поле TradeEvent (виртуальные резервы после сделки)
    лежит во внутренней инструкции-событии, и документация не обещает его там; getSignaturesForAddress (1) +
    getTransaction (1 за каждую) = ~101 кредит. То есть getTransactionsForAddress = самый дешёвый путь,
    который отдаёт ``meta`` целиком (logMessages, innerInstructions, token balances) за 0.1 кредита на транзакцию.
  * ``sortOrder=asc`` + ``blockTime`` от создания токена до created_at+24ч: платим только за нужное окно,
    не листая всю свежую историю. Страницы: ``paginationToken`` ("slot:position"), конец - ``null``.
  * Адрес = mint (а не bonding curve): mint есть в аккаунтах и покупок/продаж на кривой, и сделок PumpSwap после
    миграции, так что один запрос покрывает всю жизнь токена. Лишние транзакции (переводы, создание ATA)
    отфильтровываются разбором; их доля мала.
  * Live: тот же вызов с ``since`` = время последней сделки; один вызов (10 кредитов) за опрос токена. Для
    многих токенов одновременно дешевле поток PumpPortal (бесплатно) + Helius только для докачки пропусков.
  * Запасной путь (если метода нет на free-плане - в документации это не указано): ``getSignaturesForAddress``
    (1 кредит, до 1000 подписей) + ``getTransaction`` (1 кредит) - тот же формат транзакции, тот же разбор.

Откуда берутся поля сделки
--------------------------
Программа Pump.fun (6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P) эмитит событие Anchor ``TradeEvent`` при каждой
покупке/продаже. Событие приходит одним из двух способов, поддержаны оба:
  1. self-CPI (``emit_cpi!``): внутренняя инструкция программы с data = base58(e445a52e51cb9a1d + discr + borsh);
  2. лог ``Program data: <base64(discr + borsh)>``.
Дискриминатор TradeEvent = sha256("event:TradeEvent")[:8] = bddb7fd34ee661ee. Поля (IDL pump-fun/pump-public-docs):
mint, sol_amount (лампорты, без комиссий), token_amount (6 знаков), is_buy, user, timestamp, virtual_sol_reserves,
virtual_token_reserves, real_sol_reserves, real_token_reserves (все - ПОСЛЕ сделки), далее комиссии и новые поля
(игнорируются: разбирается только стабильный префикс в 121 байт). Из них:
  side = buy|sell по is_buy; wallet = user; sol_amount/1e9; token_amount/1e6;
  price = vSol/vTokens (в SOL за токен); market_cap_sol = price * 1e9 (весь выпуск 1e9 токенов).
После миграции торги идут в PumpSwap (pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA): события ``BuyEvent`` /
``SellEvent``. В них нет mint, но есть адрес пула: событие относится к токену, только если пул владеет в этой
транзакции токен-счётом нашего mint и счётом WSOL (``owner`` в pre/postTokenBalances). Иначе это чужой пул из
маршрута (сделка-маршрут токен → другой токен → USDC → SOL содержит события всех пулов по пути) или пул, где токен
котируется не в SOL; цена по его резервам - не цена токена в SOL (19.10.2026: пул USDC/SOL дал токену капитализацию
9,8 млн SOL). Если ни одно событие не подошло, сделка берётся запасным разбором по балансам.
Если события нет вовсе (смена формата, сторонний роутер) - запасной разбор по изменению балансов подписанта
(source ``helius_balance``, без резервов, сумма SOL приблизительна).

Что настроить (добавить в config.py, нужен другой агент)
--------------------------------------------------------
  helius_api_key: SecretStr | None = None
  helius_base_url: str = "https://mainnet.helius-rpc.com"
  helius_rps: float = 8.0   (free: 10 RPC rps; оставляем запас)

Документация
------------
  * getTransactionsForAddress: https://www.helius.dev/docs/rpc/gettransactionsforaddress
  * Кредиты: https://www.helius.dev/docs/billing/credits
  * Лимиты (free: 10 RPC rps, 429 -> JSON-RPC код -32005, ретраи 1с..30с c джиттером):
    https://www.helius.dev/docs/billing/rate-limits
  * Parsed Events (преемник Enhanced Transactions):
    https://www.helius.dev/docs/parsed-events/guides/migrate-from-enhanced-transactions
  * IDL: https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump.json , .../idl/pump_amm.json
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import math
import random
import struct
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import SecretStr

# ───────────── Константы ─────────────

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
EVENT_PROGRAMS = frozenset({PUMP_PROGRAM, PUMP_AMM_PROGRAM})
# Anchor emit_cpi: первые 8 байт data внутренней инструкции-события (e445a52e51cb9a1d).
EVENT_IX_TAG = bytes.fromhex("e445a52e51cb9a1d")
TRADE_EVENT = bytes([189, 219, 127, 211, 78, 230, 97, 238])
AMM_BUY_EVENT = bytes([103, 244, 82, 31, 44, 245, 119, 119])
AMM_SELL_EVENT = bytes([62, 47, 55, 10, 165, 3, 220, 42])

LAMPORTS = Decimal(10**9)
TOKEN_DECIMALS = 6
TOKEN_UNIT = Decimal(10**TOKEN_DECIMALS)
TOTAL_SUPPLY = Decimal(10**9)  # у Pump.fun весь выпуск - 1 млрд токенов
WSOL_MINT = "So11111111111111111111111111111111111111112"
# В событиях PumpSwap резервы пула - до сделки (см. _amm_record). Сверено 19.10.2026 на реальной транзакции:
# pool_base_reserves = preTokenBalances токен-счёта пула.
AMM_EVENT_RESERVES_ARE_PRE_TRADE = True

# Оценка кредитов (документация Helius, раздел Credits). getTransactionsForAddress считается функцией gtfa_credits.
CREDIT_COST = {
    "getTransactionsForAddress": 10,  # за каждые 100 полных транзакций (вверх), минимум 10
    "getTransactionsForAddress:signatures": 10,  # плоско за вызов
    "getSignaturesForAddress": 1,
    "getTransaction": 1,
    "rpc": 1,  # любой другой стандартный RPC-метод
    "parsed_events": 10,  # за запрос (не используется модулем, для сравнения)
    "enhanced_transactions": 100,  # legacy (не используется модулем, для сравнения)
    "webhook_event": 1,
    "webhook_management": 100,
}


def gtfa_credits(n_items: int, details: str = "full") -> int:
    """Кредиты за один успешный вызов getTransactionsForAddress, вернувший n_items записей."""
    if details == "signatures":
        return CREDIT_COST["getTransactionsForAddress:signatures"]
    return max(1, math.ceil(n_items / 100)) * CREDIT_COST["getTransactionsForAddress"]


# ───────────── base58 ─────────────

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58)}


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        try:
            n = n * 58 + _B58_INDEX[ch]
        except KeyError:
            raise ValueError(f"invalid base58 character {ch!r}") from None
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + body


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\x00"))) + out


# ───────────── Типы ─────────────


@dataclass(frozen=True)
class TradeRecord:
    """Одна сделка по токену. Количества - в человеческих единицах (SOL, токены), не в лампортах."""

    signature: str
    mint: str
    ts: datetime
    slot: int | None
    tx_index: int | None
    wallet: str
    side: str  # buy | sell
    sol_amount: Decimal
    token_amount: Decimal
    program: str  # pump | pump-amm | balance-diff
    source: str  # helius | helius_balance
    virtual_sol: Decimal | None = None  # резервы ПОСЛЕ сделки (для pump-amm - реальные резервы пула)
    virtual_tokens: Decimal | None = None
    real_sol: Decimal | None = None  # реально лежащий в кривой/пуле SOL
    supply: Decimal = TOTAL_SUPPLY

    @property
    def price_sol(self) -> Decimal | None:
        return reserves_price(self.virtual_sol, self.virtual_tokens)

    @property
    def market_cap_sol(self) -> Decimal | None:
        price = self.price_sol
        return None if price is None else price * self.supply


def reserves_price(v_sol: Decimal | None, v_tokens: Decimal | None) -> Decimal | None:
    """Цена токена в SOL = SOL-резерв / токен-резерв (оба в человеческих единицах).

    Нулевой SOL-резерв — не нулевая цена, а отсутствие цены в SOL: так выглядят события кривых Pump.fun с котировкой
    в другом токене (``BuyV2``/``SellV2``, все SOL-поля TradeEvent = 0; сверено 20.10.2026). У обычной кривой
    виртуальный резерв не меньше 30 SOL, а в пустом пуле сделок не бывает.
    """
    if v_sol is None or v_tokens is None or v_tokens <= 0 or v_sol <= 0:
        return None
    return v_sol / v_tokens


@dataclass(frozen=True)
class TxPage:
    items: list[dict[str, Any]]
    pagination_token: str | None


# ───────────── Декодирование событий ─────────────

_TRADE_CORE = struct.Struct("<32sQQ?32sqQQQQ")  # 121 байт: стабильный префикс TradeEvent
_AMM_CORE = struct.Struct("<q" + "Q" * 13 + "32s32s")  # 176 байт: префикс BuyEvent/SellEvent


@dataclass(frozen=True)
class PumpTradeEvent:
    mint: str
    sol_amount: int
    token_amount: int
    is_buy: bool
    user: str
    timestamp: int
    virtual_sol_reserves: int
    virtual_token_reserves: int
    real_sol_reserves: int
    real_token_reserves: int


@dataclass(frozen=True)
class AmmTradeEvent:
    is_buy: bool
    timestamp: int
    base_amount: int  # токены: out при покупке, in при продаже
    quote_amount: int  # SOL: in (без комиссий) при покупке, out (брутто) при продаже
    quote_to_pool: int  # при покупке: quote_amount_in_with_lp_fee; при продаже: quote_amount_out_without_lp_fee
    pool_base_reserves: int
    pool_quote_reserves: int
    pool: str
    user: str


def decode_trade_event(payload: bytes) -> PumpTradeEvent | None:
    """payload = 8 байт дискриминатора + borsh-тело. None, если это не TradeEvent или данные обрезаны."""
    if payload[:8] != TRADE_EVENT or len(payload) < 8 + _TRADE_CORE.size:
        return None
    mint, sol, tok, is_buy, user, ts, vsol, vtok, rsol, rtok = _TRADE_CORE.unpack_from(payload, 8)
    return PumpTradeEvent(b58encode(mint), sol, tok, is_buy, b58encode(user), ts, vsol, vtok, rsol, rtok)


def decode_amm_event(payload: bytes) -> AmmTradeEvent | None:
    disc = payload[:8]
    if disc not in (AMM_BUY_EVENT, AMM_SELL_EVENT) or len(payload) < 8 + _AMM_CORE.size:
        return None
    (ts, base, _limit, _ub, _uq, pool_base, pool_quote, quote, _lpbps, _lp, _pbps, _pf, quote_x, _uqa,
     pool, user) = _AMM_CORE.unpack_from(payload, 8)
    is_buy = disc == AMM_BUY_EVENT
    # Покупка: поле 13 = quote_amount_in_with_lp_fee. Продажа: поле 13 = quote_amount_out_without_lp_fee.
    return AmmTradeEvent(is_buy, ts, base, quote, quote_x, pool_base, pool_quote, b58encode(pool), b58encode(user))


# ───────────── Извлечение событий из транзакции ─────────────


def _account_keys(tx: dict[str, Any]) -> list[str]:
    message = (tx.get("transaction") or {}).get("message") or {}
    raw = message.get("accountKeys") or []
    keys = [k.get("pubkey") if isinstance(k, dict) else k for k in raw]
    # json: адреса из lookup-таблиц лежат отдельно; jsonParsed уже включает их в accountKeys.
    if not any(isinstance(k, dict) for k in raw):
        loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
        keys += list(loaded.get("writable") or []) + list(loaded.get("readonly") or [])
    return [k for k in keys if isinstance(k, str)]


def event_payloads(tx: dict[str, Any]) -> list[bytes]:
    """Все события Anchor (discr + тело) от Pump-программ: внутренние инструкции, а при их отсутствии - логи."""
    meta = tx.get("meta") or {}
    keys = _account_keys(tx)
    found: list[bytes] = []
    for group in meta.get("innerInstructions") or []:
        for ins in (group or {}).get("instructions") or []:
            if not isinstance(ins, dict):
                continue
            pid = ins.get("programId")
            if pid is None and isinstance(ins.get("programIdIndex"), int) and ins["programIdIndex"] < len(keys):
                pid = keys[ins["programIdIndex"]]
            data = ins.get("data")
            if pid not in EVENT_PROGRAMS or not isinstance(data, str):
                continue
            try:
                raw = b58decode(data)
            except ValueError:
                continue
            if raw.startswith(EVENT_IX_TAG) and len(raw) > 16:
                found.append(raw[8:])
    if found:
        return found
    for line in meta.get("logMessages") or []:
        if isinstance(line, str) and line.startswith("Program data: "):
            try:
                found.append(base64.b64decode(line[14:], validate=True))
            except (binascii.Error, ValueError):
                continue
    return found


def _ts(tx: dict[str, Any], event_ts: int | None) -> datetime | None:
    value = tx.get("blockTime")
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        value = event_ts
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _signature(tx: dict[str, Any]) -> str | None:
    sigs = (tx.get("transaction") or {}).get("signatures")
    if isinstance(sigs, list) and sigs and isinstance(sigs[0], str):
        return sigs[0]
    sig = tx.get("signature")
    return sig if isinstance(sig, str) else None


def _int_or_none(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _pump_record(ev: PumpTradeEvent, sig: str, ts: datetime, tx: dict[str, Any]) -> TradeRecord:
    return TradeRecord(
        signature=sig, mint=ev.mint, ts=ts, slot=_int_or_none(tx.get("slot")),
        tx_index=_int_or_none(tx.get("transactionIndex")), wallet=ev.user,
        side="buy" if ev.is_buy else "sell",
        sol_amount=Decimal(ev.sol_amount) / LAMPORTS, token_amount=Decimal(ev.token_amount) / TOKEN_UNIT,
        program="pump", source="helius",
        virtual_sol=Decimal(ev.virtual_sol_reserves) / LAMPORTS,
        virtual_tokens=Decimal(ev.virtual_token_reserves) / TOKEN_UNIT,
        real_sol=Decimal(ev.real_sol_reserves) / LAMPORTS,
    )


def _amm_record(ev: AmmTradeEvent, mint: str, sig: str, ts: datetime, tx: dict[str, Any]) -> TradeRecord:
    base, quote = ev.pool_base_reserves, ev.pool_quote_reserves
    if AMM_EVENT_RESERVES_ARE_PRE_TRADE:
        if ev.is_buy:
            base, quote = base - ev.base_amount, quote + ev.quote_to_pool
        else:
            base, quote = base + ev.base_amount, quote - ev.quote_to_pool
    pool_sol = Decimal(max(quote, 0)) / LAMPORTS
    return TradeRecord(
        signature=sig, mint=mint, ts=ts, slot=_int_or_none(tx.get("slot")),
        tx_index=_int_or_none(tx.get("transactionIndex")), wallet=ev.user,
        side="buy" if ev.is_buy else "sell",
        sol_amount=Decimal(ev.quote_amount) / LAMPORTS, token_amount=Decimal(ev.base_amount) / TOKEN_UNIT,
        program="pump-amm", source="helius",
        virtual_sol=pool_sol, virtual_tokens=Decimal(max(base, 0)) / TOKEN_UNIT, real_sol=pool_sol,
    )


def _token_balances(meta: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for name in ("preTokenBalances", "postTokenBalances"):
        out += [b for b in (meta.get(name) or []) if isinstance(b, dict)]
    return out


def _owner_pre_amounts(meta: dict[str, Any]) -> dict[tuple[str, str], int | None] | None:
    """(владелец, mint) → остаток до транзакции (None, если счёт появился в ней) по всем токен-счетам транзакции.

    None, если в балансах нет поля ``owner`` (старый формат ответа): тогда пул проверить нечем.
    """
    out: dict[tuple[str, str], int | None] = {}
    has_owner = False
    for name in ("preTokenBalances", "postTokenBalances"):
        for b in meta.get(name) or []:
            if not isinstance(b, dict) or not isinstance(b.get("owner"), str) or not isinstance(b.get("mint"), str):
                continue
            has_owner = True
            key = (b["owner"], b["mint"])
            amount = None
            if name == "preTokenBalances":
                try:
                    amount = int((b.get("uiTokenAmount") or {}).get("amount"))
                except (TypeError, ValueError):
                    pass
            if out.get(key) is None:
                out[key] = amount
    return out if has_owner else None


def _is_own_sol_pool(ev: AmmTradeEvent, mint: str, owners: dict[tuple[str, str], int | None]) -> bool:
    """Пул события торгует нашим токеном против SOL: владеет счетами mint и WSOL, и mint - базовый токен пула."""
    if (ev.pool, mint) not in owners or (ev.pool, WSOL_MINT) not in owners:
        return False
    pre = owners[(ev.pool, mint)]
    # пул WSOL/токен (токен - котировка): остаток токена до сделки совпадает с резервом котировки, а не базы
    return not (pre is not None and pre == ev.pool_quote_reserves and pre != ev.pool_base_reserves)


def _balance_diff_record(tx: dict[str, Any], mint: str, sig: str, min_sol: Decimal) -> TradeRecord | None:
    """Запасной разбор: подписант (fee payer) получил/отдал токены и в обратную сторону сдвинулся его SOL."""
    meta = tx.get("meta") or {}
    keys = _account_keys(tx)
    pre, post = meta.get("preBalances"), meta.get("postBalances")
    ts = _ts(tx, None)
    if not keys or ts is None or not isinstance(pre, list) or not isinstance(post, list) or not pre or not post:
        return None
    signer = keys[0]

    def owned(name: str) -> int:
        total = 0
        for b in meta.get(name) or []:
            if isinstance(b, dict) and b.get("mint") == mint and b.get("owner") == signer:
                try:
                    total += int((b.get("uiTokenAmount") or {}).get("amount", 0))
                except (TypeError, ValueError):
                    pass
        return total

    token_delta = owned("postTokenBalances") - owned("preTokenBalances")
    try:
        sol_delta = Decimal(int(post[0]) - int(pre[0]) + int(meta.get("fee") or 0)) / LAMPORTS
    except (TypeError, ValueError):
        return None
    if token_delta == 0 or abs(sol_delta) < min_sol:
        return None
    if (token_delta > 0) == (sol_delta > 0):  # перевод или нерелевантная операция, не сделка
        return None
    return TradeRecord(
        signature=sig, mint=mint, ts=ts, slot=_int_or_none(tx.get("slot")),
        tx_index=_int_or_none(tx.get("transactionIndex")), wallet=signer,
        side="buy" if token_delta > 0 else "sell", sol_amount=abs(sol_delta),
        token_amount=Decimal(abs(token_delta)) / TOKEN_UNIT, program="balance-diff", source="helius_balance",
    )


def parse_transaction(tx: dict[str, Any], mint: str, *, allow_fallback: bool = True,
                      min_fallback_sol: Decimal = Decimal("0.001")) -> list[TradeRecord]:
    """Сделки токена ``mint`` в одной транзакции (формат ответа getTransaction / записи getTransactionsForAddress).

    Неудачные транзакции, обрезанные или неизвестные события дают пустой список, исключений не бывает.
    """
    if not isinstance(tx, dict):
        return []
    meta = tx.get("meta")
    if not isinstance(meta, dict) or meta.get("err") is not None:
        return []
    sig = _signature(tx)
    if sig is None:
        return []
    records: list[TradeRecord] = []
    seen_own_event = False  # событие именно нашего токена (кривая с нашим mint или наш пул к SOL)
    owners: dict[tuple[str, str], int | None] | None = None
    owners_loaded = False
    for payload in event_payloads(tx):
        pump_ev = decode_trade_event(payload)
        if pump_ev is not None:
            ts = _ts(tx, pump_ev.timestamp)
            if pump_ev.mint == mint:
                seen_own_event = True
                if ts is not None:
                    records.append(_pump_record(pump_ev, sig, ts, tx))
            continue
        amm_ev = decode_amm_event(payload)
        if amm_ev is None:
            continue
        if not owners_loaded:
            owners, owners_loaded = _owner_pre_amounts(meta), True
        if owners is not None:
            if not _is_own_sol_pool(amm_ev, mint, owners):
                continue  # чужой пул маршрута или пул не к SOL
        elif mint not in {b.get("mint") for b in _token_balances(meta)}:
            continue  # старый формат без owner: хотя бы mint должен участвовать в транзакции
        seen_own_event = True
        ts = _ts(tx, amm_ev.timestamp)
        if ts is not None:
            records.append(_amm_record(amm_ev, mint, sig, ts, tx))
    if not records and not seen_own_event and allow_fallback:
        rec = _balance_diff_record(tx, mint, sig, min_fallback_sol)
        if rec is not None:
            records.append(rec)
    return records


def merge_same_signature(records: Iterable[TradeRecord]) -> list[TradeRecord]:
    """PK trades = (signature, token_id, ts): несколько событий одной транзакции схлопываем.

    Одинаковые кошелёк+сторона суммируются (резервы - от последнего события); при разных кошельках/сторонах
    остаётся событие с наибольшей суммой SOL.
    """
    out: dict[str, TradeRecord] = {}
    for rec in records:
        cur = out.get(rec.signature)
        if cur is None:
            out[rec.signature] = rec
        elif cur.wallet == rec.wallet and cur.side == rec.side:
            out[rec.signature] = TradeRecord(
                **{**rec.__dict__, "sol_amount": cur.sol_amount + rec.sol_amount,
                   "token_amount": cur.token_amount + rec.token_amount})
        elif rec.sol_amount > cur.sol_amount:
            out[rec.signature] = rec
    return list(out.values())


def parse_transactions(txs: Iterable[dict[str, Any]], mint: str, **kwargs: Any) -> list[TradeRecord]:
    """Сделки токена из пачки транзакций, по возрастанию (время, слот, индекс), по одной записи на подпись."""
    records: list[TradeRecord] = []
    for tx in txs:
        records += parse_transaction(tx, mint, **kwargs)
    records = merge_same_signature(records)
    records.sort(key=lambda r: (r.ts, r.slot if r.slot is not None else 0, r.tx_index if r.tx_index is not None else 0))
    return records


# ───────────── HTTP-клиент ─────────────


class HeliusError(Exception):
    """Ошибка HTTP/JSON-RPC/транспорта; ключ API из сообщений вырезан."""

    def __init__(self, http_status: int | None, code: int | str | None, message: str, endpoint: str):
        self.http_status = http_status
        self.code = code
        self.message = message
        self.endpoint = endpoint
        super().__init__(f"Helius {http_status} {code if code is not None else ''}: {message} ({endpoint})")

    @property
    def is_unsupported(self) -> bool:
        """Метод недоступен на плане/эндпоинте (а не просто сбой) - повод уйти на запасной путь."""
        return self.http_status in (403, 404) or self.code in (-32601, -32403)


class _KeySwapTransport(httpx.AsyncBaseTransport):
    """В клиенте и логах httpx ключ виден как ``api-key=REDACTED``; настоящий подставляется только на отправке."""

    def __init__(self, inner: httpx.AsyncBaseTransport, key: str):
        self.inner = inner
        self.key = SecretStr(key)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        original = request.url
        request.url = original.copy_with(
            raw_path=original.raw_path.replace(b"api-key=REDACTED", b"api-key=" + quote(self.key.get_secret_value(), safe="").encode()))
        try:
            return await self.inner.handle_async_request(request)
        finally:
            request.url = original

    async def aclose(self) -> None:
        await self.inner.aclose()


def _to_unix(value: datetime | int | float | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())
    return int(value)


class HeliusClient:
    def __init__(self, api_key: str, base_url: str = "https://mainnet.helius-rpc.com", rps: float = 8.0,
                 timeout: float = 30.0, max_retries: int = 4,
                 transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 jitter: Callable[[], float] = random.random):
        if not api_key or rps <= 0 or max_retries < 0 or timeout <= 0:
            raise ValueError("A key, positive rps/timeout and nonnegative max_retries are required")
        self._key = SecretStr(api_key)
        self._clock, self._sleep, self._jitter = clock, sleep, jitter
        self._rps, self._max_retries = rps, max_retries
        self._window = max(1, int(rps))
        self._requests: deque[float] = deque()
        self._lock = asyncio.Lock()
        self.credits_spent = 0
        self.calls = 0
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/", timeout=timeout,
            headers={"content-type": "application/json", "accept": "application/json"},
            transport=_KeySwapTransport(transport if transport is not None else httpx.AsyncHTTPTransport(), api_key),
        )

    def __repr__(self) -> str:
        return f"HeliusClient(key=***, credits_spent={self.credits_spent})"

    @classmethod
    def from_settings(cls, settings: Any) -> HeliusClient:
        if getattr(settings, "helius_api_key", None) is None:
            raise ValueError("helius_api_key is required")
        return cls(settings.helius_api_key.get_secret_value(),
                   base_url=getattr(settings, "helius_base_url", "https://mainnet.helius-rpc.com"),
                   rps=getattr(settings, "helius_rps", 8.0))

    async def __aenter__(self) -> HeliusClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _redact(self, value: Any) -> str:
        key = self._key.get_secret_value()
        return str(value).replace(key, "***").replace(quote(key, safe=""), "***")

    async def _acquire(self) -> None:
        """Скользящее окно: не более rps запросов в любую секунду."""
        async with self._lock:
            while True:
                now = self._clock()
                while self._requests and now - self._requests[0] >= 1.0:
                    self._requests.popleft()
                if len(self._requests) < self._window:
                    self._requests.append(now)
                    return
                await self._sleep(max(0.0, 1.0 - (now - self._requests[0])))

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        # Рекомендация Helius: ~1 с, удвоение до 30 с, джиттер +-25%.
        delay = min(30.0, 2.0 ** attempt) * (0.75 + 0.5 * self._jitter())
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after:
            try:
                wait = float(retry_after)
            except ValueError:
                try:
                    wait = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    return delay
            delay = max(delay, wait)
        return delay

    async def rpc(self, method: str, params: list[Any], cost: int | Callable[[Any], int]) -> Any:
        """JSON-RPC вызов с лимитом, ретраями (429, 5xx, -32005, таймаут) и учётом кредитов успешных ответов."""
        endpoint = method
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        for attempt in range(self._max_retries + 1):
            last = attempt >= self._max_retries
            await self._acquire()
            self.calls += 1
            try:
                response = await self._client.post("", params={"api-key": "REDACTED"}, json=body)
            except httpx.RequestError as exc:
                if isinstance(exc, httpx.TimeoutException) and not last:
                    await self._sleep(self._retry_delay(None, attempt))
                    continue
                raise HeliusError(None, type(exc).__name__, "Transport request failed", endpoint) from None
            try:
                data = response.json()
            except ValueError:
                data = None
            rpc_error = data.get("error") if isinstance(data, dict) else None
            rate_limited = response.status_code == 429 or (isinstance(rpc_error, dict) and rpc_error.get("code") == -32005)
            if (rate_limited or response.status_code >= 500) and not last:
                await self._sleep(self._retry_delay(response, attempt))
                continue
            if not isinstance(data, dict):
                raise HeliusError(response.status_code, None, "Invalid JSON response", endpoint)
            if isinstance(rpc_error, dict):
                raise HeliusError(response.status_code, rpc_error.get("code"),
                                  self._redact(rpc_error.get("message", "RPC error"))[:300], endpoint)
            if not response.is_success:
                raise HeliusError(response.status_code, None, self._redact(response.text)[:300], endpoint)
            if "result" not in data:
                raise HeliusError(response.status_code, None, "Missing result", endpoint)
            result = data["result"]
            self.credits_spent += cost(result) if callable(cost) else cost
            return result
        raise AssertionError("unreachable")  # pragma: no cover

    async def get_transactions_for_address(
        self, address: str, *, since: datetime | int | None = None, until: datetime | int | None = None,
        sort_order: str = "asc", limit: int = 100, pagination_token: str | None = None,
        details: str = "full", status: str = "succeeded", encoding: str = "json",
        commitment: str = "finalized", token_accounts: str = "none",
        max_supported_transaction_version: int = 1,
    ) -> TxPage:
        """Страница истории адреса. Кредиты: 10 за каждые 100 полных транзакций (см. gtfa_credits).

        ``limit`` по умолчанию 100: документация говорит 1000 для обоих режимов, но блог Helius - 100 для full;
        100 безопасно, а стоимость за транзакцию от размера страницы не зависит.
        """
        if details not in ("full", "signatures") or sort_order not in ("asc", "desc") or not 1 <= limit <= 1000:
            raise ValueError("Invalid details, sort_order or limit")
        filters: dict[str, Any] = {"status": status, "tokenAccounts": token_accounts}
        block_time = {k: v for k, v in (("gte", _to_unix(since)), ("lte", _to_unix(until))) if v is not None}
        if block_time:
            filters["blockTime"] = block_time
        config: dict[str, Any] = {"transactionDetails": details, "sortOrder": sort_order, "limit": limit,
                                  "filters": filters, "commitment": commitment}
        if details == "full":
            config["encoding"] = encoding
            config["maxSupportedTransactionVersion"] = max_supported_transaction_version
        if pagination_token:
            config["paginationToken"] = pagination_token

        def cost(result: Any) -> int:
            items = result.get("data") if isinstance(result, dict) else None
            return gtfa_credits(len(items) if isinstance(items, list) else 0, details)

        result = await self.rpc("getTransactionsForAddress", [address, config], cost)
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise HeliusError(200, None, "Invalid getTransactionsForAddress result", "getTransactionsForAddress")
        token = result.get("paginationToken")
        return TxPage([x for x in result["data"] if isinstance(x, dict)], token if isinstance(token, str) and token else None)

    async def get_signatures_for_address(self, address: str, *, before: str | None = None, until: str | None = None,
                                         limit: int = 1000, commitment: str = "finalized") -> list[dict[str, Any]]:
        """Стандартный RPC (1 кредит): подписи от новых к старым, до 1000 за вызов."""
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        config: dict[str, Any] = {"limit": limit, "commitment": commitment}
        if before:
            config["before"] = before
        if until:
            config["until"] = until
        result = await self.rpc("getSignaturesForAddress", [address, config], CREDIT_COST["getSignaturesForAddress"])
        if not isinstance(result, list):
            raise HeliusError(200, None, "Invalid getSignaturesForAddress result", "getSignaturesForAddress")
        return [x for x in result if isinstance(x, dict)]

    async def get_transaction(self, signature: str, *, commitment: str = "finalized") -> dict[str, Any] | None:
        """Стандартный RPC (1 кредит). Результат совместим с parse_transaction; None, если транзакция не найдена."""
        config = {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": commitment}
        result = await self.rpc("getTransaction", [signature, config], CREDIT_COST["getTransaction"])
        return result if isinstance(result, dict) else None

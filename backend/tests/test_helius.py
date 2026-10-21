"""Тесты клиента Helius, разбора сделок Pump.fun и записи в БД (база kairos_test_helius, отдельная от общей)."""

import base64
import hashlib
import json
import logging
import os
import struct
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import httpx
import pytest

from dbutil import reset_schema
from kairos.collectors.helius_trades import TradeStore, backfill_token, snapshot_from_trade
from kairos.db import migrate
from kairos.sources import helius
from kairos.sources.helius import (
    AMM_BUY_EVENT, AMM_SELL_EVENT, EVENT_IX_TAG, PUMP_AMM_PROGRAM, PUMP_PROGRAM, TRADE_EVENT,
    HeliusClient, HeliusError, b58decode, b58encode, gtfa_credits, merge_same_signature,
    parse_transaction, parse_transactions,
)

TEST_DSN = os.environ.get("KAIROS_TEST_PG", "postgresql://kairos@localhost:5434") + "/kairos_test_helius"
KEY = "test-key-0123456789abcdef"
MINT = "DCWGcFBKFrKi23fcKBvPJZjnFyMnNVhoS7rXVwEypump"
OTHER_MINT = "HG6RzPPFgogKc9NHtnKSn8nn7MiWZ6yxm5kU8Vfhpump"
BUYER = "8XrSxN92F98HyWFmJD7ZeyFf8MaHAJxGeWsRnNLjrLU2"
SELLER = "7PtVk5TZ8J9ohTVS6r2kLzrY33JzShaS3RC6ycC1NNEG"
POOL = "zGEfU6iUtRoAqZtykHtQqZtUK8PVyzBhTJaipx3zJJS"
ATA = "C9dFvHsjs84XpZBMYUhsSy5PbW7DfMTuu1Z8ai5fmyPd"
SIG1 = "5RYnLTaifnF1KUvQ8gDA4HgSwNZTAXaCPGQ638qaXUTEa699MxGrGtRV3EFZEKk3N1eqHv4gneDsff3tzsrxuSnX"
T0 = 1_760_796_791  # 2025-10-18T14:13:11Z


# ───────────── Фикстуры транзакций ─────────────


def trade_event(mint, sol, tok, is_buy, user, ts, vsol, vtok, rsol, rtok):
    body = struct.pack("<32sQQ?32sqQQQQ", b58decode(mint), sol, tok, is_buy, b58decode(user), ts, vsol, vtok, rsol, rtok)
    return TRADE_EVENT + body + b"\x00" * 150  # хвост: комиссии и новые поля - разбор их игнорирует


def amm_event(is_buy, user, pool, ts, base, quote, quote_x, pool_base, pool_quote):
    head = struct.pack("<q" + "Q" * 13 + "32s32s", ts, base, 0, 0, 0, pool_base, pool_quote, quote, 20, 0, 5, 0, quote_x, 0,
                       b58decode(pool), b58decode(user))
    return (AMM_BUY_EVENT if is_buy else AMM_SELL_EVENT) + head + b"\x00" * 120


# реальные значения первой сделки токена (покупка разработчика 11.74 SOL) из ответа Helius
BUY = trade_event(MINT, 11_744_823_779, 301_886_432_217_185, True, BUYER, T0,
                  41_744_823_779, 771_113_567_782_815, 11_744_823_779, 571_113_567_782_815)
SELL = trade_event(MINT, 2_000_000_000, 40_000_000_000_000, False, SELLER, T0 + 30,
                   40_000_000_000, 780_000_000_000_000, 10_000_000_000, 580_000_000_000_000)


def make_tx(sig=SIG1, events=(), via="inner", block_time=T0, slot=458_463_836, idx=289, err=None, extra_keys=(),
            token_balances=(), pre_post=None, version="legacy", program=PUMP_PROGRAM):
    keys = [BUYER, MINT, program, *extra_keys]
    inner, logs = [], [f"Program {program} invoke [1]"]
    for ev in events:
        if via == "inner":
            inner.append({"programIdIndex": 2, "accounts": [], "data": b58encode(EVENT_IX_TAG + ev)})
        else:
            logs.append("Program data: " + base64.b64encode(ev).decode())
    logs.append(f"Program {program} success")
    pre, post = pre_post or ([10**10, 0, 1], [10**10, 0, 1])
    meta = {"err": err, "fee": 5000, "preBalances": pre, "postBalances": post,
            "innerInstructions": [{"index": 2, "instructions": inner}] if inner else [], "logMessages": logs,
            "preTokenBalances": [b for b in token_balances if b.get("_pre")] or [],
            "postTokenBalances": [b for b in token_balances if not b.get("_pre")], "loadedAddresses": {"writable": [], "readonly": []}}
    return {"slot": slot, "transactionIndex": idx, "blockTime": block_time, "version": version, "meta": meta,
            "transaction": {"signatures": [sig], "message": {"accountKeys": keys, "instructions": []}}}


# ───────────── Разбор ─────────────


def test_event_discriminators_match_anchor_hash():
    assert hashlib.sha256(b"event:TradeEvent").digest()[:8] == TRADE_EVENT
    assert hashlib.sha256(b"event:BuyEvent").digest()[:8] == AMM_BUY_EVENT
    assert hashlib.sha256(b"event:SellEvent").digest()[:8] == AMM_SELL_EVENT
    assert hashlib.sha256(b"anchor:event").digest()[:8][::-1] == EVENT_IX_TAG  # u64 little-endian


def test_base58_roundtrip_and_leading_zeros():
    raw = b"\x00\x00\x01\xff" + bytes(range(30))
    assert b58decode(b58encode(raw)) == raw
    assert b58encode(b58decode(MINT)) == MINT and len(b58decode(MINT)) == 32
    with pytest.raises(ValueError):
        b58decode("0OIl")


def test_parse_buy_from_inner_instruction_event():
    [rec] = parse_transaction(make_tx(events=[BUY]), MINT)
    assert (rec.side, rec.wallet, rec.signature, rec.program, rec.source) == ("buy", BUYER, SIG1, "pump", "helius")
    assert rec.sol_amount == Decimal("11.744823779")
    assert rec.token_amount == Decimal("301886432.217185")
    assert rec.ts == datetime(2025, 10, 18, 14, 13, 11, tzinfo=timezone.utc)
    assert (rec.slot, rec.tx_index) == (458_463_836, 289)
    assert rec.virtual_sol == Decimal("41.744823779") and rec.real_sol == Decimal("11.744823779")


def test_parse_sell_from_program_data_log():
    [rec] = parse_transaction(make_tx(events=[SELL], via="logs"), MINT)
    assert rec.side == "sell" and rec.wallet == SELLER
    assert rec.sol_amount == Decimal("2") and rec.token_amount == Decimal("40000000")
    assert rec.ts == datetime.fromtimestamp(T0, tz=timezone.utc)  # blockTime приоритетнее timestamp события


def test_event_timestamp_used_when_block_time_missing():
    tx = make_tx(events=[SELL])
    tx["blockTime"] = None
    [rec] = parse_transaction(tx, MINT)
    assert rec.ts == datetime.fromtimestamp(T0 + 30, tz=timezone.utc)


def test_snapshot_math_from_reserves():
    [rec] = parse_transaction(make_tx(events=[BUY]), MINT)
    snap = snapshot_from_trade(rec)
    price = Decimal("41.744823779") / Decimal("771113567.782815")
    assert snap.price_sol == price
    assert snap.market_cap_sol == price * Decimal(10**9) == rec.market_cap_sol
    assert snap.liquidity_sol == Decimal("11.744823779")  # реальный SOL в кривой, не виртуальный
    # старт кривой: 30 SOL / 1.073e9 токенов -> капитализация ~27.96 SOL
    assert helius.reserves_price(Decimal(30), Decimal("1073000000")) * helius.TOTAL_SUPPLY == pytest.approx(
        Decimal("27.958"), abs=Decimal("0.001"))
    assert helius.reserves_price(Decimal(1), Decimal(0)) is None and helius.reserves_price(None, Decimal(1)) is None


def test_other_mint_and_failed_tx_ignored():
    assert parse_transaction(make_tx(events=[BUY]), OTHER_MINT, allow_fallback=False) == []
    assert parse_transaction(make_tx(events=[BUY], err={"InstructionError": [2, "Custom"]}), MINT) == []


@pytest.mark.parametrize("tx", [
    None, {}, {"meta": None}, {"meta": {"err": None}}, {"meta": {"err": None}, "transaction": {"signatures": []}},
    {"meta": {"err": None, "logMessages": None, "innerInstructions": None}, "transaction": {"signatures": ["x"]}},
])
def test_odd_transactions_do_not_raise(tx):
    assert parse_transaction(tx, MINT) == []


def test_corrupt_and_truncated_events_are_skipped():
    tx = make_tx(events=[BUY[:60]], via="logs")  # обрезанное событие
    tx["meta"]["logMessages"] += ["Program data: !!!not-base64!!!", "Program data: AAAA", "Program log: hi"]
    assert parse_transaction(tx, MINT, allow_fallback=False) == []
    bad_inner = make_tx(events=[BUY])
    bad_inner["meta"]["innerInstructions"][0]["instructions"].append({"programIdIndex": 99, "data": "0OIl"})
    bad_inner["meta"]["innerInstructions"][0]["instructions"].append({"programIdIndex": 2, "data": 12345})
    assert len(parse_transaction(bad_inner, MINT)) == 1


def test_jsonparsed_and_lookup_table_keys():
    tx = make_tx(events=[BUY])
    # jsonParsed: accountKeys - объекты, у инструкций есть programId
    parsed = json.loads(json.dumps(tx))
    parsed["transaction"]["message"]["accountKeys"] = [{"pubkey": k, "signer": i == 0} for i, k in
                                                       enumerate(tx["transaction"]["message"]["accountKeys"])]
    parsed["meta"]["innerInstructions"][0]["instructions"][0] = {
        "programId": PUMP_PROGRAM, "accounts": [], "data": b58encode(EVENT_IX_TAG + BUY)}
    assert len(parse_transaction(parsed, MINT)) == 1
    # v0: программа лежит в адресах из lookup-таблицы, индекс за пределами статических ключей
    v0 = json.loads(json.dumps(tx))
    v0["transaction"]["message"]["accountKeys"] = [BUYER, MINT]
    v0["meta"]["loadedAddresses"] = {"writable": [], "readonly": [PUMP_PROGRAM]}
    assert len(parse_transaction(v0, MINT)) == 1


def pool_balances(pool=POOL, mint=MINT, base=900_000_000_000, quote_mint=helius.WSOL_MINT, quote=5_000_000_000):
    """Токен-счета пула в транзакции (до сделки): база = mint, котировка = WSOL."""
    return [{"_pre": True, "accountIndex": 4, "mint": mint, "owner": pool, "uiTokenAmount": {"amount": str(base)}},
            {"_pre": True, "accountIndex": 5, "mint": quote_mint, "owner": pool, "uiTokenAmount": {"amount": str(quote)}}]


def test_pump_amm_buy_and_sell_events():
    balances = [{"accountIndex": 3, "mint": MINT, "owner": BUYER, "uiTokenAmount": {"amount": "5"}}, *pool_balances()]
    buy = amm_event(True, BUYER, POOL, T0, base=1_000_000, quote=50_000_000, quote_x=50_100_000,
                    pool_base=900_000_000_000, pool_quote=5_000_000_000)
    tx = make_tx(events=[buy], program=PUMP_AMM_PROGRAM, extra_keys=[ATA], token_balances=balances)
    [rec] = parse_transaction(tx, MINT)
    assert (rec.program, rec.side, rec.wallet) == ("pump-amm", "buy", BUYER)
    assert rec.sol_amount == Decimal("0.05") and rec.token_amount == Decimal(1)
    # резервы события - до сделки: пересчитываются в состояние после покупки
    assert rec.virtual_tokens == Decimal("899999") and rec.real_sol == Decimal("5.0501")
    sell = amm_event(False, BUYER, POOL, T0, base=2_000_000, quote=90_000_000, quote_x=89_900_000,
                     pool_base=900_000_000_000, pool_quote=5_000_000_000)
    [srec] = parse_transaction(make_tx(events=[sell], program=PUMP_AMM_PROGRAM, extra_keys=[ATA], token_balances=balances), MINT)
    assert srec.side == "sell" and srec.real_sol == Decimal("4.9101")
    # токен-аккаунт чужого mint -> событие не относится к нашему токену
    foreign = [{"accountIndex": 3, "mint": OTHER_MINT, "owner": BUYER, "uiTokenAmount": {"amount": "5"}}]
    assert parse_transaction(make_tx(events=[buy], program=PUMP_AMM_PROGRAM, extra_keys=[ATA], token_balances=foreign),
                             MINT, allow_fallback=False) == []


OTHER_POOL = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def test_pump_amm_event_of_foreign_or_non_sol_pool_is_not_ours():
    user_b = {"accountIndex": 3, "mint": MINT, "owner": BUYER, "uiTokenAmount": {"amount": "5"}}
    buy = amm_event(True, BUYER, POOL, T0, base=1_000_000, quote=50_000_000, quote_x=50_100_000,
                    pool_base=900_000_000_000, pool_quote=5_000_000_000)
    # маршрут: событие пула USDC/SOL в транзакции, где участвует наш mint (пул не владеет нашим mint)
    foreign = amm_event(False, BUYER, OTHER_POOL, T0, base=14_073_838, quote=138_414_624, quote_x=138_068_587,
                        pool_base=2_206_686_531_999, pool_quote=21_702_716_546_638)
    balances = [user_b, *pool_balances(), *pool_balances(OTHER_POOL, USDC, 2_206_686_531_999, quote=21_702_716_546_638)]
    recs = parse_transaction(make_tx(events=[buy, foreign], program=PUMP_AMM_PROGRAM, extra_keys=[ATA],
                                     token_balances=balances), MINT)
    assert [r.real_sol for r in recs] == [Decimal("5.0501")]  # только событие своего пула
    # наш mint в пуле, но котировка - не WSOL: цены в SOL нет, событие не берётся
    non_sol = pool_balances(quote_mint=USDC)
    assert parse_transaction(make_tx(events=[buy], program=PUMP_AMM_PROGRAM, extra_keys=[ATA],
                                     token_balances=[user_b, *non_sol]), MINT, allow_fallback=False) == []
    # пул WSOL/токен: наш mint - котировка (его остаток совпадает с резервом котировки)
    rev = amm_event(True, BUYER, POOL, T0, base=1_000_000, quote=50_000_000, quote_x=50_100_000,
                    pool_base=7_000_000_000, pool_quote=900_000_000_000)
    assert parse_transaction(make_tx(events=[rev], program=PUMP_AMM_PROGRAM, extra_keys=[ATA],
                                     token_balances=[user_b, *pool_balances()]), MINT, allow_fallback=False) == []
    # старый формат балансов без owner: привязка по участию mint, как раньше
    no_owner = [{k: v for k, v in b.items() if k != "owner"} for b in [user_b]]
    assert len(parse_transaction(make_tx(events=[buy], program=PUMP_AMM_PROGRAM, extra_keys=[ATA],
                                         token_balances=no_owner), MINT)) == 1


def test_real_multihop_route_takes_balance_diff_not_foreign_pool_price():
    """Реальная транзакция 19.10.2026 (токен id 98 на сервере): продажа GoLd по маршруту GoLd → Xsv9 → USDC → SOL.

    Раньше сделке достались резервы пула USDC/SOL (21 702 SOL) и капитализация 9,8 млн SOL. Свой пул токена
    котируется в Xsv9, а не в SOL, поэтому событий «наш пул к SOL» нет: сделка берётся по балансам подписанта.
    """
    path = os.path.join(os.path.dirname(__file__), "data", "tx_multihop_amm.json")
    with open(path, encoding="utf-8") as f:
        tx = json.load(f)
    [rec] = parse_transaction(tx, "GoLdZWYNg9jg4z5SGCLJx69xag2vMjLaSQK5S7TUGoLd")
    assert (rec.program, rec.side, rec.wallet) == ("balance-diff", "sell", "6fKcWbqfLf9WLKog2bWZtqPkChhXWVZL7r4oCApPNFmS")
    assert rec.sol_amount == Decimal("0.135619386") and rec.token_amount == Decimal("87625.061212")
    assert rec.market_cap_sol is None and snapshot_from_trade(rec) is None
    # событие пула USDC/SOL относится к USDC (его база), а резервы до сделки совпадают с preTokenBalances пула
    [usdc] = parse_transaction(tx, USDC)
    assert usdc.program == "pump-amm" and usdc.real_sol == Decimal("21702.578478051")



def test_real_pump_curve_with_non_sol_quote_has_no_price():
    """Реальная транзакция (токен id 78 на сервере): CreateV2 + BuyV2 на кривой с котировкой A7bdiY…, не SOL.

    Все SOL-поля TradeEvent нулевые; раньше записывалась капитализация 0, и такие точки среди настоящих цен
    давали ложные обвалы. Теперь сделка остаётся (кошелёк, сторона, токены), но без цены и снимка.
    """
    path = os.path.join(os.path.dirname(__file__), "data", "tx_pump_non_sol_quote.json")
    with open(path, encoding="utf-8") as f:
        tx = json.load(f)
    [rec] = parse_transaction(tx, "58eo3pKwDZ2GKT2XvRumuns6RustWyvqtgfyubA3kYAJ")
    assert (rec.program, rec.side, rec.sol_amount) == ("pump", "buy", Decimal(0))
    assert rec.token_amount == Decimal("295796.052154")
    assert rec.price_sol is None and rec.market_cap_sol is None and snapshot_from_trade(rec) is None
    assert helius.reserves_price(Decimal(0), Decimal(1)) is None

def test_balance_diff_fallback_only_for_real_swaps():
    pre_b = {"_pre": True, "accountIndex": 3, "mint": MINT, "owner": BUYER, "uiTokenAmount": {"amount": "0"}}
    post_b = {"accountIndex": 3, "mint": MINT, "owner": BUYER, "uiTokenAmount": {"amount": "2500000"}}
    swap = make_tx(token_balances=[pre_b, post_b], extra_keys=[ATA], pre_post=([10**10, 0, 1], [9 * 10**9, 0, 1]))
    [rec] = parse_transaction(swap, MINT)
    assert (rec.side, rec.program, rec.source, rec.wallet) == ("buy", "balance-diff", "helius_balance", BUYER)
    assert rec.sol_amount == Decimal("1") - Decimal(5000) / helius.LAMPORTS and rec.token_amount == Decimal("2.5")
    assert rec.price_sol is None and snapshot_from_trade(rec) is None
    # перевод токенов без движения SOL - не сделка; отключается флагом
    transfer = make_tx(token_balances=[pre_b, post_b], extra_keys=[ATA], pre_post=([10**10, 0, 1], [10**10 - 5000, 0, 1]))
    assert parse_transaction(transfer, MINT) == []
    assert parse_transaction(swap, MINT, allow_fallback=False) == []


def test_merge_same_signature_sums_same_wallet_and_side():
    one = parse_transaction(make_tx(events=[BUY]), MINT)[0]
    two = parse_transaction(make_tx(events=[BUY, BUY]), MINT)
    assert len(two) == 2
    [merged] = merge_same_signature(two)
    assert merged.sol_amount == one.sol_amount * 2 and merged.token_amount == one.token_amount * 2
    [rec] = parse_transactions([make_tx(events=[BUY, SELL])], MINT)
    assert rec.side == "buy"  # разные кошельки/стороны: остаётся крупнейшее событие


def test_parse_transactions_sorted_and_deduped():
    a = make_tx(sig="s-late", events=[SELL], block_time=T0 + 30, idx=1)
    b = make_tx(sig="s-early", events=[BUY], block_time=T0, idx=2)
    recs = parse_transactions([a, b, b], MINT)
    assert [r.signature for r in recs] == ["s-early", "s-late"]


# ───────────── Клиент ─────────────


class FakeTime:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(handler, ft=None, **kw):
    ft = ft or FakeTime()
    client = HeliusClient(KEY, transport=httpx.MockTransport(handler), clock=ft.clock, sleep=ft.sleep,
                          jitter=lambda: 0.5, **kw)
    return client, ft


def rpc_ok(result):
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


async def test_key_is_masked_everywhere(caplog):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(401, json={"jsonrpc": "2.0", "error": {"code": -32401, "message": f"bad key {KEY}"}})

    client, _ = make_client(handler)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(HeliusError) as exc:
        await client.get_transactions_for_address(MINT)
    assert exc.value.http_status == 401 and not exc.value.is_unsupported
    assert KEY not in str(exc.value) and "***" in str(exc.value)
    assert KEY not in repr(client) and KEY not in caplog.text
    assert f"api-key={KEY}" in seen[0]  # на провод настоящий ключ уходит
    await client.aclose()


async def test_retries_on_429_5xx_and_rpc_rate_limit():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={})
        if len(calls) == 2:
            return httpx.Response(503, text="oops")
        if len(calls) == 3:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32005, "message": "Too many requests"}})
        return rpc_ok({"data": [{"slot": 1}], "paginationToken": None})

    client, ft = make_client(handler)
    page = await client.get_transactions_for_address(MINT)
    assert len(calls) == 4 and page.items == [{"slot": 1}] and page.pagination_token is None
    assert ft.sleeps[0] == 7 and ft.sleeps[1] == 2.0 and ft.sleeps[2] == 4.0  # Retry-After; затем 2^n при jitter 0.5
    assert client.credits_spent == 10  # неуспешные попытки не считаются
    await client.aclose()


async def test_retries_exhausted_and_other_errors_not_retried():
    n = []

    def always_429(request):
        n.append(1)
        return httpx.Response(429, json={})

    client, _ = make_client(always_429, max_retries=2)
    with pytest.raises(HeliusError) as exc:
        await client.get_transaction("sig")
    assert exc.value.http_status is None or exc.value.http_status == 429
    assert len(n) == 3 and client.credits_spent == 0
    await client.aclose()

    def rpc_error(request):
        n.append(1)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}})

    n.clear()
    client, _ = make_client(rpc_error)
    with pytest.raises(HeliusError) as exc:
        await client.get_transactions_for_address(MINT)
    assert len(n) == 1 and exc.value.code == -32601 and exc.value.is_unsupported
    await client.aclose()


async def test_timeout_is_retried_then_transport_error_sanitized():
    n = []

    def handler(request):
        n.append(1)
        if len(n) == 1:
            raise httpx.ReadTimeout(f"timeout for {request.url}")
        raise httpx.ConnectError(f"cannot connect {request.url}")

    client, _ = make_client(handler)
    with pytest.raises(HeliusError) as exc:
        await client.get_transaction("sig")
    assert len(n) == 2 and KEY not in str(exc.value) and exc.value.code == "ConnectError"
    await client.aclose()


async def test_rate_limit_window_spaces_requests():
    client, ft = make_client(lambda r: rpc_ok([]), rps=2)
    for _ in range(5):
        await client.get_signatures_for_address(MINT)
    assert len(ft.sleeps) >= 2 and ft.now >= 2.0  # 5 запросов при 2 rps - не быстрее двух секунд
    assert client.credits_spent == 5
    await client.aclose()


def test_credit_estimates():
    assert gtfa_credits(0) == 10 and gtfa_credits(100) == 10 and gtfa_credits(101) == 20 and gtfa_credits(1000) == 100
    assert gtfa_credits(900, "signatures") == 10
    assert helius.CREDIT_COST["getTransaction"] == 1 and helius.CREDIT_COST["enhanced_transactions"] == 100


async def test_gtfa_request_shape_and_credit_accounting():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return rpc_ok({"data": [{"slot": i} for i in range(150)], "paginationToken": "5:1"})

    client, _ = make_client(handler)
    since = datetime(2025, 10, 18, 14, 0, tzinfo=timezone.utc)
    page = await client.get_transactions_for_address(MINT, since=since, until=since + timedelta(hours=24),
                                                     pagination_token="4:2")
    body = bodies[0]
    assert body["method"] == "getTransactionsForAddress" and body["params"][0] == MINT
    cfg = body["params"][1]
    assert cfg["transactionDetails"] == "full" and cfg["sortOrder"] == "asc" and cfg["paginationToken"] == "4:2"
    assert cfg["filters"]["blockTime"] == {"gte": int(since.timestamp()), "lte": int(since.timestamp()) + 86400}
    assert cfg["filters"]["status"] == "succeeded" and cfg["maxSupportedTransactionVersion"] == 1
    assert page.pagination_token == "5:1" and client.credits_spent == 20  # 150 транзакций = 2 x 10
    with pytest.raises(ValueError):
        await client.get_transactions_for_address(MINT, limit=5000)
    await client.aclose()


def test_client_requires_key():
    with pytest.raises(ValueError):
        HeliusClient("")


# ───────────── БД ─────────────


@pytest.fixture
async def db_pool():
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await reset_schema(conn)
        await migrate(conn)
    finally:
        await conn.close()
    pool = await asyncpg.create_pool(TEST_DSN, min_size=1, max_size=4)
    yield pool
    await pool.close()


def paged_handler(pages, bodies):
    """pages: {токен-запроса: (items, следующий токен)}; None - первая страница."""

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        items, nxt = pages[body["params"][1].get("paginationToken")]
        return rpc_ok({"data": items, "paginationToken": nxt})

    return handler


def three_pages():
    t1 = make_tx(sig="sig-1", events=[BUY], block_time=T0, idx=1)
    t2 = make_tx(sig="sig-2", events=[SELL], block_time=T0 + 30, idx=2)
    t3 = make_tx(sig="sig-3", events=[BUY], block_time=T0 + 30, idx=3)  # та же секунда, что и sig-2
    noise = make_tx(sig="sig-n", events=[])
    return {None: ([t1, noise], "1:1"), "1:1": ([], "1:2"), "1:2": ([t2, t3], None)}


async def test_backfill_writes_trades_snapshots_and_is_idempotent(db_pool):
    store = TradeStore(db_pool)
    created = datetime.fromtimestamp(T0, tz=timezone.utc) + timedelta(minutes=2)
    token_id = await db_pool.fetchval(
        "INSERT INTO tokens (chain, mint, created_at, source) VALUES ('SOLANA', $1, $2, 'pumpportal') RETURNING id", MINT, created)
    until = created + timedelta(hours=24)
    bodies = []
    client, _ = make_client(paged_handler(three_pages(), bodies))
    res = await backfill_token(db_pool, client, MINT, until)
    assert (res.pages, res.trades, res.snapshots, res.stopped) == (3, 3, 2, "exhausted")
    # пустая страница с токеном не останавливает обход; since = created_at - 5 минут
    assert bodies[0]["params"][1]["filters"]["blockTime"]["gte"] == int(created.timestamp()) - 300
    assert bodies[0]["params"][1]["filters"]["blockTime"]["lte"] == int(until.timestamp())
    rows = await db_pool.fetch("SELECT * FROM trades ORDER BY ts, signature")
    assert [r["signature"] for r in rows] == ["sig-1", "sig-2", "sig-3"]
    assert rows[0]["side"] == "buy" and rows[0]["sol_amount"] == Decimal("11.744823779")
    assert rows[0]["wallet_address"] == BUYER and rows[0]["source"] == "helius" and rows[0]["token_id"] == token_id
    assert rows[0]["market_cap_sol"] == (Decimal("41.744823779") / Decimal("771113567.782815")) * Decimal(10**9)
    snaps = await db_pool.fetch("SELECT * FROM token_snapshots ORDER BY ts")
    assert len(snaps) == 2  # две сделки в одну секунду -> одна строка, побеждает последняя (sig-3 после sig-2)
    assert snaps[1]["liquidity_sol"] == Decimal("11.744823779") and snaps[1]["source"] == "helius"
    calls = await db_pool.fetch("SELECT * FROM api_calls WHERE provider = 'helius'")
    assert len(calls) == 3 and sum(c["credits"] for c in calls) == res.credits == 30 and all(c["ok"] for c in calls)

    # повторный прогон ничего не добавляет и не меняет
    client2, _ = make_client(paged_handler(three_pages(), []))
    res2 = await backfill_token(db_pool, client2, MINT, until)
    assert res2.trades == 0
    assert await db_pool.fetchval("SELECT count(*) FROM trades") == 3
    assert await db_pool.fetchval("SELECT count(*) FROM token_snapshots") == 2
    assert [dict(r) for r in await db_pool.fetch("SELECT * FROM token_snapshots ORDER BY ts")] == [dict(r) for r in snaps]
    assert await store.get_token(MINT) is not None


async def test_snapshot_does_not_overwrite_other_sources(db_pool):
    token_id = await db_pool.fetchval("INSERT INTO tokens (chain, mint, source) VALUES ('SOLANA', $1, 'pumpportal') RETURNING id", MINT)
    ts = datetime.fromtimestamp(T0, tz=timezone.utc)
    await db_pool.execute(
        "INSERT INTO token_snapshots (ts, token_id, market_cap_sol, liquidity_sol, source) VALUES ($1, $2, 99, 99, 'pumpportal')", ts, token_id)
    store = TradeStore(db_pool)
    new, snaps = await store.save_trades(token_id, parse_transactions([make_tx(events=[BUY])], MINT))
    assert new == 1 and snaps == 0
    assert await db_pool.fetchval("SELECT market_cap_sol FROM token_snapshots WHERE token_id = $1", token_id) == 99


async def test_unknown_token_is_created_and_migration_marked(db_pool):
    balances = [{"accountIndex": 3, "mint": MINT, "owner": BUYER, "uiTokenAmount": {"amount": "5"}}, *pool_balances()]
    buy = amm_event(True, BUYER, POOL, T0, 1_000_000, 50_000_000, 50_100_000, 900_000_000_000, 5_000_000_000)
    tx = make_tx(events=[buy], program=PUMP_AMM_PROGRAM, extra_keys=[ATA], token_balances=balances)
    client, _ = make_client(paged_handler({None: ([tx], None)}, []))
    res = await backfill_token(db_pool, client, MINT, datetime.fromtimestamp(T0 + 3600, tz=timezone.utc))
    assert res.trades == 1
    row = await db_pool.fetchrow("SELECT * FROM tokens WHERE mint = $1", MINT)
    assert row["source"] == "helius" and row["migration_pool"] == "pump-amm"
    assert row["migrated_at"] == datetime.fromtimestamp(T0, tz=timezone.utc)


async def test_pagination_stops_on_repeated_token_and_max_pages(db_pool):
    until = datetime.fromtimestamp(T0 + 3600, tz=timezone.utc)
    loop_pages = {None: ([make_tx(sig="a", events=[BUY])], "9:9"), "9:9": ([make_tx(sig="b", events=[SELL], block_time=T0 + 5)], "9:9")}
    client, _ = make_client(paged_handler(loop_pages, []))
    res = await backfill_token(db_pool, client, MINT, until)
    assert res.stopped == "stalled" and res.pages == 2 and res.trades == 2

    counter = iter(range(1000))

    def endless(request):
        n = next(counter)
        return rpc_ok({"data": [make_tx(sig=f"e{n}", events=[BUY], block_time=T0 + n)], "paginationToken": f"t{n}"})

    client, _ = make_client(endless)
    res = await backfill_token(db_pool, client, OTHER_MINT, until, max_pages=4)
    assert res.stopped == "max_pages" and res.pages == 4


async def test_http_error_is_logged_and_raised(db_pool):
    client, _ = make_client(lambda r: httpx.Response(401, json={"error": "unauthorized"}))
    with pytest.raises(HeliusError):
        await backfill_token(db_pool, client, MINT, datetime.fromtimestamp(T0, tz=timezone.utc))
    call = await db_pool.fetchrow("SELECT * FROM api_calls")
    assert call["ok"] is False and call["http_status"] == 401 and call["credits"] == 0


async def test_fallback_to_signatures_and_get_transaction(db_pool):
    txs = {"sig-1": make_tx(sig="sig-1", events=[BUY], block_time=T0), "sig-2": make_tx(sig="sig-2", events=[SELL], block_time=T0 + 30)}
    methods = []

    def handler(request):
        body = json.loads(request.content)
        methods.append(body["method"])
        if body["method"] == "getTransactionsForAddress":
            return httpx.Response(403, json={"error": "plan"})
        if body["method"] == "getSignaturesForAddress":
            return rpc_ok([{"signature": "sig-2", "blockTime": T0 + 30, "err": None},
                           {"signature": "sig-failed", "blockTime": T0 + 20, "err": {"x": 1}},
                           {"signature": "sig-1", "blockTime": T0, "err": None},
                           {"signature": "sig-old", "blockTime": T0 - 99999, "err": None}])
        return rpc_ok(txs[body["params"][0]])

    client, _ = make_client(handler)
    since = datetime.fromtimestamp(T0 - 60, tz=timezone.utc)
    res = await backfill_token(db_pool, client, MINT, datetime.fromtimestamp(T0 + 3600, tz=timezone.utc), since=since)
    assert res.used_fallback and res.trades == 2 and res.stopped == "fallback_exhausted"
    assert methods == ["getTransactionsForAddress", "getSignaturesForAddress", "getTransaction", "getTransaction"]
    assert res.credits == 3  # 1 страница подписей + 2 транзакции


async def test_backfill_reports_last_ts_last_token_and_pump_trades(db_pool):
    until = datetime.fromtimestamp(T0 + 3600, tz=timezone.utc)
    pages = {None: ([make_tx(sig="a", events=[BUY], block_time=T0)], "p1"),
             "p1": ([make_tx(sig="b", events=[SELL], block_time=T0 + 30), make_tx(sig="n", events=[])], "p2"),
             "p2": ([make_tx(sig="c", events=[BUY], block_time=T0 + 90)], None)}
    client, _ = make_client(paged_handler(pages, []))
    res = await backfill_token(db_pool, client, MINT, until, max_pages=2)
    assert res.stopped == "max_pages" and res.pump_trades == 2 and res.trades == 2
    assert res.last_ts == datetime.fromtimestamp(T0 + 30, tz=timezone.utc) and res.last_token == "p2"
    client, _ = make_client(paged_handler(pages, []))
    res = await backfill_token(db_pool, client, MINT, until, since=res.last_ts)
    assert res.stopped == "exhausted" and res.last_ts == datetime.fromtimestamp(T0 + 90, tz=timezone.utc)
    assert res.last_token is None


async def test_backfill_noise_only_has_no_last_ts_and_external_result_survives_error(db_pool):
    until = datetime.fromtimestamp(T0 + 3600, tz=timezone.utc)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return rpc_ok({"data": [make_tx(sig="a", events=[BUY], block_time=T0 + 5)], "paginationToken": "p1"})
        return httpx.Response(500, json={"error": "boom"})

    from kairos.collectors.helius_trades import BackfillResult
    client, _ = make_client(handler, max_retries=0)
    res = BackfillResult()
    with pytest.raises(HeliusError):
        await backfill_token(db_pool, client, MINT, until, result=res)
    assert res.last_ts == datetime.fromtimestamp(T0 + 5, tz=timezone.utc) and res.credits == 10 and res.pages == 1


async def test_fallback_reports_max_pages_when_signature_scan_is_cut(db_pool):
    methods = []

    def handler(request):
        body = json.loads(request.content)
        methods.append(body["method"])
        if body["method"] == "getTransactionsForAddress":
            return httpx.Response(403, json={"error": "plan"})
        n = len(methods)  # всегда полная страница свежих подписей: окно не достигнуто
        return rpc_ok([{"signature": f"s{n}-{i}", "blockTime": T0 + 100 - n, "err": None} for i in range(1000)])

    client, _ = make_client(handler)
    since = datetime.fromtimestamp(T0 - 99999, tz=timezone.utc)
    res = await backfill_token(db_pool, client, MINT, datetime.fromtimestamp(T0 + 3600, tz=timezone.utc),
                               since=since, max_pages=2)
    assert res.used_fallback and res.stopped == "max_pages" and res.pages == 2
    assert res.last_ts is None and res.trades == 0
    assert methods.count("getTransaction") == 0 and res.credits == 2


async def test_fallback_logs_get_transaction_credits_even_on_error(db_pool):
    txs = {"sig-1": make_tx(sig="sig-1", events=[BUY], block_time=T0)}

    def handler(request):
        body = json.loads(request.content)
        if body["method"] == "getTransactionsForAddress":
            return httpx.Response(403, json={"error": "plan"})
        if body["method"] == "getSignaturesForAddress":
            return rpc_ok([{"signature": "sig-2", "blockTime": T0 + 30, "err": None},
                           {"signature": "sig-1", "blockTime": T0, "err": None}])
        if body["params"][0] == "sig-2":
            return httpx.Response(500, json={"error": "boom"})
        return rpc_ok(txs[body["params"][0]])

    client, _ = make_client(handler, max_retries=0)
    with pytest.raises(HeliusError):
        await backfill_token(db_pool, client, MINT, datetime.fromtimestamp(T0 + 3600, tz=timezone.utc),
                             since=datetime.fromtimestamp(T0 - 60, tz=timezone.utc))
    rows = await db_pool.fetch("SELECT endpoint, credits, ok FROM api_calls WHERE endpoint = 'getTransaction'")
    assert [(r["credits"], r["ok"]) for r in rows] == [(1, False)]  # sig-1 оплачен, sig-2 - ошибка (бесплатна)
    assert await db_pool.fetchval("SELECT sum(credits) FROM api_calls") == client.credits_spent == 2


# ───────────── Живой тест ─────────────


@pytest.mark.live
async def test_live_gtfa_returns_parseable_pump_trades():
    key = os.environ.get("HELIUS_API_KEY")
    if not key:
        pytest.skip("HELIUS_API_KEY is not set")
    mint = os.environ.get("HELIUS_LIVE_MINT", MINT)
    async with HeliusClient(key) as client:
        page = await client.get_transactions_for_address(mint, limit=20, sort_order="asc")
        records = parse_transactions(page.items, mint)
        assert page.items and records
        assert all(r.side in ("buy", "sell") and r.sol_amount > 0 for r in records)
        assert all(r.price_sol is not None and r.price_sol > 0 for r in records if r.program == "pump")
        assert client.credits_spent == 10

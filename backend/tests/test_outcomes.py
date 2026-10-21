"""Разметка исхода токена (kairos.outcomes): чистое ядро v1 и слой БД.

БД-тесты используют отдельную базу kairos_test_outcomes (не общую kairos_test).
"""

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from dbutil import reset_schema
from kairos.db import migrate
from kairos.outcomes import (
    DEFAULT_HORIZONS, RULES_V1, Coverage, CreatorTrade, RulesV1, SeriesPoint, compute_due_outcomes,
    compute_token_outcomes, creator_sell_share, due_token_ids, label_outcome, liquidity_crash, reference_point,
)

OUTCOMES_TEST_DSN = os.environ.get(
    "KAIROS_TEST_OUTCOMES_DATABASE_URL", os.environ.get("KAIROS_TEST_PG", "postgresql://kairos@localhost:5434") + "/kairos_test_outcomes",
)

T0 = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
H1 = timedelta(hours=1)
H24 = timedelta(hours=24)
D7 = timedelta(days=7)


def m(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def pt(minutes: float, cap, liq=None) -> SeriesPoint:
    return SeriesPoint(m(minutes), cap, liq)


def lab(points, trades=(), *, horizon=H1, now=None, reference_time=T0, reference="creation", **kw):
    """label_outcome с горизонтом 1 час от T0, по умолчанию уже закончившимся."""
    return label_outcome(
        points, trades, reference=reference, reference_time=reference_time, horizon=horizon,
        now=now or reference_time + horizon, **kw,
    )


# ───────────── Точка отсчёта и данные ─────────────


def test_empty_series_is_no_data():
    res = lab([])
    assert res.label == "no_data"
    assert res.peak_multiple is None and res.end_multiple is None
    assert res.metrics["no_data_reason"] == "no_reference_point"
    assert res.rule_version == "v1"


def test_reference_without_prior_point_and_nothing_within_two_minutes_is_no_data():
    res = lab([pt(2.5, 10), pt(30, 30)])
    assert res.label == "no_data"
    assert res.metrics["no_data_reason"] == "no_reference_point"


def test_reference_without_prior_point_uses_first_point_within_two_minutes():
    # Точка ровно через 2 минуты принимается (≤), становится ref; дальше ×3 без ликвидности → neutral.
    res = lab([pt(2, 10), pt(30, 30), pt(60, 30)])
    assert res.label == "neutral"
    assert res.metrics["ref_lookahead"] is True
    assert res.metrics["ref_cap"] == 10.0
    assert res.peak_multiple == Decimal(3)
    # На секунду позже — уже нет.
    assert reference_point([SeriesPoint(m(2) + timedelta(seconds=1), 10)], T0) is None


def test_reference_between_points_takes_last_known_before():
    pts = [pt(-10, 8), pt(-1, 10), pt(5, 15), pt(60, 20)]
    assert reference_point(pts, T0) == pt(-1, 10)
    res = lab(pts)
    assert res.metrics["ref_cap"] == 10.0 and res.metrics["ref_lookahead"] is False
    assert res.peak_multiple == Decimal(2)
    assert res.end_multiple == Decimal(2)


def test_point_exactly_at_reference_is_the_reference():
    assert reference_point([pt(-5, 7), pt(0, 10), pt(1, 11)], T0) == pt(0, 10)


def test_only_reference_point_without_later_data_is_no_data():
    res = lab([pt(0, 10)])
    assert res.label == "no_data"
    assert res.metrics["no_data_reason"] == "no_cap_points_on_horizon"
    # Данные есть, но только после конца горизонта — для этого горизонта их нет.
    assert lab([pt(0, 10), pt(61, 30)]).label == "no_data"


def test_zero_reference_cap_is_no_data():
    res = lab([pt(0, 0), pt(10, 5)])
    assert res.label == "no_data"
    assert res.metrics["no_data_reason"] == "reference_cap_not_positive"


def test_horizon_not_finished_is_pending_even_with_rug_like_data():
    pts = [pt(0, 10, 100), pt(5, 1, 1)]
    res = lab(pts, now=m(59))
    assert res.label == "pending"
    # Промежуточные величины уже посчитаны и сохраняются.
    assert res.end_multiple == Decimal("0.1")
    assert res.metrics["liquidity_crash"] is not None
    assert lab(pts, now=m(60)).label == "rug"


def test_unfinished_horizon_without_data_is_pending_not_no_data():
    assert lab([], now=m(30)).label == "pending"
    assert lab([pt(0, 10)], now=m(30)).label == "pending"
    assert lab([pt(0, 10)], now=m(60)).label == "no_data"


# ───────────── success ─────────────


def test_success_exactly_x3_with_alive_liquidity():
    res = lab([pt(0, 10, 20), pt(30, 30, 40), pt(60, 10, 30)])
    assert res.label == "success"
    assert res.peak_multiple == Decimal(3)
    assert res.end_multiple == Decimal(1)
    assert res.liquidity_alive is True


def test_peak_just_below_x3_is_not_success():
    res = lab([pt(0, 10, 20), pt(30, Decimal("29.99"), 40), pt(60, 10, 30)])
    assert res.label == "neutral"


def test_success_requires_end_multiple_at_least_one():
    res = lab([pt(0, 10, 20), pt(30, 40, 40), pt(60, Decimal("9.99"), 30)])
    assert res.label == "neutral"
    assert res.peak_multiple == Decimal(4)


def test_liquidity_alive_exactly_half_of_peak():
    alive = lab([pt(0, 10, 20), pt(30, 30, 40), pt(60, 10, 20)])
    assert alive.liquidity_alive is True and alive.label == "success"
    below = lab([pt(0, 10, 20), pt(30, 30, 40), pt(60, 10, Decimal("19.99"))])
    assert below.liquidity_alive is False and below.label == "neutral"
    assert below.metrics["peak_liq"] == 40.0 and below.metrics["end_liq"] == 19.99


def test_liquidity_alive_exactly_five_sol():
    alive = lab([pt(0, 10, 4), pt(30, 30, 6), pt(60, 10, 5)])
    assert alive.liquidity_alive is True and alive.label == "success"
    below = lab([pt(0, 10, 4), pt(30, 30, 6), pt(60, 10, Decimal("4.99"))])
    assert below.liquidity_alive is False and below.label == "neutral"


def test_unknown_liquidity_blocks_success():
    # Только сделки (без ликвидности): ×3 и end ≥ 1, но liquidity_alive неизвестен → neutral.
    res = lab([pt(0, 10), pt(30, 30), pt(60, 12)])
    assert res.label == "neutral"
    assert res.liquidity_alive is None
    assert res.metrics["liquidity_unknown"] is True


# ───────────── rug: ликвидность ─────────────


def test_liquidity_drop_within_exactly_ten_minutes_is_rug_but_eleven_is_not():
    rug = lab([pt(0, 10, 100), pt(10, 10, 19), pt(60, 10, 19)])
    assert rug.label == "rug"
    assert rug.metrics["rug_reasons"] == ["liquidity_crash"]
    assert rug.metrics["liquidity_crash"]["seconds"] == 600.0
    slow = lab([pt(0, 10, 100), pt(11, 10, 19), pt(60, 10, 19)])
    assert slow.label != "rug"
    assert slow.metrics["liquidity_crash"] is None


def test_liquidity_drop_of_exactly_eighty_percent_is_not_rug():
    assert liquidity_crash([pt(0, 10, 100), pt(5, 10, 20)]) is None
    crash = liquidity_crash([pt(0, 10, 100), pt(5, 10, Decimal("19.99"))])
    assert crash is not None and crash["drop"] > Decimal("0.8")


def test_liquidity_crash_on_irregular_series_uses_any_pair_in_window():
    # Постепенные шаги по 50 % за 5 минут: ни одна пара в 10-минутном окне не даёт > 80 %...
    pts = [pt(0, 10, 100), pt(5, 10, 50), pt(10, 10, 25), pt(15, 10, Decimal("12.5"))]
    assert liquidity_crash(pts) is None
    # ...а пара (0 → 9 мин) при промежуточной точке даёт.
    pts = [pt(0, 10, 100), pt(4, 10, 60), pt(9, 10, 15)]
    crash = liquidity_crash(pts)
    assert crash["from_ts"] == m(0) and crash["to_ts"] == m(9)


def test_rug_beats_success():
    # Ликвидность рухнула и восстановилась, цена ×3 и жива — всё равно rug.
    res = lab([pt(0, 10, 100), pt(5, 30, 10), pt(30, 40, 120), pt(60, 35, 110)])
    assert res.label == "rug"
    assert res.peak_multiple == Decimal(4) and res.liquidity_alive is True


# ───────────── rug: креатор ─────────────


def test_creator_sold_exactly_half_is_not_rug_but_more_is():
    pts = [pt(0, 10, 50), pt(30, 12, 55), pt(60, 11, 52)]
    buy = CreatorTrade(m(0), "buy", 1000, 2)
    half = lab(pts, [buy, CreatorTrade(m(20), "sell", 500, 1)])
    assert half.label == "neutral"
    assert half.metrics["creator"]["share"] == 0.5
    more = lab(pts, [buy, CreatorTrade(m(20), "sell", 501, 1)])
    assert more.label == "rug"
    assert more.metrics["rug_reasons"] == ["creator_sold"]


def test_creator_sell_after_first_hour_is_not_rug():
    pts = [pt(0, 10, 50), pt(30, 12, 55), pt(24 * 60, 11, 52)]
    trades = [CreatorTrade(m(0), "buy", 1000), CreatorTrade(m(61), "sell", 900)]
    res = lab(pts, trades, horizon=H24)
    assert res.label == "neutral"
    assert res.metrics["creator"]["sold_in_window"] == 0.0
    # Продажа ровно в конце первого часа — внутри окна.
    res = lab(pts, [CreatorTrade(m(0), "buy", 1000), CreatorTrade(m(60), "sell", 900)], horizon=H24)
    assert res.label == "rug"


def test_creator_first_hour_counts_from_reference_for_first_mention():
    # Шилл через 5 часов после создания; креатор сливает через 30 минут после шилла.
    created = T0 - timedelta(hours=5)
    pts = [SeriesPoint(created, 10, 50), pt(0, 10, 50), pt(30, 9, 48), pt(60, 9, 48)]
    trades = [CreatorTrade(created, "buy", 1000), CreatorTrade(m(30), "sell", 600)]
    res = lab(pts, trades, reference="first_mention")
    assert res.label == "rug"
    assert res.metrics["creator"]["held_at_start"] == 1000.0
    assert res.metrics["creator"]["window_start"] == T0.isoformat()


def test_creator_base_from_initial_tokens_and_window_buys():
    # Начальная покупка не в сделках, передана отдельно; докупка в окне входит в базу.
    s = creator_sell_share(
        [CreatorTrade(m(10), "buy", 200), CreatorTrade(m(20), "sell", 600)], T0, m(60), initial_tokens=Decimal(1000),
    )
    assert s["base"] == Decimal(1200) and s["share"] == Decimal(600) / Decimal(1200)
    assert s["base_unknown"] is False


def test_creator_unknown_base_does_not_fire():
    # Продажи превышают известные покупки: начальная покупка не записана → база неизвестна, rug не ставится.
    pts = [pt(0, 10, 50), pt(30, 12, 55), pt(60, 11, 52)]
    res = lab(pts, [CreatorTrade(m(20), "sell", 900)])
    assert res.label == "neutral"
    assert res.metrics["creator"]["base_unknown"] is True
    assert res.metrics["creator"]["share"] is None
    # Нет сделок вообще — база нулевая, доля неизвестна.
    assert creator_sell_share([], T0, m(60))["share"] is None


# ───────────── rug: обвал цены; dead; neutral; migrated ─────────────


def test_collapse_rug_requires_end_below_tenth_and_drawdown_above_ninety():
    res = lab([pt(0, 10, 50), pt(10, 20, 60), pt(60, Decimal("0.99"), 40)])
    assert res.label == "rug"
    assert res.metrics["rug_reasons"] == ["collapse"]
    assert res.max_drawdown == 1 - Decimal("0.99") / Decimal(20)
    # end ровно 0.1 — не rug, а dead.
    edge = lab([pt(0, 10, 50), pt(10, 20, 60), pt(60, 1, 40)])
    assert edge.label == "dead"
    assert edge.end_multiple == Decimal("0.1")
    # end < 0.1, но просадка ровно 0.9 (пик = ref = 10, минимум 1, end 0.9) — не rug.
    edge2 = lab([pt(0, 10, 50), pt(10, 1, 40), pt(60, Decimal("0.9"), 40)])
    assert edge2.max_drawdown == Decimal("0.91") and edge2.label == "rug"
    edge3 = lab([pt(0, 10, 50), pt(10, 1, 40), pt(60, 1, 40)])
    assert edge3.max_drawdown == Decimal("0.9") and edge3.label == "dead"


def test_dead_boundary_at_half():
    assert lab([pt(0, 10, 50), pt(60, Decimal("4.99"), 40)]).label == "dead"
    assert lab([pt(0, 10, 50), pt(60, 5, 40)]).label == "neutral"


def test_migrated_token_is_not_dead():
    pts = [pt(0, 10, 50), pt(60, 3, 40)]
    res = lab(pts, migrated_at=m(45))
    assert res.label == "neutral" and res.migrated is True
    late = lab(pts, migrated_at=m(61))
    assert late.label == "dead" and late.migrated is False
    exact = lab(pts, migrated_at=m(60))
    assert exact.migrated is True


def test_max_drawdown_from_first_peak_and_zero_when_peak_is_last():
    res = lab([pt(0, 10, 50), pt(10, 40, 60), pt(20, 10, 55), pt(30, 40, 60), pt(60, 20, 55)])
    assert res.max_drawdown == Decimal("0.75")
    assert res.metrics["peak_ts"] == m(10).isoformat()
    flat = lab([pt(0, 10, 50), pt(60, 20, 55)])
    assert flat.max_drawdown == Decimal(0)
    assert flat.peak_multiple == flat.end_multiple == Decimal(2)


def test_end_value_is_carried_forward_with_staleness():
    res = lab([pt(0, 10, 50), pt(20, 12, 55)], horizon=H24)
    assert res.end_multiple == Decimal("1.2")
    assert res.metrics["end_staleness_s"] == (H24 - timedelta(minutes=20)).total_seconds()
    assert res.metrics["horizon_end"] == (T0 + H24).isoformat()


def test_custom_rules_change_thresholds():
    rules = RulesV1(rule_version="v1-test", success_peak_multiple=Decimal(2))
    pts = [pt(0, 10, 20), pt(30, 20, 40), pt(60, 10, 30)]
    assert lab(pts).label == "neutral"
    res = lab(pts, rules=rules)
    assert res.label == "success" and res.rule_version == "v1-test"
    assert RULES_V1.rule_version == "v1"


def test_series_order_does_not_matter():
    pts = [pt(60, 10, 30), pt(0, 10, 20), pt(30, 30, 40)]
    assert lab(pts).label == "success"


# ───────────── Полнота данных (coverage) ─────────────

SUCCESS_PTS = [pt(0, 10, 20), pt(30, 30, 40), pt(60, 10, 30)]  # без coverage → success


def test_without_coverage_metrics_have_no_coverage_key():
    res = lab(SUCCESS_PTS)
    assert res.label == "success"
    assert "coverage" not in res.metrics and "pending_reason" not in res.metrics


def test_synced_null_is_pending_not_final_even_with_full_data():
    res = lab(SUCCESS_PTS, coverage=Coverage(None))
    assert res.label == "pending"
    assert res.metrics["coverage"] == {"synced_until": None, "complete": False, "truncated": False}
    assert res.metrics["pending_reason"] == "data_incomplete"
    # Промежуточные величины всё равно посчитаны по имеющимся данным.
    assert res.peak_multiple == Decimal(3) and res.liquidity_alive is True


def test_synced_before_horizon_end_is_pending_not_no_data():
    cov = Coverage(m(59))
    empty = lab([], coverage=cov)
    assert empty.label == "pending"
    assert empty.metrics["no_data_reason"] == "no_reference_point"
    assert empty.metrics["coverage"]["complete"] is False
    assert empty.metrics["coverage"]["synced_until"] == m(59).isoformat()
    only_ref = lab([pt(0, 10)], coverage=cov)
    assert only_ref.label == "pending" and only_ref.metrics["no_data_reason"] == "no_cap_points_on_horizon"
    assert lab(SUCCESS_PTS, coverage=cov).label == "pending"
    # Данные полны на дальнем горизонте, но сам горизонт не закончился — причина другая.
    early = lab(SUCCESS_PTS, now=m(30), coverage=Coverage(m(120)))
    assert early.label == "pending" and early.metrics["pending_reason"] == "horizon_not_finished"


def test_synced_exactly_at_horizon_end_counts_as_complete():
    res = lab(SUCCESS_PTS, coverage=Coverage(m(60)))
    assert res.label == "success"
    assert res.metrics["coverage"] == {"synced_until": m(60).isoformat(), "complete": True, "truncated": False}
    assert lab([], coverage=Coverage(m(60))).label == "no_data"
    assert lab(SUCCESS_PTS, coverage=Coverage(m(60) - timedelta(seconds=1))).label == "pending"


def test_synced_after_horizon_end_behaves_as_without_coverage():
    cov = Coverage(m(24 * 60))
    plain = lab(SUCCESS_PTS)
    full = lab(SUCCESS_PTS, coverage=cov)
    assert (full.label, full.peak_multiple, full.end_multiple, full.liquidity_alive) == \
        (plain.label, plain.peak_multiple, plain.end_multiple, plain.liquidity_alive)
    assert lab([pt(0, 10, 100), pt(5, 1, 1)], coverage=cov).label == "rug"
    assert lab([pt(0, 10, 50), pt(60, 4, 40)], coverage=cov).label == "dead"
    assert lab([], coverage=cov).label == "no_data"


def test_truncated_backfill_is_complete_but_flagged():
    res = lab(SUCCESS_PTS, coverage=Coverage(m(60), truncated=True))
    assert res.label == "success"
    assert res.metrics["coverage"]["complete"] is True and res.metrics["coverage"]["truncated"] is True
    waiting = lab(SUCCESS_PTS, coverage=Coverage(m(30), truncated=True))
    assert waiting.label == "pending"
    assert waiting.metrics["coverage"] == {"synced_until": m(30).isoformat(), "complete": False, "truncated": True}
    # Полнота — по synced_until, не по флагу.
    assert Coverage(m(60), truncated=True).complete_for(m(60)) is True
    assert Coverage(None, truncated=True).complete_for(m(60)) is False


# ───────────── Слой БД ─────────────


@pytest.fixture
async def outcomes_pool():
    """Чистая схема в отдельной базе kairos_test_outcomes на каждый тест."""
    conn = await asyncpg.connect(OUTCOMES_TEST_DSN)
    try:
        await reset_schema(conn)
        await migrate(conn)
    finally:
        await conn.close()
    pool = await asyncpg.create_pool(OUTCOMES_TEST_DSN, min_size=1, max_size=4)
    yield pool
    await pool.close()


SYNCED_FAR = T0 + timedelta(days=30)  # сделки загружены далеко за все горизонты: данные полны


async def insert_token(
    pool, mint: str, created_at, creator="creator-wallet", migrated_at=None, initial_cap=None,
    synced_until=SYNCED_FAR, truncated=False,
) -> int:
    """Токен с загруженными сделками (``trades_synced_until`` по умолчанию далеко в будущем — все горизонты полны).
    ``synced_until=None`` — сделки не загружались."""
    wallet_id = await pool.fetchval(
        """INSERT INTO wallets (chain, address) VALUES ('SOLANA', $1)
           ON CONFLICT (chain, address) DO UPDATE SET address = EXCLUDED.address RETURNING id""",
        creator,
    )
    return await pool.fetchval(
        """INSERT INTO tokens (mint, creator_wallet_id, created_at, migrated_at, initial_market_cap_sol, source,
                               trades_synced_until, trades_backfill_truncated)
           VALUES ($1, $2, $3, $4, $5, 'test', $6, $7) RETURNING id""",
        mint, wallet_id, created_at, migrated_at, initial_cap, synced_until, truncated,
    )


async def set_synced(pool, token_id: int, synced_until, truncated=None):
    await pool.execute("UPDATE tokens SET trades_synced_until = $2 WHERE id = $1", token_id, synced_until)
    if truncated is not None:
        await pool.execute("UPDATE tokens SET trades_backfill_truncated = $2 WHERE id = $1", token_id, truncated)


async def insert_snapshots(pool, token_id: int, rows):
    await pool.executemany(
        "INSERT INTO token_snapshots (ts, token_id, market_cap_sol, liquidity_sol, source) VALUES ($1, $2, $3, $4, 'test')",
        [(ts, token_id, _d(cap), _d(liq)) for ts, cap, liq in rows],
    )


async def insert_trade(pool, token_id: int, ts, wallet: str, side: str, tokens, sol, cap=None, sig=None):
    await pool.execute(
        """INSERT INTO trades (ts, signature, token_id, wallet_address, side, sol_amount, token_amount, market_cap_sol, source)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'test')""",
        ts, sig or f"sig-{token_id}-{ts.timestamp()}-{wallet}", token_id, wallet, side, _d(sol), _d(tokens), _d(cap),
    )


async def insert_mention(pool, token_id: int, tweet_time, tweet_id: str):
    kol_id = await pool.fetchval("INSERT INTO kols (username) VALUES ('shiller') RETURNING id")
    await pool.execute(
        """INSERT INTO mentions (kol_id, chain, ca, token_id, tweet_id, tweet_time, source)
           VALUES ($1, 'SOLANA', 'ca', $2, $3, $4, 'test')""",
        kol_id, token_id, tweet_id, tweet_time,
    )


def _d(v):
    return None if v is None else Decimal(str(v))


async def outcome_rows(pool, token_id: int):
    rows = await pool.fetch(
        "SELECT * FROM token_outcomes WHERE token_id = $1 ORDER BY reference, horizon", token_id,
    )
    return {(r["reference"], r["horizon"]): r for r in rows}


async def test_compute_token_outcomes_both_references(outcomes_pool):
    pool = outcomes_pool
    created = T0
    shill = T0 + timedelta(minutes=30)
    token_id = await insert_token(pool, "mint-a", created, migrated_at=T0 + timedelta(hours=2))
    await insert_snapshots(pool, token_id, [
        (created, 10, 30), (shill, 20, 40), (T0 + timedelta(minutes=50), 60, 80),
        (T0 + timedelta(minutes=60), 60, 80), (T0 + timedelta(minutes=90), 65, 90),
        (T0 + timedelta(hours=23), 30, 70), (T0 + timedelta(days=6), 3, 40),
    ])
    await insert_mention(pool, token_id, shill, "t1")
    await insert_mention(pool, token_id, shill + timedelta(minutes=5), "t2")  # не первое упоминание

    now = T0 + timedelta(days=8)
    results = await compute_token_outcomes(pool, token_id, now=now)
    assert len(results) == 6
    rows = await outcome_rows(pool, token_id)
    assert set(rows) == {(ref, h) for ref in ("creation", "first_mention") for h in DEFAULT_HORIZONS}

    c1 = rows[("creation", H1)]
    assert c1["label"] == "success"
    assert c1["reference_time"] == created
    assert c1["peak_multiple"] == Decimal(6) and c1["end_multiple"] == Decimal(6)
    assert c1["liquidity_alive"] is True and c1["migrated"] is False
    assert c1["rule_version"] == "v1" and c1["computed_at"] == now
    metrics = json.loads(c1["metrics"])
    assert metrics["ref_cap"] == 10.0 and metrics["rug_reasons"] == []

    f1 = rows[("first_mention", H1)]
    assert f1["reference_time"] == shill
    assert f1["peak_multiple"] == Decimal("3.25")  # 65 / 20 на отрезке 30..90 мин
    assert f1["label"] == "success"

    c24 = rows[("creation", H24)]
    assert c24["label"] == "success" and c24["migrated"] is True
    assert c24["end_multiple"] == Decimal(3)

    c7 = rows[("creation", D7)]
    assert c7["label"] == "neutral"  # end 0.3 < 0.5, но мигрировал → не dead
    assert c7["end_multiple"] == Decimal("0.3")
    f7 = rows[("first_mention", D7)]
    assert f7["label"] == "neutral" and f7["end_multiple"] == Decimal("0.15")


async def test_compute_token_outcomes_skips_first_mention_without_mentions(outcomes_pool):
    pool = outcomes_pool
    token_id = await insert_token(pool, "mint-b", T0)
    await insert_snapshots(pool, token_id, [(T0, 10, 30), (T0 + timedelta(minutes=10), 4, 25)])
    results = await compute_token_outcomes(pool, token_id, now=T0 + timedelta(days=8))
    assert [r.reference for r in results] == ["creation"] * 3
    assert {r.label for r in results} == {"dead"}
    assert len(await outcome_rows(pool, token_id)) == 3


async def test_compute_token_outcomes_skips_creation_without_created_at(outcomes_pool):
    pool = outcomes_pool
    token_id = await insert_token(pool, "mint-c", None)
    shill = T0
    await insert_snapshots(pool, token_id, [(shill - timedelta(minutes=1), 10, 30), (shill + timedelta(minutes=30), 11, 31)])
    await insert_mention(pool, token_id, shill, "t1")
    results = await compute_token_outcomes(pool, token_id, now=T0 + timedelta(days=8))
    assert {r.reference for r in results} == {"first_mention"}
    assert results[0].label == "neutral"
    assert await compute_token_outcomes(pool, 999_999, now=T0) == []


async def test_compute_token_outcomes_is_idempotent(outcomes_pool):
    pool = outcomes_pool
    token_id = await insert_token(pool, "mint-d", T0)
    await insert_snapshots(pool, token_id, [(T0, 10, 30), (T0 + timedelta(minutes=30), 35, 50), (T0 + timedelta(minutes=59), 12, 40)])
    await insert_mention(pool, token_id, T0 + timedelta(minutes=5), "t1")
    first = await compute_token_outcomes(pool, token_id, now=T0 + timedelta(days=8))
    before = await outcome_rows(pool, token_id)
    second = await compute_token_outcomes(pool, token_id, now=T0 + timedelta(days=9))
    after = await outcome_rows(pool, token_id)
    assert len(before) == len(after) == 6
    assert [(r.reference, r.horizon, r.label, r.peak_multiple) for r in first] == \
        [(r.reference, r.horizon, r.label, r.peak_multiple) for r in second]
    for key, row in before.items():
        assert after[key]["label"] == row["label"]
        assert after[key]["computed_at"] == T0 + timedelta(days=9)
    assert await pool.fetchval("SELECT count(*) FROM token_outcomes") == 6


async def test_pending_is_replaced_by_final_label(outcomes_pool):
    pool = outcomes_pool
    token_id = await insert_token(pool, "mint-e", T0)
    await insert_snapshots(pool, token_id, [(T0, 10, 30), (T0 + timedelta(minutes=20), 25, 45)])
    await compute_token_outcomes(pool, token_id, now=T0 + timedelta(minutes=30))
    rows = await outcome_rows(pool, token_id)
    assert {r["label"] for r in rows.values()} == {"pending"}
    assert rows[("creation", H1)]["peak_multiple"] == Decimal("2.5")

    await insert_snapshots(pool, token_id, [(T0 + timedelta(minutes=55), 40, 60)])
    await compute_token_outcomes(pool, token_id, now=T0 + timedelta(hours=1))
    rows = await outcome_rows(pool, token_id)
    assert rows[("creation", H1)]["label"] == "success"
    assert rows[("creation", H1)]["peak_multiple"] == Decimal(4)
    assert rows[("creation", H24)]["label"] == "pending"
    assert rows[("creation", D7)]["label"] == "pending"


async def test_compute_token_outcomes_uses_trades_and_creator_sells(outcomes_pool):
    pool = outcomes_pool
    token_id = await insert_token(pool, "mint-f", T0, creator="dev", initial_cap=10)
    # Снимка при создании нет: стартовая капитализация берётся из tokens.initial_market_cap_sol.
    await insert_trade(pool, token_id, T0, "dev", "buy", 1000, 2)
    await insert_trade(pool, token_id, T0 + timedelta(minutes=5), "someone", "buy", 100, 1, cap=15)
    await insert_trade(pool, token_id, T0 + timedelta(minutes=10), "dev", "sell", 600, 1.5, cap=8)
    await insert_trade(pool, token_id, T0 + timedelta(minutes=50), "someone", "sell", 50, 0.2, cap=7)
    results = await compute_token_outcomes(pool, token_id, now=T0 + timedelta(days=8), horizons=(H1,))
    assert len(results) == 1
    res = results[0]
    assert res.label == "rug"
    assert res.metrics["rug_reasons"] == ["creator_sold"]
    assert res.metrics["creator"]["share"] == 0.6
    assert res.metrics["ref_cap"] == 10.0 and res.peak_multiple == Decimal("1.5")
    assert res.liquidity_alive is None
    row = (await outcome_rows(pool, token_id))[("creation", H1)]
    assert json.loads(row["metrics"])["creator"]["sold_in_window"] == 600.0


async def test_compute_due_outcomes_missing_and_pending(outcomes_pool):
    pool = outcomes_pool
    old = await insert_token(pool, "old", T0 - timedelta(days=10))
    await insert_snapshots(pool, old, [(T0 - timedelta(days=10), 10, 30), (T0 - timedelta(days=10, minutes=-30), 4, 20)])
    fresh = await insert_token(pool, "fresh", T0 - timedelta(minutes=30))
    await insert_snapshots(pool, fresh, [(T0 - timedelta(minutes=30), 10, 30), (T0 - timedelta(minutes=10), 12, 32)])
    orphan = await insert_token(pool, "no-created-at", None)  # нет точек отсчёта → не должен

    assert await compute_due_outcomes(pool, now=T0) == 2
    old_rows = await outcome_rows(pool, old)
    assert {r["label"] for r in old_rows.values()} == {"dead"}
    fresh_rows = await outcome_rows(pool, fresh)
    assert {r["label"] for r in fresh_rows.values()} == {"pending"}
    assert await outcome_rows(pool, orphan) == {}

    # pending с незакончившимся горизонтом — не должен; ничего не делаем.
    assert await compute_due_outcomes(pool, now=T0) == 0

    # Горизонт 1 час у fresh закончился → пересчёт, 1h становится финальной, 24h/7d остаются pending.
    assert await compute_due_outcomes(pool, now=T0 + timedelta(minutes=31)) == 1
    fresh_rows = await outcome_rows(pool, fresh)
    assert fresh_rows[("creation", H1)]["label"] == "neutral"
    assert fresh_rows[("creation", H24)]["label"] == "pending"
    assert await compute_due_outcomes(pool, now=T0 + timedelta(minutes=31)) == 0

    # Новое упоминание добавляет недостающую точку отсчёта first_mention.
    await insert_mention(pool, old, T0 - timedelta(days=10, hours=-1), "t1")
    assert await compute_due_outcomes(pool, now=T0 + timedelta(minutes=31)) == 1
    assert len(await outcome_rows(pool, old)) == 6


async def test_compute_due_outcomes_limit_and_batches(outcomes_pool):
    pool = outcomes_pool
    ids = []
    for i in range(5):
        tid = await insert_token(pool, f"mint-{i}", T0 - timedelta(days=10), creator=f"dev-{i}")
        await insert_snapshots(pool, tid, [(T0 - timedelta(days=10), 10, 30), (T0 - timedelta(days=9), 20, 40)])
        ids.append(tid)
    assert await compute_due_outcomes(pool, now=T0, limit=2, batch_size=1) == 2
    assert await pool.fetchval("SELECT count(DISTINCT token_id) FROM token_outcomes") == 2
    assert await compute_due_outcomes(pool, now=T0, limit=500) == 3
    assert await pool.fetchval("SELECT count(*) FROM token_outcomes") == 15
    assert await compute_due_outcomes(pool, now=T0) == 0


async def test_compute_due_outcomes_ignores_other_rule_versions(outcomes_pool):
    pool = outcomes_pool
    tid = await insert_token(pool, "mint-v", T0 - timedelta(days=10))
    await insert_snapshots(pool, tid, [(T0 - timedelta(days=10), 10, 30), (T0 - timedelta(days=10, minutes=-30), 20, 40)])
    other = RulesV1(rule_version="v2-draft", success_peak_multiple=Decimal(2))
    assert await compute_due_outcomes(pool, now=T0, rules=other) == 1
    assert await compute_due_outcomes(pool, now=T0, rules=other) == 0
    # Разметка v1 всё ещё отсутствует → токен должен по v1.
    assert await compute_due_outcomes(pool, now=T0) == 1
    labels = dict(await pool.fetch(
        "SELECT rule_version, label FROM token_outcomes WHERE horizon = $1", H1,
    ))
    assert labels == {"v2-draft": "success", "v1": "neutral"}
    assert await pool.fetchval("SELECT count(*) FROM token_outcomes") == 6


# ───────────── Слой БД: полнота данных ─────────────


async def test_compute_token_outcomes_reads_coverage_from_tokens(outcomes_pool):
    pool = outcomes_pool
    token_id = await insert_token(pool, "mint-cov", T0, synced_until=None)
    await insert_snapshots(pool, token_id, [(T0, 10, 20), (T0 + timedelta(minutes=30), 30, 40), (T0 + timedelta(minutes=60), 10, 30)])
    now = T0 + timedelta(days=8)

    # Сделки не загружались → все горизонты pending, хотя закончились и данные на 1h есть.
    results = await compute_token_outcomes(pool, token_id, now=now)
    assert {r.label for r in results} == {"pending"}
    rows = await outcome_rows(pool, token_id)
    metrics = json.loads(rows[("creation", H1)]["metrics"])
    assert metrics["coverage"] == {"synced_until": None, "complete": False, "truncated": False}
    assert metrics["pending_reason"] == "data_incomplete"
    assert rows[("creation", H1)]["peak_multiple"] == Decimal(3)

    # Загрузили до T0+2h: 1h решён, 24h и 7d ждут данных.
    await set_synced(pool, token_id, T0 + timedelta(hours=2))
    results = await compute_token_outcomes(pool, token_id, now=now)
    labels = {r.horizon: r.label for r in results}
    assert labels == {H1: "success", H24: "pending", D7: "pending"}
    rows = await outcome_rows(pool, token_id)
    assert json.loads(rows[("creation", H1)]["metrics"])["coverage"]["complete"] is True
    assert json.loads(rows[("creation", H24)]["metrics"])["coverage"]["synced_until"] == (T0 + timedelta(hours=2)).isoformat()

    # Загрузили ровно до конца 24h: он решён (те же три точки — ×3, конец ×1, ликвидность жива → success), 7d ждёт.
    await set_synced(pool, token_id, T0 + H24)
    labels = {r.horizon: r.label for r in await compute_token_outcomes(pool, token_id, now=now)}
    assert labels == {H1: "success", H24: "success", D7: "pending"}


async def test_compute_token_outcomes_records_truncated_backfill(outcomes_pool):
    pool = outcomes_pool
    token_id = await insert_token(pool, "mint-trunc", T0, synced_until=T0 + timedelta(hours=2), truncated=True)
    await insert_snapshots(pool, token_id, [(T0, 10, 20), (T0 + timedelta(minutes=30), 4, 15)])
    results = {r.horizon: r for r in await compute_token_outcomes(pool, token_id, now=T0 + timedelta(days=8))}
    assert results[H1].label == "dead"
    assert results[H1].metrics["coverage"] == {
        "synced_until": (T0 + timedelta(hours=2)).isoformat(), "complete": True, "truncated": True,
    }
    assert results[H24].label == "pending" and results[H24].metrics["coverage"]["truncated"] is True


async def test_due_token_ids_requires_synced_trades(outcomes_pool):
    pool = outcomes_pool
    unsynced = await insert_token(pool, "unsynced", T0 - timedelta(days=10), synced_until=None)
    await insert_snapshots(pool, unsynced, [(T0 - timedelta(days=10), 10, 30), (T0 - timedelta(days=9), 20, 40)])
    await insert_mention(pool, unsynced, T0 - timedelta(days=9), "t-unsynced")
    synced = await insert_token(pool, "synced", T0 - timedelta(days=10), creator="dev-2")
    await insert_snapshots(pool, synced, [(T0 - timedelta(days=10), 10, 30), (T0 - timedelta(days=9), 20, 40)])

    # Без trades_synced_until токен не должен даже при отсутствии строк (ни по creation, ни по first_mention).
    assert await due_token_ids(pool, T0, 100) == [synced]
    assert await compute_due_outcomes(pool, now=T0) == 1
    assert await outcome_rows(pool, unsynced) == {}
    assert await compute_due_outcomes(pool, now=T0) == 0

    # Как только сделки загружены — должен; строки появляются.
    await set_synced(pool, unsynced, T0)
    assert await due_token_ids(pool, T0, 100) == [unsynced]
    assert await compute_due_outcomes(pool, now=T0) == 1
    assert len(await outcome_rows(pool, unsynced)) == 6


async def test_pending_rows_waiting_for_data_are_not_recomputed(outcomes_pool):
    pool = outcomes_pool
    token_id = await insert_token(pool, "mint-wait", T0, synced_until=T0 + timedelta(minutes=30))
    await insert_snapshots(pool, token_id, [(T0, 10, 20), (T0 + timedelta(minutes=20), 25, 45)])
    now = T0 + timedelta(days=8)  # все горизонты давно закончились, но данных нет

    # Нет строк → должен; получает pending (данные неполны) на всех горизонтах.
    assert await compute_due_outcomes(pool, now=now) == 1
    rows = await outcome_rows(pool, token_id)
    assert {r["label"] for r in rows.values()} == {"pending"}
    first_computed = {k: r["computed_at"] for k, r in rows.items()}

    # Ждущие данных pending не пересчитываются каждый цикл.
    assert await due_token_ids(pool, now + timedelta(minutes=5), 100) == []
    assert await compute_due_outcomes(pool, now=now + timedelta(minutes=5)) == 0
    assert await compute_due_outcomes(pool, now=now + timedelta(minutes=10)) == 0
    rows = await outcome_rows(pool, token_id)
    assert {k: r["computed_at"] for k, r in rows.items()} == first_computed

    # Данные догрузили до конца 1h (ровно) → должен; 1h становится окончательным, остальные ждут дальше.
    await insert_snapshots(pool, token_id, [(T0 + timedelta(minutes=55), 40, 60)])
    await set_synced(pool, token_id, T0 + H1)
    assert await due_token_ids(pool, now, 100) == [token_id]
    assert await compute_due_outcomes(pool, now=now + timedelta(minutes=15)) == 1
    rows = await outcome_rows(pool, token_id)
    assert rows[("creation", H1)]["label"] == "success"
    assert rows[("creation", H24)]["label"] == "pending" and rows[("creation", D7)]["label"] == "pending"
    assert json.loads(rows[("creation", H24)]["metrics"])["coverage"]["complete"] is False
    assert await compute_due_outcomes(pool, now=now + timedelta(minutes=20)) == 0

    # Полная загрузка → остальные горизонты решаются; дальше токен не должен.
    await set_synced(pool, token_id, now)
    assert await compute_due_outcomes(pool, now=now + timedelta(minutes=25)) == 1
    rows = await outcome_rows(pool, token_id)
    assert rows[("creation", H24)]["label"] == "success" and rows[("creation", D7)]["label"] == "success"
    assert await compute_due_outcomes(pool, now=now + timedelta(minutes=30)) == 0


async def test_due_uses_min_of_synced_and_now(outcomes_pool):
    # synced_until в будущем относительно now (рассинхрон часов): pending с незакончившимся горизонтом
    # не должен, иначе пересчёт давал бы снова pending в каждом цикле.
    pool = outcomes_pool
    token_id = await insert_token(pool, "mint-skew", T0, synced_until=T0 + timedelta(days=30))
    await insert_snapshots(pool, token_id, [(T0, 10, 20), (T0 + timedelta(minutes=20), 25, 45)])
    assert await compute_due_outcomes(pool, now=T0 + timedelta(minutes=30)) == 1
    rows = await outcome_rows(pool, token_id)
    assert {r["label"] for r in rows.values()} == {"pending"}
    assert json.loads(rows[("creation", H1)]["metrics"])["pending_reason"] == "horizon_not_finished"
    assert await due_token_ids(pool, T0 + timedelta(minutes=31), 100) == []
    assert await due_token_ids(pool, T0 + H1, 100) == [token_id]

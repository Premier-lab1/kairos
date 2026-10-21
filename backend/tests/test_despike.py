"""Защита исходов от одиночных выбросов капитализации (kairos.outcomes.despike).

Случай с сервера 19.10.2026: токен id 98 — у ~100 из 11,6 тыс. сделок капитализация ≈9,8 млн SOL при обычных
20–60 SOL (резервы чужого пула USDC/SOL из сделки-маршрута), пик в token_outcomes «×932 703», метка rug.
БД-тест использует отдельную базу kairos_test_spikes.
"""

import os
from datetime import timedelta
from decimal import Decimal

import asyncpg
import pytest

from dbutil import reset_schema
from kairos.db import migrate
from kairos.outcomes import RULES_V2, SeriesPoint, compute_token_outcomes, despike, label_outcome, spike_indices
from test_outcomes import H1, T0, insert_mention, insert_snapshots, insert_token, insert_trade, m, pt

SPIKES_TEST_DSN = os.environ.get(
    "KAIROS_TEST_SPIKES_DATABASE_URL",
    os.environ.get("KAIROS_TEST_PG", "postgresql://kairos@localhost:5434") + "/kairos_test_spikes",
)

BAD = Decimal("9834854.089696153922486607179")  # капитализация по пулу USDC/SOL из реальной сделки


def D(values):
    return [Decimal(str(v)) for v in values]


def test_single_and_short_runs_of_spikes_are_found():
    assert spike_indices(D([20, 21, 22, 9_800_000, 23, 22, 21])) == {3}
    assert spike_indices(D([20, 21, 22, 9_800_000, 9_800_000, 23, 22, 21, 20])) == {3, 4}
    assert spike_indices(D([40, 41, 42, 0.5, 43, 42, 41])) == {3}  # провал в середине тоже выброс
    assert spike_indices(D([20, 20, 20, 160, 20, 20])) == set()  # ровно ×8 к медиане — ещё не выброс (строго больше)
    assert spike_indices(D([20, 20, 20, 161, 20, 20])) == {3}


def test_steps_and_tail_drops_are_kept():
    # обвал, после которого ряд остаётся внизу (раг) — не выброс ни в одной точке
    assert spike_indices(D([60, 61, 62, 63, 64, 1, 1.1, 1.2, 1.1, 1])) == set()
    # падение в последней точке (раг без сделок после) — край ряда, вниз не фильтруем
    assert spike_indices(D([60, 61, 62, 63, 64, 0.5])) == set()
    # взлёт в последней точке без продолжения — выброс
    assert spike_indices(D([60, 61, 62, 63, 64, 9_800_000])) == {5}
    # рост ступенькой: новый уровень держится — не выброс
    assert spike_indices(D([10, 10, 11, 10, 100, 101, 102, 103, 100])) == set()
    # короткие ряды не трогаем
    assert spike_indices(D([10, 9_800_000, 10, 10])) == set()


def test_despike_drops_cap_spike_with_its_liquidity_and_liquidity_only_spike():
    pts = [pt(i, 20 + i, 10) for i in range(10)]
    pts[4] = pt(4, BAD, 21702)          # снимок чужого пула: неверны и цена, и ликвидность
    pts[7] = pt(7, 27, 900)             # одиночный выброс только ликвидности
    out, n = despike(pts)
    assert n == 2 and len(out) == 9
    assert all(p.market_cap_sol < 100 for p in out)
    assert [p for p in out if p.ts == m(7)][0] == SeriesPoint(m(7), Decimal(27), None)


def _series_with_spikes():
    """Ровный ряд ~20–60 SOL за час с 5 выбросами по 9,8 млн SOL и ликвидностью 21 702 SOL."""
    pts = []
    for i in range(61):
        cap = Decimal(20) + Decimal(i) * Decimal("0.6")
        pts.append(SeriesPoint(m(i), cap, Decimal(30) + Decimal(i) / 2))
        if i in (10, 25, 26, 40, 55):
            pts.append(SeriesPoint(m(i) + timedelta(seconds=20), BAD, Decimal("21702.578")))
    return pts


def test_label_without_despike_shows_the_bug_and_with_despike_is_fixed():
    raw = _series_with_spikes()
    bad = label_outcome(raw, reference="first_mention", reference_time=T0, horizon=H1, now=T0 + H1, rules=RULES_V2)
    assert bad.peak_multiple > 100_000 and bad.label == "rug"  # как на сервере: пик «×сотни тысяч», раг
    clean, n = despike(raw)
    assert n == 5
    good = label_outcome(clean, reference="first_mention", reference_time=T0, horizon=H1, now=T0 + H1, rules=RULES_V2)
    assert good.peak_multiple == Decimal("2.8") and good.label == "neutral"


@pytest.fixture
async def spikes_pool():
    conn = await asyncpg.connect(SPIKES_TEST_DSN)
    try:
        await reset_schema(conn)
        await migrate(conn)
    finally:
        await conn.close()
    pool = await asyncpg.create_pool(SPIKES_TEST_DSN, min_size=1, max_size=4)
    yield pool
    await pool.close()


async def test_compute_token_outcomes_ignores_spikes_from_trades_and_snapshots(spikes_pool):
    pool = spikes_pool
    token_id = await insert_token(pool, "mint-spiky", T0)
    raw = _series_with_spikes()
    good = [p for p in raw if p.market_cap_sol != BAD]
    await insert_snapshots(pool, token_id, [(p.ts, p.market_cap_sol, p.liquidity_sol) for p in good])
    for i, p in enumerate(p for p in raw if p.market_cap_sol == BAD):  # выбросы — и сделкой, и снимком
        await insert_trade(pool, token_id, p.ts, f"w{i}", "sell", 1000, "0.14", cap=BAD)
        await insert_snapshots(pool, token_id, [(p.ts + timedelta(seconds=1), BAD, Decimal("21702.578"))])
    await insert_mention(pool, token_id, T0, "t1")
    results = await compute_token_outcomes(pool, token_id, now=T0 + timedelta(days=8), rules=RULES_V2)
    hour = {r.reference: r for r in results if r.horizon == H1}
    for ref in ("creation", "first_mention"):
        assert hour[ref].label == "neutral"
        assert hour[ref].peak_multiple == Decimal("2.8")
        assert hour[ref].metrics["despiked_points"] == 10
        assert hour[ref].metrics["liquidity_crash"] is None

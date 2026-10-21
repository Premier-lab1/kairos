"""V2 thresholds and version isolation; DB tests use only kairos_test_v2."""

import asyncio
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from kairos.db import migrate
from kairos import outcomes as o
from kairos.report import overview, kol_leaderboard, parse_args as report_args

DSN = "postgresql://kairos@localhost:5434/kairos_test_v2"
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
H24 = timedelta(hours=24)
NOW = T0 + timedelta(days=8)


def pt(seconds, cap, liq=20):
    return o.SeriesPoint(T0 + timedelta(seconds=seconds), cap, liq)


def label(points, rules=o.RULES_V2, **kwargs):
    return o.label_outcome(points, reference=kwargs.pop("reference", "creation"),
                           reference_time=T0, horizon=kwargs.pop("horizon", H24),
                           now=kwargs.pop("now", NOW), rules=rules, **kwargs)


@pytest.mark.parametrize("cap,found", [("10", False), ("9.99", True)])
def test_fast_dump_strict_threshold(cap, found):
    evidence = o.cap_fast_dump([pt(0, 100), pt(60, cap)], o.RULES_V2)
    assert (evidence is not None) is found
    if found:
        assert evidence["drop"] == Decimal("0.9001")
        assert evidence["from_cap"] == 100 and evidence["to_cap"] == Decimal(cap)
        assert evidence["seconds"] == 60


@pytest.mark.parametrize("seconds,found", [(0, False), (1800, True), (1801, False)])
def test_fast_dump_window(seconds, found):
    assert (o.cap_fast_dump([pt(0, 100), pt(seconds, 1)], o.RULES_V2) is not None) is found


def test_fast_dump_sliding_pairs_and_sorting():
    points = [pt(2000, 3), pt(0, 1000), pt(1900, 100), pt(1950, None)]
    evidence = o.cap_fast_dump(points, o.RULES_V2)
    assert evidence["from_ts"] == pt(1900, 100).ts
    assert evidence["to_ts"] == pt(2000, 3).ts


def _drop_bruteforce(series, value, window, threshold):
    """Прежний перебор всех пар: первая пара по j, затем по i."""
    for j, pj in enumerate(series):
        for pi in series[:j]:
            if timedelta(0) < pj.ts - pi.ts <= window and value(pi) > 0 and 1 - value(pj) / value(pi) > threshold:
                return pi.ts, pj.ts
    return None


def _fast_dump_bruteforce(points, rules):
    caps = sorted((p for p in points if p.market_cap_sol is not None), key=lambda p: p.ts)
    return _drop_bruteforce(caps, lambda p: p.market_cap_sol, rules.fast_dump_window, rules.fast_dump_drawdown)


def test_fast_dump_matches_bruteforce_on_random_series():
    import random
    rng = random.Random(7)
    for _ in range(300):
        n = rng.randint(1, 40)
        points = [pt(rng.choice([rng.randint(0, 7200), 600]), rng.choice([None, 0, rng.randint(1, 1000)])) for _ in range(n)]
        found = o.cap_fast_dump(points, o.RULES_V2)
        expected = _fast_dump_bruteforce(points, o.RULES_V2)
        assert ((found["from_ts"], found["to_ts"]) if found else None) == expected
        if found:
            assert found["drop"] > o.RULES_V2.fast_dump_drawdown
            assert 0 < found["seconds"] <= 1800


def test_liquidity_crash_matches_bruteforce_on_random_series():
    import random
    rng = random.Random(11)
    rules = o.RULES_V1
    for _ in range(300):
        points = sorted((o.SeriesPoint(T0 + timedelta(seconds=rng.choice([rng.randint(0, 1800), 300])), None,
                                       rng.choice([None, 0, rng.randint(1, 100)])) for _ in range(rng.randint(1, 40))),
                        key=lambda p: p.ts)
        found = o.liquidity_crash(points, rules)
        liq = [p for p in points if p.liquidity_sol is not None]
        expected = _drop_bruteforce(liq, lambda p: p.liquidity_sol, rules.rug_liquidity_window, rules.rug_liquidity_drop)
        assert ((found["from_ts"], found["to_ts"]) if found else None) == expected


def test_fast_dump_dense_series_is_fast():
    import time
    points = [pt(i * 0.25, 1000 - (i % 500)) for i in range(6000)]  # 6 000 точек за 25 минут, без слива
    start = time.perf_counter()
    assert o.cap_fast_dump(points, o.RULES_V2) is None
    assert time.perf_counter() - start < 1.0


def test_fast_dump_skips_nonpositive_origins():
    assert o.cap_fast_dump([pt(0, 0), pt(1, -1), pt(2, 1)], o.RULES_V2) is None


@pytest.mark.parametrize("peak,cap,found", [(300, "120", True), (300, "120.01", False),
                                           (299, "100", False)])
def test_pnd_inclusive_peak_and_retrace(peak, cap, found):
    points = [pt(0, 100), pt(60, peak), pt(120, cap)]
    evidence = o.pump_and_dump(points, Decimal(100), points[1], o.RULES_V2)
    assert (evidence is not None) is found
    if found:
        assert evidence["retrace"] == Decimal("0.9")
        assert evidence["threshold_cap"] == 120


@pytest.mark.parametrize("seconds,found", [(0, False), (1800, True), (1801, False), (1860, False)])
def test_pnd_window_after_peak(seconds, found):
    peak = pt(60, 300)
    assert (o.pump_and_dump([pt(60 + seconds, 100)], Decimal(100), peak,
                            o.RULES_V2) is not None) is found


def test_rules_disabled_in_v1():
    points = [pt(0, 30), pt(60, 400), pt(120, 30)]
    assert o.Rules is o.RulesV1
    assert o.RULESETS == (o.RULES_V1, o.RULES_V2)
    assert o.cap_fast_dump(points) is None
    assert o.pump_and_dump(points, Decimal(30), points[1]) is None
    result = label(points, o.RULES_V1)
    assert "fast_dump" not in result.metrics and "pump_and_dump" not in result.metrics


@pytest.mark.parametrize("peak,reasons", [(150, ["pump_and_dump"]),
                                         (300, ["pump_and_dump"]),
                                         (301, ["fast_dump", "pump_and_dump"])])
def test_creation_pump_dump(peak, reasons):
    points = [pt(0, 30), pt(600, peak), pt(1200, 30)]
    result = label(points)
    assert result.label == "rug" and result.metrics["rug_reasons"] == reasons
    # Live liquidity and end_multiple == 1 satisfy the existing v1 success rule.
    assert label(points, o.RULES_V1).label == "success"


def test_creation_pump_dump_with_unknown_liquidity_is_v1_neutral():
    points = [pt(0, 30, None), pt(600, 300, None), pt(1200, 30, None)]
    assert label(points, o.RULES_V1).label == "neutral"
    assert label(points).label == "rug"


def test_first_mention_dump_was_dead():
    points = [pt(0, 100), pt(300, 400), pt(900, 25), pt(86400, 25)]
    result = label(points, reference="first_mention")
    assert result.label == "rug"
    assert result.metrics["rug_reasons"] == ["fast_dump", "pump_and_dump"]
    assert label(points, o.RULES_V1, reference="first_mention").label == "dead"


def test_late_retrace_does_not_trigger():
    result = label([pt(0, 30), pt(600, 150), pt(600 + 1860, 30)])
    assert result.label == "success"
    assert result.metrics["pump_and_dump"] is None


@pytest.mark.parametrize("rules", o.RULESETS)
def test_clean_success(rules):
    result = label([pt(0, 30), pt(600, 120), pt(1200, 60)], rules)
    assert result.label == "success" and result.metrics["rug_reasons"] == []


def test_rules_use_only_horizon_and_global_peak():
    points = [pt(0, 30), pt(60, 150), pt(120, 30), pt(180, 300), pt(240, 200)]
    result = label(points)
    assert result.metrics["pump_and_dump"] is None  # earlier local pump is ignored
    result = label([pt(0, 30), pt(3500, 300), pt(3601, 1)], horizon=timedelta(hours=1))
    assert result.label == "success"
    assert result.metrics["fast_dump"] is result.metrics["pump_and_dump"] is None


def test_pending_and_no_data_precede_rug():
    points = [pt(0, 30), pt(60, 400), pt(120, 1)]
    assert label(points, now=T0 + timedelta(minutes=10)).label == "pending"
    assert label(points, coverage=o.Coverage()).label == "pending"
    assert label([]).label == "no_data"


@pytest.mark.parametrize("field", ["fast_dump_drawdown", "fast_dump_window",
                                    "pnd_min_peak_multiple", "pnd_retrace", "pnd_window"])
def test_partial_rule_configuration_disables_detector(field):
    rules = replace(o.RULES_V2, **{field: None})
    points = [pt(0, 30), pt(60, 400), pt(120, 1)]
    result = label(points, rules)
    key = "fast_dump" if field.startswith("fast_dump") else "pump_and_dump"
    assert key not in result.metrics


@pytest.fixture
async def v2_pool():
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        await migrate(conn)
    finally:
        await conn.close()
    async with asyncpg.create_pool(DSN, min_size=1, max_size=2) as pool:
        yield pool


async def seed(pool, mint="v2-token", synced=NOW):
    token_id = await pool.fetchval(
        """INSERT INTO tokens (mint, source, created_at, trades_synced_until)
           VALUES ($1, 'test', $2, $3) RETURNING id""", mint, T0, synced,
    )
    await pool.executemany(
        "INSERT INTO token_snapshots (token_id, ts, market_cap_sol, liquidity_sol, source) VALUES ($1, $2, $3, $4, 'test')",
        [(token_id, p.ts, p.market_cap_sol, p.liquidity_sol)
         for p in [pt(0, 100), pt(300, 400), pt(900, 25)]],
    )
    return token_id


async def test_db_versions_side_by_side(v2_pool):
    tid = await seed(v2_pool)
    await o.compute_token_outcomes(v2_pool, tid, now=NOW)
    before = await v2_pool.fetch("SELECT * FROM token_outcomes WHERE rule_version = 'v1' ORDER BY horizon")
    await o.compute_token_outcomes(v2_pool, tid, now=NOW, rules=o.RULES_V2)
    assert await v2_pool.fetch("SELECT * FROM token_outcomes WHERE rule_version = 'v1' ORDER BY horizon") == before
    assert await v2_pool.fetchval("SELECT count(*) FROM token_outcomes") == 6
    assert {r["label"] for r in before} == {"dead"}
    assert {r["label"] for r in await v2_pool.fetch(
        "SELECT label FROM token_outcomes WHERE rule_version='v2'")} == {"rug"}


async def test_db_due_v2_with_only_v1_rows(v2_pool):
    tid = await seed(v2_pool)
    await o.compute_token_outcomes(v2_pool, tid, now=NOW)
    assert await o.due_token_ids(v2_pool, NOW, 10) == []
    assert await o.due_token_ids(v2_pool, NOW, 10, rules=o.RULES_V2) == [tid]
    assert await o.compute_due_outcomes(v2_pool, now=NOW, rules=o.RULES_V2) == 1
    assert await o.compute_due_outcomes(v2_pool, now=NOW, rules=o.RULES_V2) == 0


async def test_db_earlier_mention_makes_final_rows_due(v2_pool):
    """Сбор запаздывает: более раннее упоминание может прийти после окончательной разметки."""
    tid = await seed(v2_pool)
    kol = await v2_pool.fetchval("INSERT INTO kols (username) VALUES ('late') RETURNING id")
    mention = """INSERT INTO mentions (kol_id, chain, ca, token_id, tweet_id, tweet_time, source)
                 VALUES ($1, 'SOLANA', 'v2-token', $2, $3, $4, 'test')"""
    await v2_pool.execute(mention, kol, tid, "t2", T0 + timedelta(minutes=10))
    assert await o.compute_due_outcomes(v2_pool, now=NOW) == 1
    assert await o.due_token_ids(v2_pool, NOW, 10) == []
    await v2_pool.execute(mention, kol, tid, "t1", T0 + timedelta(minutes=2))
    assert await o.due_token_ids(v2_pool, NOW, 10) == [tid]
    await o.compute_due_outcomes(v2_pool, now=NOW)
    times = await v2_pool.fetch("""SELECT DISTINCT reference_time FROM token_outcomes
                                   WHERE token_id = $1 AND reference = 'first_mention'""", tid)
    assert [r["reference_time"] for r in times] == [T0 + timedelta(minutes=2)]
    assert await o.due_token_ids(v2_pool, NOW, 10) == []


async def test_db_pending_is_version_independent(v2_pool):
    tid = await seed(v2_pool, synced=T0 + timedelta(minutes=30))
    await o.compute_token_outcomes(v2_pool, tid, now=NOW, rules=o.RULES_V2)
    await v2_pool.execute("UPDATE tokens SET trades_synced_until=$1 WHERE id=$2", NOW, tid)
    await o.compute_token_outcomes(v2_pool, tid, now=NOW)
    assert await o.due_token_ids(v2_pool, NOW, 10) == []
    assert await o.compute_due_outcomes(v2_pool, now=NOW, rules=o.RULES_V2) == 1


async def test_cli_summary_and_recompute_final_rows(v2_pool):
    tid = await seed(v2_pool)
    await seed(v2_pool, "unsynced", synced=None)
    assert await o.recompute_all(v2_pool, now=NOW) == 6
    rows = await o.outcome_summary(v2_pool)
    assert len(rows) == 6 and sum(r["count"] for r in rows) == 6
    assert {(r["rule_version"], r["reference"], r["horizon"], r["label"])
            for r in rows} == {(v, "creation", h, label)
                               for v, label in [("v1", "dead"), ("v2", "rug")]
                               for h in o.DEFAULT_HORIZONS}
    await v2_pool.execute("UPDATE token_outcomes SET label='neutral'")
    assert await o.recompute_all(v2_pool, (o.RULES_V2,), limit=0, now=NOW) == 0
    assert await o.recompute_all(v2_pool, (o.RULES_V2,), limit=1, now=NOW) == 3
    assert {r["label"] for r in await o.outcome_summary(v2_pool, (o.RULES_V2,))} == {"rug"}
    assert await v2_pool.fetchval("SELECT count(*) FROM token_outcomes WHERE token_id != $1", tid) == 0
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "kairos.outcomes", "--dsn", DSN, "--rule-version", "v2", "--limit", "1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONIOENCODING": "cp1251"},
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
    assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
    output = stdout.decode("utf-8")
    assert all(text in output for text in ("rule_version", "reference", "horizon", "label", "count", "v2", "rug"))
    assert "v1" not in output


async def test_report_selected_version(v2_pool):
    tid = await seed(v2_pool)
    kol = await v2_pool.fetchval("INSERT INTO kols (username) VALUES ('v2-kol') RETURNING id")
    await v2_pool.execute(
        """INSERT INTO mentions (kol_id, chain, ca, token_id, tweet_id, tweet_time, source)
           VALUES ($1, 'SOLANA', 'v2-token', $2, 'v2-tweet', $3, 'test')""", kol, tid, T0,
    )
    await o.recompute_all(v2_pool, now=NOW)
    assert {r["label"] for r in (await overview(v2_pool, "v2"))["outcomes"]} == {"rug"}
    assert {r["label"] for r in (await overview(v2_pool))["outcomes"]} == {"dead"}
    v1, = await kol_leaderboard(v2_pool, min_tokens=1)
    v2, = await kol_leaderboard(v2_pool, min_tokens=1, rule_version="v2")
    assert v1["dead"] == v2["rug"] == 1
    assert v1["rug"] == v2["dead"] == 0
    assert report_args([]).rule_version == "v2"


def test_cli_version_selection_and_validation():
    assert o.parse_args([]).rule_version == "all"
    assert o.parse_args(["--rule-version", "v1"]).rule_version == "v1"
    for args in (["--rule-version", "v3"], ["--limit", "-1"]):
        with pytest.raises(SystemExit):
            o.parse_args(args)

"""Walk-forward evaluation of s1, using tweet time rather than collection time."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sys

import asyncpg

from kairos.config import get_settings
from kairos.scoring import DAY, FINAL, load_history, score_token, wilson_interval


@dataclass
class BacktestResult:
    n: int
    warmup_n: int
    population_n: int
    base: dict
    thresholds: list
    top_ten_percent: dict
    histogram: list
    component_means: dict
    confidence_counts: dict
    excluded: dict
    entry_buckets: list
    deciles: list
    collection_lag: dict
    suggested_threshold: float | None
    warnings: list[str]
    notes: list[str]
    parameters: dict
    scores: list


def _metrics(rows, base_rate=None):
    n = len(rows)
    s = sum(r["label"] == "success" for r in rows)
    rug = sum(r["label"] == "rug" for r in rows)
    precision = s / n if n else None
    return dict(n=n, success=s, rug=rug, precision=precision,
                success_interval=wilson_interval(s, n), rug_share=rug / n if n else None,
                rug_interval=wilson_interval(rug, n),
                lift=precision / base_rate if base_rate and precision is not None else None)


def _percentile(values, q):
    if not values:
        return None
    pos = (len(values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


async def run_backtest(db, *, rule_version="v2", horizon=DAY, delay=timedelta(minutes=10),
                       thresholds=(40, 50, 60, 70), warmup_share=0.5):
    if not 0 <= warmup_share <= 1:
        raise ValueError("warmup_share must be between 0 and 1")
    if delay < timedelta(0) or horizon <= timedelta(0):
        raise ValueError("delay must be nonnegative and horizon positive")
    hist = await load_history(db, rule_version, horizon)
    population = sorted((tid for tid in hist.final if tid in hist.first), key=lambda tid: (hist.first[tid], tid))
    warmup_n = int(len(population) * warmup_share)
    rows = []
    warnings = []
    for tid in population[warmup_n:]:
        result = score_token(hist, tid, hist.first[tid] + delay, rule_version=rule_version, horizon=horizon)
        if result is not None:
            rows.append(dict(token_id=tid, label=hist.final[tid]["label"], score=result.score,
                             confidence=result.confidence, entry_minute=result.features["token"]["entry_minute"],
                             components=result.features["components"]))
    base = _metrics(rows)
    b = base["precision"]
    # Сигнал = скор выше порога и confidence не ниже medium: только такие уходят в бота (docs/signals.md).
    threshold_rows = [dict(threshold=th, **_metrics([r for r in rows if r["score"] >= th
                                                     and r["confidence"] in ("medium", "high")], b))
                      for th in thresholds]
    ranked = sorted(rows, key=lambda r: (-r["score"], r["token_id"]))
    top = _metrics(ranked[:math.ceil(len(rows) * 0.1)], b)
    histogram = [dict(bucket=f"{lo}–{lo+10}" + (" (inclusive)" if lo == 90 else ""),
                      **_metrics([r for r in rows if lo <= r["score"] < lo + 10 or lo == 90 and r["score"] == 100], b))
                 for lo in range(0, 100, 10)]
    means = {}
    for group, selected in (("success", [r for r in rows if r["label"] == "success"]),
                            ("non_success", [r for r in rows if r["label"] != "success"])):
        keys = ("shillers", "breadth", "creator_clean", "entry", "similar", "rug_shillers", "creator_rug", "similar_rug",
                "kol_holding", "kol_fast_dumpers", "kol_sold_after_call")
        means[group] = {k: sum(r["components"].get(k, 0) for r in selected) / len(selected) if selected else None for k in keys}
    counts = Counter(o["label"] for (tid, ref), o in hist.outcomes.items() if ref == "first_mention")
    excluded_n = counts["pending"] + counts["no_data"]
    excluded = dict(no_data=counts["no_data"], pending=counts["pending"], total=excluded_n,
                    share=excluded_n / sum(counts.values()) if counts else 0.0,
                    final_without_mentions=len(hist.final) - len(population))
    buckets = {key: [] for key in ("0–10", "10–60", "60–180", ">180", "unknown")}
    for r in rows:
        minute = r["entry_minute"]
        key = "unknown" if minute is None else "0–10" if minute <= 10 else "10–60" if minute <= 60 else "60–180" if minute <= 180 else ">180"
        buckets[key].append(r)
    entry_buckets = [dict(bucket=k, **_metrics(v, b)) for k, v in buckets.items()]
    deciles = [dict(decile=i+1, **_metrics(ranked[i*len(rows)//10:(i+1)*len(rows)//10], b)) for i in range(10)]
    lag = sorted((m["first_seen_at"] - m["tweet_time"]).total_seconds() for m in hist.mentions
                 if m.get("source") == "frontrun_search" and m.get("first_seen_at") is not None)
    suggested = min((r["threshold"] for r in threshold_rows if r["n"] >= 30 and b is not None
                     and r["precision"] >= 3 * b), default=None)
    if not rows:
        warnings.append("Тестовое множество пусто: нет токенов для оценки.")
    if len(rows) < 100:
        warnings.append(f"Малая тестовая выборка: {len(rows)} < 100 токенов.")
    for r in threshold_rows:
        if r["n"] < 30:
            warnings.append(f"Порог {r['threshold']}: {r['n']} < 30 сигналов; оценка ненадёжна.")
    notes = ["Метка измеряется от первого шилла; скор использует данные до первого шилла + delay. Это слегка оптимистичная оценка скора, без коррекции.",
             "Бэктест видит упоминания по tweet_time, live-скоринг — после сбора. Задержка сбора делает бэктест оптимистичным.",
             "Децили: 1 — самые высокие скоры; одинаковые скоры разделяются по token_id. Top 10% округляется вверх.",
             "Корзины entry: ≤10, (10,60], (60,180], >180 минут; отрицательная минута включена в первую корзину."]
    return BacktestResult(len(rows), warmup_n, len(population), base, threshold_rows, top, histogram, means,
                          {level: sum(r["confidence"] == level for r in rows) for level in ("low", "medium", "high")},
                          excluded, entry_buckets, deciles,
                          dict(n=len(lag), median_seconds=_percentile(lag, .5), p90_seconds=_percentile(lag, .9)),
                          suggested, warnings, notes,
                          dict(rule_version=rule_version, horizon=str(horizon), delay=str(delay), warmup_share=warmup_share), rows)


def _cell(value):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, (tuple, list)):
        return "[" + ", ".join(_cell(v) for v in value) + "]"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |",
                      *("| " + " | ".join(_cell(v) for v in row) + " |" for row in rows)])


def format_markdown(result, meta):
    r = result
    parts = ["# Walk-forward бэктест s1", "\n".join(f"{k}: {_cell(v)}" for k, v in meta.items()),
             "Параметры: " + ", ".join(f"{k}={v}" for k, v in r.parameters.items()),
             f"Популяция: {r.population_n}; warm-up (исключён из метрик): {r.warmup_n}; тест: {r.n}.",
             "## Базовые доли", _table(["n", "success", "95% Wilson", "rug", "95% Wilson"],
                  [[r.n, r.base["precision"], r.base["success_interval"], r.base["rug_share"], r.base["rug_interval"]]])]
    headers = ["Группа", "n", "success precision", "95% Wilson", "rug share", "lift"]
    def metrics_row(name, v):
        return [name, v["n"], v["precision"], v["success_interval"], v["rug_share"], v["lift"]]
    parts += ["## Пороги", "Сигнал — скор не ниже порога и confidence medium или high (как в боте). Top 10% — по скору без условия на confidence.", _table(headers, [metrics_row(v["threshold"], v) for v in r.thresholds] + [metrics_row("Top 10%", r.top_ten_percent)]),
              "Предлагаемый порог: " + (str(r.suggested_threshold) if r.suggested_threshold is not None else "not enough data / недостаточно данных"),
              "## Исключения", _table(["Показатель", "Значение"], r.excluded.items()),
              "## Минута входа", _table(headers, [metrics_row(v["bucket"], v) for v in r.entry_buckets]),
              "## Децили скора", _table(headers, [metrics_row(v["decile"], v) for v in r.deciles]),
              "## Гистограмма скора", _table(["Скор", "n", "success", "rug"], [[v["bucket"], v["n"], v["success"], v["rug"]] for v in r.histogram]),
              "## Средние компоненты", _table(["Компонента", "success", "non-success"],
                    [[k, v, r.component_means["non_success"][k]] for k, v in r.component_means["success"].items()]),
              "## Confidence", _table(["Уровень", "n"], r.confidence_counts.items()),
              "## Задержка сбора frontrun_search (секунды)", _table(["Показатель", "Значение"], r.collection_lag.items()),
              "## Ограничения", *r.warnings, *r.notes]
    return "\n\n".join(parts) + "\n"


async def _main(args):
    conn = await asyncpg.connect(args.dsn or get_settings().database_url)
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            result = await run_backtest(conn, rule_version=args.rule_version,
                horizon={"1h": timedelta(hours=1), "24h": DAY, "7d": timedelta(days=7)}[args.horizon],
                delay=timedelta(minutes=args.delay_min), warmup_share=args.warmup_share)
            dataset = await conn.fetchval("SELECT current_database()")
        markdown = format_markdown(result, {"dataset": dataset, "dump_time": datetime.now(timezone.utc).isoformat()})
        print(markdown)
        if args.out:
            Path(args.out).write_text(markdown, encoding="utf-8")
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn")
    parser.add_argument("--rule-version", default="v2")
    parser.add_argument("--horizon", choices=("1h", "24h", "7d"), default="24h")
    parser.add_argument("--delay-min", type=float, default=10)
    parser.add_argument("--warmup-share", type=float, default=.5)
    parser.add_argument("--out")
    asyncio.run(_main(parser.parse_args()))

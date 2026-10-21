"""Разметка исхода токена по правилам v1 (docs/outcomes.md).

Модуль состоит из двух слоёв:

1. Чистое ядро без БД: ``SeriesPoint``, ``CreatorTrade``, ``RulesV1`` и ``label_outcome`` — по ряду
   капитализации/ликвидности и сделкам креатора выдаёт ``OutcomeResult`` с меткой и промежуточными
   числами в ``metrics``. Все пороги — именованные поля ``RulesV1``.
2. Слой БД: ``compute_token_outcomes`` загружает ряды одного токена, считает исходы для точек отсчёта
   ``creation`` и ``first_mention`` на всех горизонтах и пишет их в ``token_outcomes`` (upsert);
   ``compute_due_outcomes`` находит токены с загруженными сделками без разметки или с ``pending`` на
   закончившемся и уже покрытом данными горизонте и считает их пачками.

Порядок меток v1 (срабатывает первая): no_data → pending → rug → success → dead → neutral.

Полнота данных (``Coverage``): сделки и снимки Helius загружены до ``tokens.trades_synced_until``. Пока конец
горизонта позже этого момента (или момент неизвестен — NULL), отсутствие точек нельзя отличить от «ещё не
загрузили», поэтому итог — ``pending`` с ``metrics.coverage = {"synced_until", "complete": false, ...}``,
а не ``no_data`` и не окончательная метка. Когда ``trades_synced_until`` ≥ конца горизонта, разметка идёт как
обычно (``complete: true``); обрезанный бэкфилл (``trades_backfill_truncated``) полноту не отменяет, а только
помечается ``coverage.truncated = true``. Чистое ядро без ``coverage`` (None) считает данные полными.

Уточнения правил, принятые при реализации (см. докстринги функций):

* Капитализация в точке отсчёта (``ref``) — последняя известная точка не позже ``reference_time``;
  если такой нет — первая точка в пределах ``RulesV1.reference_lookahead`` (2 минуты) после неё;
  иначе данных нет.
* ``no_data`` требует хотя бы одной точки капитализации на горизонте *после* опорной точки: одной
  опорной точки недостаточно, чтобы судить об исходе. Пока горизонт не закончился, ``no_data`` не
  ставится — данные ещё могут прийти, поэтому результат ``pending`` (терминальная метка ``no_data``
  не пересчитывалась бы ``compute_due_outcomes``).
* Все ряды берутся в окне горизонта: опорная точка плюс точки с ``ts`` в ``(ref_point.ts, horizon_end]``.
  Капитализация «в конце горизонта» — последняя известная точка не позже конца горизонта (значение
  переносится вперёд; давность записывается в ``metrics.end_staleness_s``).
* Все числа — ``Decimal`` (как ``numeric`` в PostgreSQL), чтобы граничные сравнения (ровно ×3,
  ровно 50 %) были точными.
"""

from __future__ import annotations

import json
import logging
import argparse
import asyncio
import sys
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import asyncpg

log = logging.getLogger(__name__)

REF_CREATION = "creation"
REF_FIRST_MENTION = "first_mention"
REFERENCES = (REF_CREATION, REF_FIRST_MENTION)

LABEL_NO_DATA = "no_data"
LABEL_PENDING = "pending"
LABEL_RUG = "rug"
LABEL_SUCCESS = "success"
LABEL_DEAD = "dead"
LABEL_NEUTRAL = "neutral"

DEFAULT_HORIZONS: tuple[timedelta, ...] = (timedelta(hours=1), timedelta(hours=24), timedelta(days=7))
DEFAULT_BATCH_SIZE = 50


def _dec(value: Any) -> Decimal | None:
    """Приводит число к Decimal без потери точности (через str для float)."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ───────────── Чистое ядро ─────────────


@dataclass(frozen=True)
class SeriesPoint:
    """Точка ряда: капитализация и/или ликвидность токена в SOL в момент ``ts``.

    Источник — снимок ``token_snapshots`` (есть и капитализация, и ликвидность) или сделка из ``trades``
    (только капитализация, ``liquidity_sol`` = None). Любое из двух чисел может отсутствовать.
    """

    ts: datetime
    market_cap_sol: Decimal | None = None
    liquidity_sol: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "market_cap_sol", _dec(self.market_cap_sol))
        object.__setattr__(self, "liquidity_sol", _dec(self.liquidity_sol))


@dataclass(frozen=True)
class CreatorTrade:
    """Сделка кошелька креатора по этому токену: ``side`` — 'buy' | 'sell', объём в токенах и SOL."""

    ts: datetime
    side: str
    token_amount: Decimal
    sol_amount: Decimal | None = None

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError(f"unknown trade side: {self.side!r}")
        object.__setattr__(self, "token_amount", _dec(self.token_amount))
        object.__setattr__(self, "sol_amount", _dec(self.sol_amount))


@dataclass(frozen=True)
class RulesV1:
    """Пороги правил v1. Новые пороги — новый ``rule_version``, старая разметка остаётся рядом."""

    rule_version: str = "v1"
    # success: peak_multiple ≥ 3 и end_multiple ≥ 1 и liquidity_alive
    success_peak_multiple: Decimal = Decimal("3")
    success_end_multiple: Decimal = Decimal("1")
    # rug: ликвидность упала больше чем на 80 % за ≤ 10 минут
    rug_liquidity_drop: Decimal = Decimal("0.8")
    rug_liquidity_window: timedelta = timedelta(minutes=10)
    # rug: креатор продал больше 50 % своих токенов в первый час
    rug_creator_sell_share: Decimal = Decimal("0.5")
    rug_creator_window: timedelta = timedelta(hours=1)
    # rug: end_multiple < 0.1 при max_drawdown > 0.9
    rug_end_multiple: Decimal = Decimal("0.1")
    rug_max_drawdown: Decimal = Decimal("0.9")
    # dead: не мигрировал и end_multiple < 0.5
    dead_end_multiple: Decimal = Decimal("0.5")
    # liquidity_alive: в конце горизонта ≥ 50 % пиковой ликвидности и ≥ 5 SOL
    liquidity_alive_share: Decimal = Decimal("0.5")
    liquidity_alive_min_sol: Decimal = Decimal("5")
    # точка отсчёта: если нет точки не позже reference_time, берём первую в пределах 2 минут после
    reference_lookahead: timedelta = timedelta(minutes=2)
    fast_dump_drawdown: Decimal | None = None
    fast_dump_window: timedelta | None = None
    pnd_min_peak_multiple: Decimal | None = None
    pnd_retrace: Decimal | None = None
    pnd_window: timedelta | None = None


RULES_V1 = RulesV1()
Rules = RulesV1
RULES_V2 = RulesV1(
    rule_version="v2", fast_dump_drawdown=Decimal("0.9"),
    fast_dump_window=timedelta(minutes=30), pnd_min_peak_multiple=Decimal("3"),
    pnd_retrace=Decimal("0.9"), pnd_window=timedelta(minutes=30),
)
RULESETS = (RULES_V1, RULES_V2)


@dataclass(frozen=True)
class Coverage:
    """Докуда загружены сделки/снимки токена (``tokens.trades_synced_until``, ``tokens.trades_backfill_truncated``).

    ``synced_until`` = None — не загружали вовсе: ни один горизонт не полон. ``truncated`` — последний бэкфилл
    упёрся в лимит страниц: за последней сделкой возможны пропуски; на полноту не влияет, только записывается
    в ``metrics.coverage.truncated``.
    """

    synced_until: datetime | None = None
    truncated: bool = False

    def complete_for(self, horizon_end: datetime) -> bool:
        """Данные на горизонте полны, если загружено не позже конца горизонта (ровно в конец — полны)."""
        return self.synced_until is not None and self.synced_until >= horizon_end


@dataclass
class OutcomeResult:
    """Одна строка ``token_outcomes``. ``metrics`` — промежуточные числа, пригодные для jsonb."""

    token_id: int | None
    horizon: timedelta
    rule_version: str
    reference: str
    reference_time: datetime
    label: str
    peak_multiple: Decimal | None = None
    end_multiple: Decimal | None = None
    max_drawdown: Decimal | None = None
    liquidity_alive: bool | None = None
    migrated: bool | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


def _jsonable(value: Any) -> Any:
    """Готовит metrics к jsonb: Decimal → float, datetime → ISO, timedelta → секунды."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    return value


DESPIKE_FACTOR = Decimal(8)
DESPIKE_NEIGHBOURS = 7
DESPIKE_MIN_POINTS = 5


def _median(values: Sequence[Decimal]) -> Decimal:
    return sorted(values)[len(values) // 2]  # как despike графика дашборда (web/src/lib/server/queries/tokens.ts)


def spike_indices(values: Sequence[Decimal], factor: Decimal = DESPIKE_FACTOR,
                  neighbours: int = DESPIKE_NEIGHBOURS) -> set[int]:
    """Одиночные выбросы ряда: точка больше чем в ``factor`` раз отличается от медианы соседей.

    Соседи — до ``neighbours`` точек слева и столько же справа, медианы берутся по каждой стороне отдельно, и
    выбросом точка считается, только если отличается в одну сторону от *обеих* медиан. Так ступенька (обвал или
    рост, после которого ряд остаётся на новом уровне) не трогается: справа от неё медиана уже нового уровня.
    На краю ряда (соседи только с одной стороны) выбросом считается только взлёт: падение в последней точке может
    быть настоящим рагом, а взлёт без продолжения — нет. Ряды короче ``DESPIKE_MIN_POINTS`` не фильтруются.
    Плохие точки — сделки, посчитанные по резервам чужого пула (см. kairos/sources/helius.py).
    """
    out: set[int] = set()
    if len(values) < DESPIKE_MIN_POINTS:
        return out
    for i, v in enumerate(values):
        left = values[max(0, i - neighbours):i]
        right = values[i + 1:i + 1 + neighbours]
        medians = [_median(side) for side in (left, right) if side]
        if any(m <= 0 for m in medians):
            continue
        if all(v > m * factor for m in medians):
            out.add(i)
        elif len(medians) == 2 and all(v * factor < m for m in medians):
            out.add(i)
    return out


def despike(points: Sequence[SeriesPoint]) -> tuple[list[SeriesPoint], int]:
    """Убирает одиночные выбросы капитализации и ликвидности (``spike_indices``), возвращает (ряд, число правок).

    Выброс капитализации убирает точку целиком (у снимка из чужого пула неверна и ликвидность), выброс одной
    ликвидности — только её. ``points`` должны быть отсортированы по ``ts``.
    """
    pts = list(points)
    cap_idx = [i for i, p in enumerate(pts) if p.market_cap_sol is not None]
    liq_idx = [i for i, p in enumerate(pts) if p.liquidity_sol is not None]
    bad_cap = {cap_idx[k] for k in spike_indices([pts[i].market_cap_sol for i in cap_idx])}
    bad_liq = {liq_idx[k] for k in spike_indices([pts[i].liquidity_sol for i in liq_idx])} - bad_cap
    out = []
    for i, p in enumerate(pts):
        if i in bad_cap:
            continue
        out.append(SeriesPoint(p.ts, p.market_cap_sol) if i in bad_liq else p)
    return out, len(bad_cap) + len(bad_liq)


def reference_point(points: Sequence[SeriesPoint], reference_time: datetime, rules: RulesV1 = RULES_V1) -> SeriesPoint | None:
    """Опорная точка капитализации.

    Последняя точка с известной капитализацией не позже ``reference_time``; если такой нет — первая
    точка с капитализацией в интервале ``(reference_time, reference_time + rules.reference_lookahead]``.
    Иначе None (данных для точки отсчёта нет). ``points`` должны быть отсортированы по ``ts``.
    """
    before = [p for p in points if p.market_cap_sol is not None and p.ts <= reference_time]
    if before:
        return before[-1]
    deadline = reference_time + rules.reference_lookahead
    for p in points:
        if p.market_cap_sol is not None and reference_time < p.ts <= deadline:
            return p
    return None


def _first_drop(series, value, window: timedelta, threshold: Decimal):
    """Первая (по времени j, затем по порядку i) пара точек ``i`` раньше ``j`` с ``0 < ts_j − ts_i ≤ window``,
    ``value(i) > 0`` и падением ``1 − value(j) / value(i)`` строго больше ``threshold``; ``series`` отсортирован по ts.

    То же, что перебор всех пар, но за линейное время: момент ``j`` ищется по скользящему максимуму окна
    (если падение больше порога от какой-то точки окна, то и от максимума), а пара для отчёта — перебором
    только для найденного ``j``. Перебор пар на плотных токенах (5–10 тыс. точек) занимал до 100 с на токен.
    """
    window_max: deque = deque()   # индексы точек окна с убывающим value
    pending: list[int] = []       # точки с тем же ts, что и текущая j: в окно ещё не входят
    for j, pj in enumerate(series):
        if pending and series[pending[0]].ts < pj.ts:
            for i in pending:
                if value(series[i]) > 0:
                    while window_max and value(series[window_max[-1]]) <= value(series[i]):
                        window_max.pop()
                    window_max.append(i)
            pending = []
        while window_max and pj.ts - series[window_max[0]].ts > window:
            window_max.popleft()
        if window_max and 1 - value(pj) / value(series[window_max[0]]) > threshold:
            for pi in series[:j]:
                if timedelta(0) < pj.ts - pi.ts <= window and value(pi) > 0:
                    drop = 1 - value(pj) / value(pi)
                    if drop > threshold:
                        return pi, pj, drop
        pending.append(j)
    return None


def liquidity_crash(points: Sequence[SeriesPoint], rules: RulesV1 = RULES_V1) -> dict[str, Any] | None:
    """Ищет падение ликвидности больше чем на ``rug_liquidity_drop`` за ``rug_liquidity_window`` или меньше.

    Ряд нерегулярный, поэтому проверяются все упорядоченные пары точек с известной ликвидностью
    (i раньше j), для которых ``0 < ts_j − ts_i ≤ window`` и ``liq_i > 0``: падение
    ``1 − liq_j / liq_i`` должно быть *строго* больше порога. Ровно 80 % — не rug; окно ровно
    10 минут — считается (≤), 10 минут и 1 секунда — нет. Возвращается первая найденная пара
    (по времени j) или None.
    """
    liq = [p for p in points if p.liquidity_sol is not None]
    found = _first_drop(liq, lambda p: p.liquidity_sol, rules.rug_liquidity_window, rules.rug_liquidity_drop)
    if found is None:
        return None
    pi, pj, drop = found
    return {
        "from_ts": pi.ts, "to_ts": pj.ts, "from_sol": pi.liquidity_sol,
        "to_sol": pj.liquidity_sol, "drop": drop, "seconds": (pj.ts - pi.ts).total_seconds(),
    }


def cap_fast_dump(points: Sequence[SeriesPoint], rules: RulesV1 = RULES_V1) -> dict[str, Any] | None:
    """Find the first cap drop strictly above the threshold in the inclusive time window."""
    if rules.fast_dump_drawdown is None or rules.fast_dump_window is None:
        return None
    caps = sorted((p for p in points if p.market_cap_sol is not None), key=lambda p: p.ts)
    found = _first_drop(caps, lambda p: p.market_cap_sol, rules.fast_dump_window, rules.fast_dump_drawdown)
    if found is None:
        return None
    pi, pj, drop = found
    return {
        "from_ts": pi.ts, "to_ts": pj.ts, "from_cap": pi.market_cap_sol,
        "to_cap": pj.market_cap_sol, "drop": drop,
        "seconds": (pj.ts - pi.ts).total_seconds(),
    }


def pump_and_dump(
    caps: Sequence[SeriesPoint], ref: Decimal, peak_point: SeriesPoint,
    rules: RulesV1 = RULES_V1,
) -> dict[str, Any] | None:
    """Find a retrace of at least the configured gain share after the global peak."""
    if rules.pnd_min_peak_multiple is None or rules.pnd_retrace is None or rules.pnd_window is None:
        return None
    peak = peak_point.market_cap_sol
    if ref <= 0 or peak is None or peak / ref < rules.pnd_min_peak_multiple:
        return None
    threshold = ref + (1 - rules.pnd_retrace) * (peak - ref)
    for p in sorted(caps, key=lambda p: p.ts):
        if (peak_point.ts < p.ts <= peak_point.ts + rules.pnd_window
                and p.market_cap_sol is not None and p.market_cap_sol <= threshold):
            return {
                "from_ts": peak_point.ts, "to_ts": p.ts, "from_cap": peak,
                "to_cap": p.market_cap_sol, "ref_cap": ref, "threshold_cap": threshold,
                "retrace": (peak - p.market_cap_sol) / (peak - ref),
                "seconds": (p.ts - peak_point.ts).total_seconds(),
            }
    return None


def creator_sell_share(
    trades: Sequence[CreatorTrade],
    window_start: datetime,
    window_end: datetime,
    initial_tokens: Decimal | None = None,
) -> dict[str, Any]:
    """Доля своих токенов, проданная креатором в окне ``[window_start, window_end]``.

    Остаток креатора = ``initial_tokens`` (начальная покупка, если её *нет* среди сделок) + покупки −
    продажи по сделкам его кошелька. База («свои токены») = остаток на начало окна (сделки строго до
    ``window_start``) + покупки внутри окна; так начальная покупка в момент создания (ts == created_at)
    входит в базу для точки отсчёта ``creation``. Доля = продажи в окне / база.

    Если известных покупок не хватает (остаток уходит в минус на любой сделке не позже ``window_end``),
    база неизвестна — начальная покупка не попала в сделки. Тогда ``share`` = None и ``base_unknown`` =
    True: правило «продал > 50 %» не срабатывает, а не считается нарушенным — лучше пропустить rug,
    чем размечать как rug каждую продажу креатора при неполных данных. Нулевая база (креатор ничего
    не держал) тоже даёт ``share`` = None.
    """
    held = initial_tokens if initial_tokens is not None else Decimal(0)
    base_unknown = False
    held_at_start = None
    bought = Decimal(0)
    sold = Decimal(0)
    for t in sorted(trades, key=lambda t: t.ts):
        if t.ts > window_end:
            break
        if held_at_start is None and t.ts >= window_start:
            held_at_start = held
        if t.side == "buy":
            held += t.token_amount
            if t.ts >= window_start:
                bought += t.token_amount
        else:
            held -= t.token_amount
            if t.ts >= window_start:
                sold += t.token_amount
        if held < 0:
            base_unknown = True
    if held_at_start is None:
        held_at_start = held
    base = held_at_start + bought
    share = None if base_unknown or base <= 0 else sold / base
    return {
        "held_at_start": held_at_start, "bought_in_window": bought, "sold_in_window": sold,
        "base": base, "share": share, "base_unknown": base_unknown,
        "window_start": window_start, "window_end": window_end,
    }


def label_outcome(
    points: Iterable[SeriesPoint],
    creator_trades: Iterable[CreatorTrade] = (),
    *,
    reference: str,
    reference_time: datetime,
    horizon: timedelta,
    now: datetime,
    migrated_at: datetime | None = None,
    creator_initial_tokens: Decimal | None = None,
    rules: RulesV1 = RULES_V1,
    token_id: int | None = None,
    coverage: Coverage | None = None,
) -> OutcomeResult:
    """Метка исхода v1 для одной точки отсчёта и одного горизонта.

    Горизонт *решён* (можно ставить окончательную метку), когда он закончился (``now ≥ reference_time +
    horizon``) **и** данные на нём полны (``coverage`` is None или ``coverage.complete_for(end)``). Пока горизонт
    не решён, результат — ``pending`` с ``metrics.pending_reason`` = ``horizon_not_finished`` |
    ``data_incomplete``; промежуточные величины при этом всё равно считаются по тому, что есть. При заданном
    ``coverage`` в ``metrics.coverage`` пишутся ``synced_until``, ``complete`` и ``truncated``.

    Проверки в порядке документа, срабатывает первая:

    1. ``no_data`` — горизонт решён, а данных нет: нет опорной точки (см. ``reference_point``), ``ref ≤ 0``,
       или на горизонте нет ни одной точки капитализации после опорной.
    2. ``pending`` — горизонт не решён (не закончился или данные неполны).
    3. ``rug`` — ликвидность упала > 80 % за ≤ 10 минут (``liquidity_crash`` по окну горизонта), **или**
       креатор продал > 50 % своих токенов в первый час после точки отсчёта (``creator_sell_share`` по
       окну ``[reference_time, reference_time + min(1h, horizon)]``), **или** ``end_multiple`` < 0.1 при
       ``max_drawdown`` > 0.9.
    4. ``success`` — ``peak_multiple`` ≥ 3 и ``liquidity_alive`` и ``end_multiple`` ≥ 1.
    5. ``dead`` — не мигрировал (``migrated_at`` отсутствует или позже конца горизонта) и ``end_multiple`` < 0.5.
    6. ``neutral`` — всё остальное.

    Величины: ``peak`` — максимум капитализации по окну (опорная точка входит, поэтому ``peak_multiple`` ≥ 1);
    ``end`` — последняя точка капитализации не позже конца горизонта; ``max_drawdown`` = 1 − min(cap после
    первой точки пика, включительно) / peak. ``liquidity_alive`` — последняя известная ликвидность в окне
    ≥ 50 % максимальной ликвидности в окне и ≥ 5 SOL; None, если ликвидность в окне неизвестна (тогда
    ``success`` невозможен). ``migrated`` — ``migrated_at ≤`` конец горизонта.

    ``creator_initial_tokens`` передаётся только если начальная покупка креатора отсутствует в ``creator_trades``.
    """
    pts = sorted(points, key=lambda p: p.ts)
    trades = list(creator_trades)
    end = reference_time + horizon
    finished = now >= end
    complete = coverage is None or coverage.complete_for(end)
    decided = finished and complete
    migrated = migrated_at is not None and migrated_at <= end
    metrics: dict[str, Any] = {"horizon_end": end, "now": now, "finished": finished}
    if coverage is not None:
        metrics["coverage"] = {
            "synced_until": coverage.synced_until, "complete": complete, "truncated": coverage.truncated,
        }
    if not decided:
        metrics["pending_reason"] = "horizon_not_finished" if not finished else "data_incomplete"

    def result(label: str, **kw: Any) -> OutcomeResult:
        return OutcomeResult(
            token_id=token_id, horizon=horizon, rule_version=rules.rule_version, reference=reference,
            reference_time=reference_time, label=label, migrated=migrated, metrics=_jsonable(metrics), **kw,
        )

    def undecided(reason: str) -> OutcomeResult:
        metrics["no_data_reason"] = reason
        return result(LABEL_NO_DATA if decided else LABEL_PENDING)

    ref_point = reference_point(pts, reference_time, rules)
    if ref_point is None:
        return undecided("no_reference_point")
    ref = ref_point.market_cap_sol
    metrics.update(ref_cap=ref, ref_ts=ref_point.ts, ref_lookahead=ref_point.ts > reference_time)
    if ref <= 0:
        return undecided("reference_cap_not_positive")

    window = [ref_point] + [p for p in pts if ref_point.ts < p.ts <= end]
    caps = [p for p in window if p.market_cap_sol is not None]
    metrics["points_in_window"] = len(window)
    metrics["cap_points_after_ref"] = len(caps) - 1
    if len(caps) < 2:
        return undecided("no_cap_points_on_horizon")

    # Пик, конец, просадка.
    peak_point = max(caps, key=lambda p: p.market_cap_sol)  # при равенстве — первая по времени
    peak = peak_point.market_cap_sol
    end_point = caps[-1]
    end_cap = end_point.market_cap_sol
    min_after_peak = min(p.market_cap_sol for p in caps if p.ts >= peak_point.ts)
    peak_multiple = peak / ref
    end_multiple = end_cap / ref
    max_drawdown = 1 - min_after_peak / peak
    metrics.update(
        peak_cap=peak, peak_ts=peak_point.ts, end_cap=end_cap, end_ts=end_point.ts,
        end_staleness_s=(end - end_point.ts).total_seconds(), min_after_peak=min_after_peak,
    )

    # Ликвидность.
    liq = [p for p in window if p.liquidity_sol is not None]
    liquidity_alive: bool | None = None
    if liq:
        peak_liq = max(p.liquidity_sol for p in liq)
        end_liq = liq[-1].liquidity_sol
        liquidity_alive = end_liq >= peak_liq * rules.liquidity_alive_share and end_liq >= rules.liquidity_alive_min_sol
        metrics.update(peak_liq=peak_liq, end_liq=end_liq, end_liq_ts=liq[-1].ts)
    else:
        metrics["liquidity_unknown"] = True

    crash = liquidity_crash(window, rules)
    creator_window_end = reference_time + min(rules.rug_creator_window, horizon)
    creator = creator_sell_share(trades, reference_time, creator_window_end, creator_initial_tokens)
    metrics["liquidity_crash"] = crash
    metrics["creator"] = creator
    fast_dump = cap_fast_dump(caps, rules)
    pnd = pump_and_dump(caps, ref, peak_point, rules)
    if rules.fast_dump_drawdown is not None and rules.fast_dump_window is not None:
        metrics["fast_dump"] = fast_dump
    if rules.pnd_min_peak_multiple is not None and rules.pnd_retrace is not None and rules.pnd_window is not None:
        metrics["pump_and_dump"] = pnd

    values = dict(
        peak_multiple=peak_multiple, end_multiple=end_multiple, max_drawdown=max_drawdown,
        liquidity_alive=liquidity_alive,
    )
    if not decided:
        return result(LABEL_PENDING, **values)

    rug_reasons = []
    if crash is not None:
        rug_reasons.append("liquidity_crash")
    if creator["share"] is not None and creator["share"] > rules.rug_creator_sell_share:
        rug_reasons.append("creator_sold")
    if end_multiple < rules.rug_end_multiple and max_drawdown > rules.rug_max_drawdown:
        rug_reasons.append("collapse")
    if fast_dump is not None:
        rug_reasons.append("fast_dump")
    if pnd is not None:
        rug_reasons.append("pump_and_dump")
    metrics["rug_reasons"] = rug_reasons
    if rug_reasons:
        return result(LABEL_RUG, **values)
    if (
        peak_multiple >= rules.success_peak_multiple
        and liquidity_alive is True
        and end_multiple >= rules.success_end_multiple
    ):
        return result(LABEL_SUCCESS, **values)
    if not migrated and end_multiple < rules.dead_end_multiple:
        return result(LABEL_DEAD, **values)
    return result(LABEL_NEUTRAL, **values)


# ───────────── Слой БД ─────────────


@dataclass
class TokenData:
    """Всё, что нужно для разметки одного токена, загруженное одним проходом."""

    token_id: int
    created_at: datetime | None
    migrated_at: datetime | None
    first_mention_at: datetime | None
    points: list[SeriesPoint]
    creator_trades: list[CreatorTrade]
    coverage: Coverage = field(default_factory=Coverage)
    despiked: int = 0  # сколько выбросов убрано из ряда (``despike``)


async def load_token_data(conn: asyncpg.Connection, token_id: int) -> TokenData | None:
    """Ряд капитализации/ликвидности (снимки + сделки + стартовая капитализация токена), сделки креатора
    и полнота загрузки (``trades_synced_until``, ``trades_backfill_truncated``)."""
    token = await conn.fetchrow(
        """SELECT t.created_at, t.migrated_at, t.initial_market_cap_sol, w.address AS creator_address,
                  t.trades_synced_until, t.trades_backfill_truncated
           FROM tokens t LEFT JOIN wallets w ON w.id = t.creator_wallet_id
           WHERE t.id = $1""",
        token_id,
    )
    if token is None:
        return None
    snapshots = await conn.fetch(
        "SELECT ts, market_cap_sol, liquidity_sol FROM token_snapshots WHERE token_id = $1 ORDER BY ts",
        token_id,
    )
    trade_caps = await conn.fetch(
        "SELECT ts, market_cap_sol FROM trades WHERE token_id = $1 AND market_cap_sol IS NOT NULL ORDER BY ts",
        token_id,
    )
    points = [SeriesPoint(r["ts"], r["market_cap_sol"], r["liquidity_sol"]) for r in snapshots]
    points += [SeriesPoint(r["ts"], r["market_cap_sol"]) for r in trade_caps]
    if token["created_at"] is not None and token["initial_market_cap_sol"] is not None and not any(
        p.ts == token["created_at"] and p.market_cap_sol is not None for p in points
    ):
        points.append(SeriesPoint(token["created_at"], token["initial_market_cap_sol"]))
    points.sort(key=lambda p: p.ts)
    points, despiked = despike(points)

    creator_trades: list[CreatorTrade] = []
    if token["creator_address"] is not None:
        rows = await conn.fetch(
            """SELECT ts, side, token_amount, sol_amount FROM trades
               WHERE token_id = $1 AND wallet_address = $2 ORDER BY ts""",
            token_id, token["creator_address"],
        )
        creator_trades = [CreatorTrade(r["ts"], r["side"], r["token_amount"], r["sol_amount"]) for r in rows]
    first_mention_at = await conn.fetchval("SELECT min(tweet_time) FROM mentions WHERE token_id = $1", token_id)
    return TokenData(
        token_id=token_id, created_at=token["created_at"], migrated_at=token["migrated_at"],
        first_mention_at=first_mention_at, points=points, creator_trades=creator_trades,
        coverage=Coverage(token["trades_synced_until"], bool(token["trades_backfill_truncated"])),
        despiked=despiked,
    )


def references_for(data: TokenData) -> list[tuple[str, datetime]]:
    """Точки отсчёта токена: creation (если известно created_at) и first_mention (если есть упоминания)."""
    refs = []
    if data.created_at is not None:
        refs.append((REF_CREATION, data.created_at))
    if data.first_mention_at is not None:
        refs.append((REF_FIRST_MENTION, data.first_mention_at))
    return refs


async def upsert_outcome(conn: asyncpg.Connection, outcome: OutcomeResult, computed_at: datetime) -> None:
    """Пишет строку в token_outcomes; при конфликте по (token_id, horizon, rule_version, reference) заменяет."""
    await conn.execute(
        """INSERT INTO token_outcomes (token_id, horizon, rule_version, reference, reference_time, label,
                                       peak_multiple, end_multiple, max_drawdown, liquidity_alive, migrated,
                                       metrics, computed_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13)
           ON CONFLICT (token_id, horizon, rule_version, reference) DO UPDATE SET
               reference_time = EXCLUDED.reference_time,
               label = EXCLUDED.label,
               peak_multiple = EXCLUDED.peak_multiple,
               end_multiple = EXCLUDED.end_multiple,
               max_drawdown = EXCLUDED.max_drawdown,
               liquidity_alive = EXCLUDED.liquidity_alive,
               migrated = EXCLUDED.migrated,
               metrics = EXCLUDED.metrics,
               computed_at = EXCLUDED.computed_at""",
        outcome.token_id, outcome.horizon, outcome.rule_version, outcome.reference, outcome.reference_time,
        outcome.label, outcome.peak_multiple, outcome.end_multiple, outcome.max_drawdown,
        outcome.liquidity_alive, outcome.migrated, json.dumps(outcome.metrics), computed_at,
    )


async def compute_token_outcomes(
    db: asyncpg.Pool | asyncpg.Connection,
    token_id: int,
    now: datetime | None = None,
    horizons: Sequence[timedelta] = DEFAULT_HORIZONS,
    rules: RulesV1 = RULES_V1,
) -> list[OutcomeResult]:
    """Считает и сохраняет исходы токена для всех точек отсчёта и горизонтов.

    Ряды загружаются один раз. Точка ``creation`` пропускается, если у токена нет ``created_at``;
    ``first_mention`` — если нет упоминаний с этим ``token_id``. Полнота данных берётся из
    ``tokens.trades_synced_until`` / ``trades_backfill_truncated``: горизонты, до конца которых сделки ещё не
    загружены (или ``trades_synced_until`` NULL), получают ``pending``. Строки пишутся в одной транзакции,
    ``computed_at`` = ``now``. Возвращает записанные результаты (пустой список, если токена нет).
    """
    if isinstance(db, asyncpg.Pool):
        async with db.acquire() as conn:
            return await compute_token_outcomes(conn, token_id, now, horizons, rules)
    now = now or utc_now()
    data = await load_token_data(db, token_id)
    if data is None:
        return []
    results = []
    for reference, reference_time in references_for(data):
        for horizon in horizons:
            outcome = label_outcome(
                data.points, data.creator_trades, reference=reference, reference_time=reference_time,
                horizon=horizon, now=now, migrated_at=data.migrated_at, rules=rules, token_id=token_id,
                coverage=data.coverage,
            )
            if data.despiked:
                outcome.metrics["despiked_points"] = data.despiked
            results.append(outcome)
    async with db.transaction():
        for outcome in results:
            await upsert_outcome(db, outcome, now)
    return results


async def due_token_ids(
    db: asyncpg.Pool | asyncpg.Connection,
    now: datetime,
    limit: int,
    horizons: Sequence[timedelta] = DEFAULT_HORIZONS,
    rules: RulesV1 = RULES_V1,
) -> list[int]:
    """Токены, которым нужна разметка. Рассматриваются только токены с загруженными сделками
    (``trades_synced_until IS NOT NULL``). Токен должен, если у него нет строки для какой-то пары
    (точка отсчёта, горизонт) — тогда пишется хотя бы ``pending`` с отметкой полноты — или есть строка
    ``pending``, конец горизонта которой не позже ``least(trades_synced_until, now)``: данные до конца
    горизонта уже загружены и сам горизонт закончился, пересчёт даст окончательную метку. ``pending``,
    ждущий данных (конец горизонта позже ``trades_synced_until``) или окончания горизонта, не должен —
    пересчёт дал бы снова ``pending``. Ещё токен должен, если точка отсчёта сдвинулась (пришло более раннее
    упоминание или поменялся ``created_at``): ``reference_time`` строки не совпадает с актуальным."""
    rows = await db.fetch(
        """WITH horizons AS (SELECT unnest($1::interval[]) AS horizon),
                refs AS (
                    SELECT t.id AS token_id, 'creation' AS reference, t.created_at AS reference_time
                    FROM tokens t WHERE t.created_at IS NOT NULL
                    UNION ALL
                    SELECT m.token_id, 'first_mention', min(m.tweet_time) FROM mentions m
                    WHERE m.token_id IS NOT NULL GROUP BY m.token_id
                )
           SELECT DISTINCT r.token_id
           FROM refs r
           JOIN tokens t ON t.id = r.token_id AND t.trades_synced_until IS NOT NULL
           CROSS JOIN horizons h
           LEFT JOIN token_outcomes o
                  ON o.token_id = r.token_id AND o.horizon = h.horizon
                 AND o.reference = r.reference AND o.rule_version = $2
           WHERE o.token_id IS NULL
              OR o.reference_time IS DISTINCT FROM r.reference_time
              OR (o.label = 'pending' AND o.reference_time + o.horizon <= least(t.trades_synced_until, $3))
           ORDER BY r.token_id
           LIMIT $4""",
        list(horizons), rules.rule_version, now, limit,
    )
    return [r["token_id"] for r in rows]


async def compute_due_outcomes(
    pool: asyncpg.Pool,
    now: datetime | None = None,
    limit: int = 500,
    horizons: Sequence[timedelta] = DEFAULT_HORIZONS,
    rules: RulesV1 = RULES_V1,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Размечает до ``limit`` токенов с загруженными сделками, у которых исходы отсутствуют или ``pending``
    на горизонте, закончившемся и покрытом данными (см. ``due_token_ids``).

    Токены обрабатываются пачками по ``batch_size`` на одном соединении; ошибка одного токена логируется и
    не останавливает остальные. Возвращает число обработанных токенов.
    """
    now = now or utc_now()
    ids = await due_token_ids(pool, now, limit, horizons, rules)
    done = 0
    for start in range(0, len(ids), batch_size):
        async with pool.acquire() as conn:
            for token_id in ids[start:start + batch_size]:
                try:
                    await compute_token_outcomes(conn, token_id, now, horizons, rules)
                    done += 1
                except Exception:  # noqa: BLE001 — один плохой токен не должен останавливать пачку
                    log.exception("outcomes: token %s failed", token_id)
    return done


async def recompute_all(db, rulesets=RULESETS, limit: int | None = None, now: datetime | None = None) -> int:
    """Recompute synced tokens, including those with final outcomes already stored."""
    now = now or utc_now()
    rows = await db.fetch(
        "SELECT id FROM tokens WHERE trades_synced_until IS NOT NULL ORDER BY id LIMIT $1", limit,
    )
    count = 0
    for row in rows:
        for rules in rulesets:
            count += len(await compute_token_outcomes(db, row["id"], now=now, rules=rules))
    return count


async def outcome_summary(db, rulesets=RULESETS) -> list[dict[str, Any]]:
    """Counts for the selected versions across all stored outcomes."""
    return [dict(r) for r in await db.fetch(
        """SELECT rule_version, reference, horizon, label, count(*) AS count
           FROM token_outcomes WHERE rule_version = ANY($1::text[])
           GROUP BY rule_version, reference, horizon, label
           ORDER BY rule_version, reference, horizon, label""",
        [r.rule_version for r in rulesets],
    )]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Recompute and summarize token outcomes.")
    parser.add_argument("--rule-version", choices=("v1", "v2", "all"), default="all")
    parser.add_argument("--dsn")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be nonnegative")
    return args


async def _main(args):
    from kairos.config import get_settings
    from kairos.report import _table

    rulesets = tuple(r for r in RULESETS if args.rule_version in ("all", r.rule_version))
    conn = await asyncpg.connect(args.dsn or get_settings().database_url)
    try:
        await recompute_all(conn, rulesets, args.limit)
        rows = await outcome_summary(conn, rulesets)
        columns = ("rule_version", "reference", "horizon", "label", "count")
        print(_table(columns, [[r[c] for c in columns] for r in rows]))
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(_main(parse_args()))

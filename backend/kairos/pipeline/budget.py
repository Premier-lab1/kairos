"""API credit accounting and UTC calendar-day budget checks."""

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def record_call(
    db: asyncpg.Pool | asyncpg.Connection,
    provider: str,
    endpoint: str,
    credits: int | float | Decimal,
    *,
    ok: bool,
    http_status: int | None = None,
    note: str | None = None,
    ts: datetime | None = None,
) -> None:
    await db.execute(
        """INSERT INTO api_calls (provider, endpoint, credits, ok, http_status, note, ts)
           VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7::timestamptz, now()))""",
        provider, endpoint, Decimal(str(credits)), ok, http_status, note, ts,
    )


async def spent_since(
    db: asyncpg.Pool | asyncpg.Connection, provider: str, since: datetime,
) -> Decimal:
    return await db.fetchval(
        """SELECT COALESCE(SUM(credits), 0) FROM api_calls
           WHERE provider = $1 AND ts >= $2""",
        provider, since,
    )


class CreditBudget:
    """Дневной бюджет кредитов провайдера (сутки UTC).

    ``pace=True`` — расход по равномерному графику: к моменту ``t`` доступно
    ``daily_limit · min(1, (прошло с полуночи + burst) / сутки)``, где ``burst`` — запас вперёд (1 час).
    Без графика 18.10.2026 весь дневной бюджет Helius ушёл за первые 8 минут, а Frontrun — за 3 часа,
    и остаток суток сбор шиллеров и сделок стоял. Задача, упёршаяся в график, откладывается на
    ``retry`` (15 минут), а не до полуночи; если исчерпан весь дневной лимит — до полуночи.

    ``shares`` — гарантированные доли дневного лимита по видам задач (``{kind: доля}``, сумма ≤ 1).
    Без долей дорогие и редкие виды голодали: поиск CA (2 кредита, приоритет 10) съедал каждый кредит,
    как только график его отпускал, и до smart followers (3), истории CA (50) и кошельков (400) очередь
    не доходила. С долями вид ``kind`` может потратить ``allowed_now() − потрачено всеми − Σ резервов
    других видов``, где резерв вида = его неизрасходованная доля на текущий момент графика
    (``daily_limit · share · прошедшая часть суток − потрачено этим видом``, не меньше 0). Резерв держится,
    только пока у вида есть ждущие или выполняемые задачи — доля вида без работы уходит остальным, а
    остаток лимита сверх всех долей общий. Расход по видам считается по ``api_calls.endpoint`` (туда пишут
    ``job.kind``). Дорогой вид копит свою долю по графику: при 15 % от 3000 кошельки (400) дойдут до
    ~20:00 UTC — одна проверка в сутки.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        provider: str,
        daily_limit: int,
        clock: Callable[[], datetime] = utc_now,
        *,
        pace: bool = False,
        burst: timedelta = timedelta(hours=1),
        retry: timedelta = timedelta(minutes=15),
        shares: Mapping[str, float] | None = None,
    ):
        self.pool = pool
        self.provider = provider
        self.daily_limit = daily_limit
        self.clock = clock
        self.pace = pace
        self.burst = burst
        self.retry = retry
        self.shares = {kind: Decimal(str(share)) for kind, share in (shares or {}).items()}
        if any(share < 0 for share in self.shares.values()) or sum(self.shares.values(), Decimal(0)) > 1:
            raise ValueError("budget shares must be nonnegative and sum to at most 1")
        self._spent_cache: Decimal | None = None

    def _midnight(self) -> datetime:
        return self.clock().astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    async def spent_today(self) -> Decimal:
        spent = await spent_since(self.pool, self.provider, self._midnight())
        self._spent_cache = spent
        return spent

    async def spent_today_by_kind(self) -> dict[str, Decimal]:
        """Расход за сутки по ``api_calls.endpoint`` (виду задачи); обновляет и общий кэш расхода."""
        rows = await self.pool.fetch(
            """SELECT endpoint, COALESCE(SUM(credits), 0) AS spent FROM api_calls
               WHERE provider = $1 AND ts >= $2 GROUP BY endpoint""",
            self.provider, self._midnight())
        by_kind = {row["endpoint"]: row["spent"] for row in rows}
        self._spent_cache = sum(by_kind.values(), Decimal(0))
        return by_kind

    def _pace_share(self) -> Decimal:
        if not self.pace:
            return Decimal(1)
        elapsed = self.clock() - self._midnight() + self.burst
        return min(Decimal(1), Decimal(str(elapsed.total_seconds() / 86400)))

    def allowed_now(self, kind: str | None = None) -> Decimal:
        """Сколько кредитов можно потратить с начала суток к текущему моменту: всему провайдеру или
        в рамках гарантированной доли вида ``kind`` (0, если доля виду не назначена)."""
        share = Decimal(1) if kind is None else self.shares.get(kind, Decimal(0))
        return (Decimal(self.daily_limit) * share * self._pace_share()).quantize(Decimal(1))

    async def _held_kinds(self, kinds: list[str]) -> set[str]:
        """Виды, у которых есть работа (ждущие или выполняемые задачи): их резерв нельзя трогать."""
        if not kinds:
            return set()
        rows = await self.pool.fetch(
            """SELECT DISTINCT kind FROM jobs
               WHERE kind = ANY($1::text[]) AND status IN ('pending', 'running')""", kinds)
        return {row["kind"] for row in rows}

    async def remaining(self, kind: str | None = None) -> Decimal:
        """Остаток для вида ``kind`` с учётом резервов других видов; без вида или без долей — остаток
        всего провайдера."""
        if kind is None or not self.shares:
            return max(Decimal(0), self.allowed_now() - await self.spent_today())
        by_kind = await self.spent_today_by_kind()
        left = self.allowed_now() - sum(by_kind.values(), Decimal(0))
        held = await self._held_kinds([other for other in self.shares if other != kind])
        reserved = sum((max(Decimal(0), self.allowed_now(other) - by_kind.get(other, Decimal(0))) for other in held),
                       Decimal(0))
        return max(Decimal(0), left - reserved)

    async def can_spend(self, cost: int, kind: str | None = None) -> bool:
        return cost <= await self.remaining(kind)

    def next_reset(self) -> datetime:
        """Когда повторить задачу, упёршуюся в бюджет: при графике — через ``retry`` (но не позже полуночи),
        если дневной лимит ещё не исчерпан целиком; иначе — в следующую полночь UTC."""
        midnight = self._midnight() + timedelta(days=1)
        if self.pace and (self._spent_cache is None or self._spent_cache < self.daily_limit):
            return min(midnight, self.clock() + self.retry)
        return midnight

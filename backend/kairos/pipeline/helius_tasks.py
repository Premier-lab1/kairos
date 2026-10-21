"""Задачи конвейера Helius: догрузка сделок токена до контрольных точек исхода.

Поток: токен (PumpPortal или история CA шиллера) → ``schedule_backfills`` ставит задачи ``helius_backfill`` на
каждую контрольную точку «точка отсчёта + горизонт + 5 минут» (точки отсчёта: создание токена и первое
упоминание; горизонты 1 ч / 24 ч / 7 д) → ``HeliusTasks.run_job`` качает сделки с ``tokens.trades_synced_until``
до точки (``collectors.helius_trades.backfill_token``), двигает ``trades_synced_until`` и пересчитывает исходы.

Кредиты: ``backfill_token`` сам пишет каждую страницу в ``api_calls`` (provider='helius') с фактически
списанными кредитами, поэтому здесь они повторно не записываются - иначе дневной бюджет считался бы дважды.
Перед запуском проверяется остаток дневного бюджета и число страниц ограничивается им (10 кредитов на страницу).
Ключ API нигде не логируется: исключения клиента уже очищены от ключа.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from kairos import outcomes
from kairos.collectors.helius_trades import BackfillResult, TradeStore, backfill_token
from kairos.sources.creation import find_creation
from kairos.config import Settings
from kairos.pipeline import jobs
from kairos.pipeline.budget import CreditBudget
from kairos.sources.helius import HeliusClient, HeliusError

log = logging.getLogger(__name__)

PROVIDER = "helius"
KIND_BACKFILL = "helius_backfill"
KIND_CREATION = "helius_creation"
KINDS = (KIND_BACKFILL, KIND_CREATION)
CREATION_FLAG = "helius_creation_checked"

CHECKPOINT_SLACK = timedelta(minutes=5)       # запас после конца горизонта: блоки финализируются с задержкой
# горизонт → приоритет задачи: короткие горизонты раньше (по ним скорее появляются метки)
HORIZON_PRIORITY: tuple[tuple[timedelta, int], ...] = (
    (timedelta(hours=1), 5), (timedelta(hours=24), 3), (timedelta(days=7), 1),
)
INITIAL_PRIORITY = 4                          # поиск created_at: открывает точки отсчёта creation
RETRY_ON_ERROR = timedelta(minutes=10)
RETRY_ON_AUTH_ERROR = timedelta(minutes=30)   # 401/403: ключ неверен или без доступа - повторять часто нет смысла
INITIAL_HORIZON = timedelta(days=7)           # задача initial качает историю не дальше first_mention + 7 д + запас
PROBE_PAGES = 2                               # пробный проход по токену без created_at: есть ли вообще сделки Pump
NO_PUMP_FLAG = "helius_no_pump_trades"
COST_CAP_FLAG = "helius_cost_capped"  # токен выбрал helius_max_credits_per_token, больше не качаем
# Заведомо не мем-токены: в истории шиллеров встречаются как «CA», но сделок Pump.fun по ним нет.
EXCLUDED_MINTS = (
    "So11111111111111111111111111111111111111112",   # wrapped SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
)
CREDITS_PER_PAGE = 10                         # getTransactionsForAddress: 10 кредитов за страницу в 100 транзакций
MIN_CREDITS = CREDITS_PER_PAGE                # меньше одной страницы не запускаем
SOURCE_HISTORY = "frontrun_ca_history"

# Mint Solana - 32 байта в base58: 32-44 символа алфавита без 0, O, I, l.
_BASE58_RE = "^[1-9A-HJ-NP-Za-km-z]{32,44}$"

_EXHAUSTED = ("exhausted", "fallback_exhausted")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime:
    ts = datetime.fromisoformat(str(value))
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def _count(status: str) -> int:
    # asyncpg возвращает тег команды: 'INSERT 0 5' / 'UPDATE 3'
    return int(status.rsplit(" ", 1)[-1])


# ───────────── Токены из истории шиллеров ─────────────


async def register_history_tokens(pool: asyncpg.Pool) -> int:
    """Создаёт минимальные строки tokens для CA из mentions, которых нет в базе, и привязывает к ним упоминания.

    Берутся только упоминания сети SOLANA с правдоподобным mint (base58, 32-44 символа); EVM и мусор
    пропускаются, как и заведомо не мем-токены (``EXCLUDED_MINTS``: wSOL, USDC, USDT).
    Возвращает число созданных токенов.
    """
    async with pool.acquire() as conn, conn.transaction():
        created = await conn.fetchval(
            f"""WITH new AS (
                    INSERT INTO tokens (chain, mint, source)
                    SELECT DISTINCT 'SOLANA', m.ca, '{SOURCE_HISTORY}' FROM mentions m
                    WHERE m.chain = 'SOLANA' AND m.token_id IS NULL AND m.ca ~ '{_BASE58_RE}'
                      AND m.ca <> ALL($1::text[])
                    ON CONFLICT (chain, mint) DO NOTHING RETURNING id)
                SELECT count(*) FROM new""", list(EXCLUDED_MINTS))
        await conn.execute(
            f"""UPDATE mentions m SET token_id = t.id
                FROM tokens t
                WHERE m.token_id IS NULL AND m.chain = 'SOLANA' AND m.ca ~ '{_BASE58_RE}'
                  AND t.chain = 'SOLANA' AND t.mint = m.ca
                  AND m.ca <> ALL($1::text[])""", list(EXCLUDED_MINTS))
    return int(created)


# ───────────── Планировщик ─────────────


def _checkpoints(created_at: datetime | None, first_mention: datetime | None) -> dict[datetime, int]:
    """Контрольные точки токена → приоритет (при совпадении точек - больший)."""
    out: dict[datetime, int] = {}
    for ref in (created_at, first_mention):
        if ref is None:
            continue
        for horizon, priority in HORIZON_PRIORITY:
            point = ref + horizon + CHECKPOINT_SLACK
            out[point] = max(out.get(point, 0), priority)
    return out


_CANDIDATES_SQL = """
SELECT t.id, t.mint, t.created_at, t.trades_synced_until AS synced, fm.first_mention
FROM tokens t
LEFT JOIN LATERAL (SELECT min(m.tweet_time) AS first_mention FROM mentions m WHERE m.token_id = t.id) fm ON true
WHERE t.chain = 'SOLANA' AND t.id > $1
  AND (t.migrated_at IS NOT NULL OR fm.first_mention IS NOT NULL)
  AND t.metadata->>'helius_no_pump_trades' IS DISTINCT FROM 'true'
  AND t.metadata->>'helius_cost_capped' IS DISTINCT FROM 'true'
  AND (
      (t.created_at IS NULL AND t.trades_synced_until IS NULL)
      OR EXISTS (
          SELECT 1 FROM unnest(ARRAY[t.created_at, fm.first_mention]) AS r(ts)
          CROSS JOIN unnest($3::interval[]) AS h(d)
          WHERE r.ts IS NOT NULL AND r.ts + h.d + $4::interval <= $5
            AND (t.trades_synced_until IS NULL OR r.ts + h.d + $4::interval > t.trades_synced_until)))
ORDER BY t.id
LIMIT $2
"""


async def schedule_backfills(pool: asyncpg.Pool, settings: Settings, now: datetime, limit: int = 500) -> int:
    """Ставит задачи догрузки сделок. Возвращает число новых задач (за вызов не больше ``limit``).

    Интересные токены: SOLANA и (есть упоминание или токен мигрировал). Для каждой контрольной точки,
    уже наступившей и не покрытой ``trades_synced_until``, - задача ``<mint>:<эпоха в минутах>``.
    Токен без известного created_at и без покрытия получает одну задачу ``<mint>:initial`` (until = now до минуты,
    а при наличии упоминания - не позже first_mention + 7 д + 5 мин), чтобы найти историю.
    Токены с признаком ``helius_no_pump_trades`` (пробный проход не нашёл сделок Pump) пропускаются. Повторный вызов новых задач не создаёт (ключи задач уникальны).
    """
    del settings  # параметры бюджета и страниц нужны воркеру, планировщику - нет
    await register_history_tokens(pool)
    created = 0
    after_id = 0
    chunk = max(limit, 200)
    horizons = [h for h, _ in HORIZON_PRIORITY]
    now_minute = now.replace(second=0, microsecond=0)
    while created < limit:
        rows = await pool.fetch(_CANDIDATES_SQL, after_id, chunk, horizons, CHECKPOINT_SLACK, now)
        for row in rows:
            after_id = row["id"]
            synced = row["synced"]
            mint = row["mint"]
            wanted: list[tuple[str, dict, int]] = []
            if row["created_at"] is None and synced is None:
                initial_until = now_minute
                if row["first_mention"] is not None:
                    initial_until = min(initial_until, row["first_mention"] + INITIAL_HORIZON + CHECKPOINT_SLACK)
                wanted.append((f"{mint}:initial", {"mint": mint, "until": initial_until.isoformat()}, INITIAL_PRIORITY))
            for point, priority in sorted(_checkpoints(row["created_at"], row["first_mention"]).items()):
                if point <= now and (synced is None or point > synced):
                    wanted.append((f"{mint}:{int(point.timestamp() // 60)}",
                                   {"mint": mint, "until": point.isoformat()}, priority))
            for key, payload, priority in wanted:
                if created >= limit:
                    break
                if await jobs.enqueue(pool, KIND_BACKFILL, key, payload, priority=priority, run_after=now):
                    created += 1
            if created >= limit:
                break
        if len(rows) < chunk:
            break
    created += await schedule_creation_checks(pool, now)
    return created


async def schedule_creation_checks(pool: asyncpg.Pool, now: datetime, limit: int = 50) -> int:
    rows = await pool.fetch(
        """SELECT t.mint FROM tokens t
           WHERE t.chain = 'SOLANA' AND t.name IS NULL AND t.trades_synced_until IS NOT NULL
             AND EXISTS (SELECT 1 FROM mentions m WHERE m.token_id = t.id)
             AND t.metadata->>'helius_creation_checked' IS DISTINCT FROM 'true'
             AND t.metadata->>'helius_no_pump_trades' IS DISTINCT FROM 'true'
             AND t.metadata->>'helius_cost_capped' IS DISTINCT FROM 'true'
             AND t.mint <> ALL($1::text[])
             AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind = $2 AND j.key = t.mint || ':creation')
           ORDER BY t.id LIMIT $3""", list(EXCLUDED_MINTS), KIND_CREATION, limit)
    created = 0
    for row in rows:
        mint = row["mint"]
        if await jobs.enqueue(pool, KIND_CREATION, f"{mint}:creation", {"mint": mint}, priority=0, run_after=now):
            created += 1
    return created


# ───────────── Воркер ─────────────


class HeliusTasks:
    def __init__(self, pool: asyncpg.Pool, client: HeliusClient, budget: CreditBudget,
                 settings: Settings, clock: Callable[[], datetime] = utc_now):
        self.pool = pool
        self.client = client
        self.budget = budget
        self.settings = settings
        self.clock = clock

    async def run_job(self, job: jobs.Job) -> None:
        if job.kind == KIND_CREATION:
            await self._creation_job(job)
            return
        mint = job.payload.get("mint")
        try:
            until_req = _parse_ts(job.payload["until"])
        except (KeyError, ValueError, TypeError):
            until_req = None
        if not mint or until_req is None:
            await jobs.skip(self.pool, job.id, "bad payload: mint and until are required")
            return
        token = await self.pool.fetchrow(
            """SELECT id, created_at, trades_synced_until,
                      coalesce((metadata->>'helius_no_pump_trades')::boolean, false) AS no_pump,
                      coalesce((metadata->>'helius_cost_capped')::boolean, false) AS cost_capped
               FROM tokens WHERE chain = 'SOLANA' AND mint = $1""", mint)
        if token is None:
            await jobs.skip(self.pool, job.id, "unknown token")
            return
        token_id, since = token["id"], token["trades_synced_until"]
        if token["no_pump"]:
            await jobs.complete(self.pool, job.id, {"no_pump_trades": True, "noop": True})
            return
        if token["cost_capped"]:
            await jobs.complete(self.pool, job.id, {"cost_capped": True, "noop": True})
            return
        cap = self.settings.helius_max_credits_per_token
        token_spent = await self._token_credits(mint)
        if token_spent >= cap:
            await self._set_flag(token_id, COST_CAP_FLAG)
            log.warning("helius: token %s reached %s credits, backfill stopped", mint, token_spent)
            await jobs.complete(self.pool, job.id, {"cost_capped": True, "noop": True, "token_credits": int(token_spent)})
            return
        until = min(until_req, self.clock())
        if since is not None and until <= since:
            await jobs.complete(self.pool, job.id, {"noop": True})
            return
        remaining = await self.budget.remaining()
        if remaining < MIN_CREDITS:
            await jobs.reschedule(self.pool, job.id, self.budget.next_reset(), "daily credit budget exhausted")
            return
        max_pages = max(1, min(self.settings.helius_backfill_max_pages, int(remaining // CREDITS_PER_PAGE),
                               int((cap - token_spent) // CREDITS_PER_PAGE)))
        # токен без created_at и без покрытия: сначала короткая проба - есть ли у него сделки Pump вообще
        probe = token["created_at"] is None and since is None
        if probe:
            max_pages = min(max_pages, PROBE_PAGES)
        result = BackfillResult()
        try:
            await backfill_token(self.pool, self.client, mint, until, since=since, max_pages=max_pages, result=result)
        except HeliusError as exc:
            note = f"{exc.code if exc.code is not None else ''} {exc.message}".strip()
            await self._keep_progress(token_id, since, result)
            status = exc.http_status
            if status in (401, 403):
                log.error("%s %s: http %s - check HELIUS_API_KEY (%s)", job.kind, job.key, status, note)
                await jobs.fail(self.pool, job.id, f"http {status}: {note} (check HELIUS_API_KEY)",
                                retry_in=RETRY_ON_AUTH_ERROR)
                return
            if status is not None and 400 <= status < 500 and status != 429:
                await jobs.skip(self.pool, job.id, f"http {status}: {note}"[:500])
            else:
                await jobs.fail(self.pool, job.id, f"http {status}: {note}", retry_in=RETRY_ON_ERROR)
            log.warning("%s %s failed: http %s %s", job.kind, job.key, status, note)
            return
        except Exception as exc:  # noqa: BLE001 - сбой БД/разбора не должен оставлять задачу в running
            await self._keep_progress(token_id, since, result)
            await jobs.fail(self.pool, job.id, f"{type(exc).__name__}: {exc}", retry_in=RETRY_ON_ERROR)
            log.exception("%s %s crashed", job.kind, job.key)
            return

        if probe:
            await self._set_flag(token_id, CREATION_FLAG)
        if probe and result.pump_trades == 0:
            await self.pool.execute(
                """UPDATE tokens SET metadata = (CASE WHEN jsonb_typeof(metadata) = 'object' THEN metadata
                                                      ELSE '{}'::jsonb END) || '{"helius_no_pump_trades": true}'::jsonb
                   WHERE id = $1""", token_id)
            await jobs.complete(self.pool, job.id, {
                "no_pump_trades": True, "credits": result.credits, "pages": result.pages,
                "transactions": result.transactions, "stopped": result.stopped})
            return
        exhausted = result.stopped in _EXHAUSTED
        continuation = False
        if exhausted:
            synced_to: datetime | None = until
        else:
            last = result.last_ts
            advanced = last is not None and (since is None or last > since)
            synced_to = last if advanced else None
            if advanced:
                key = f"{mint}:cont:{int(last.timestamp())}"
                continuation = await jobs.enqueue(
                    self.pool, KIND_BACKFILL, key, {"mint": mint, "until": until_req.isoformat()},
                    priority=job.priority, run_after=self.clock())
        # created_at неизвестен и история качалась с самого начала: первая сделка - момент создания
        discover_created = token["created_at"] is None and since is None and result.creation is None
        await self._store_coverage(token_id, synced_to, truncated=not exhausted, discover_created=discover_created)

        n_outcomes: int | None = None
        try:
            n_outcomes = 0
            for rules in outcomes.RULESETS:
                n_outcomes += len(await outcomes.compute_token_outcomes(self.pool, token_id, now=self.clock(), rules=rules))
        except Exception:  # noqa: BLE001 - пересчёт исходов повторит compute_due_outcomes
            log.exception("outcomes for token %s failed", token_id)
        await jobs.complete(self.pool, job.id, {
            "trades": result.trades, "snapshots": result.snapshots, "credits": result.credits,
            "pages": result.pages, "transactions": result.transactions, "stopped": result.stopped,
            "exhausted": exhausted, "truncated": not exhausted, "continuation": continuation,
            "last_ts": result.last_ts.isoformat() if result.last_ts else None, "last_token": result.last_token,
            "synced_until": synced_to.isoformat() if synced_to else None, "outcomes": n_outcomes,
        })

    # ───────────── потолок на токен ─────────────

    async def _creation_job(self, job: jobs.Job) -> None:
        mint = job.payload.get("mint")
        if not isinstance(mint, str) or not mint:
            await jobs.skip(self.pool, job.id, "bad payload: mint is required")
            return
        token_id = await self.pool.fetchval("SELECT id FROM tokens WHERE chain = 'SOLANA' AND mint = $1", mint)
        if token_id is None:
            await jobs.skip(self.pool, job.id, "unknown token")
            return
        if await self.budget.remaining() < MIN_CREDITS:
            await jobs.reschedule(self.pool, job.id, self.budget.next_reset(), "daily credit budget exhausted")
            return
        store = TradeStore(self.pool)
        c0 = self.client.credits_spent
        try:
            try:
                page = await self.client.get_transactions_for_address(mint, sort_order="asc", limit=20)
            except HeliusError as exc:
                await store.log_call("getTransactionsForAddress", self.client.credits_spent - c0,
                                     False, exc.http_status, f"creation mint={mint}")
                raise
            await store.log_call("getTransactionsForAddress", self.client.credits_spent - c0,
                                 True, 200, f"creation mint={mint}")
            creation = find_creation(page.items, mint)
            if creation is not None:
                await store.save_creation(token_id, creation)
            await self._set_flag(token_id, CREATION_FLAG)
        except HeliusError as exc:
            status = exc.http_status
            note = f"http {status}: {exc.message}"
            if status in (401, 403):
                await jobs.fail(self.pool, job.id, note, retry_in=RETRY_ON_AUTH_ERROR)
            elif status is not None and 400 <= status < 500 and status != 429:
                await jobs.skip(self.pool, job.id, note[:500])
            else:
                await jobs.fail(self.pool, job.id, note, retry_in=RETRY_ON_ERROR)
            return
        except Exception as exc:
            await jobs.fail(self.pool, job.id, f"{type(exc).__name__}: {exc}", retry_in=RETRY_ON_ERROR)
            log.exception("%s %s crashed", job.kind, job.key)
            return
        await jobs.complete(self.pool, job.id, {"creation": creation is not None, "credits": self.client.credits_spent - c0})

    async def _token_credits(self, mint: str) -> Decimal:
        """Сколько кредитов уже потрачено на токен: по результатам завершённых задач дозагрузки."""
        return await self.pool.fetchval(
            """SELECT coalesce(sum((result->>'credits')::numeric), 0) FROM jobs
               WHERE kind = $1 AND key LIKE $2 AND result ? 'credits'""",
            KIND_BACKFILL, mint.replace("%", "") + ":%")

    async def _set_flag(self, token_id: int, flag: str) -> None:
        await self.pool.execute(
            """UPDATE tokens SET metadata = (CASE WHEN jsonb_typeof(metadata) = 'object' THEN metadata
                                                  ELSE '{}'::jsonb END) || jsonb_build_object($2::text, true)
               WHERE id = $1""", token_id, flag)

    # ───────────── покрытие ─────────────

    async def _store_coverage(self, token_id: int, synced_to: datetime | None, *, truncated: bool,
                              discover_created: bool) -> None:
        await self.pool.execute(
            """UPDATE tokens SET
                   trades_synced_until = GREATEST(trades_synced_until, $2::timestamptz),
                   trades_backfill_truncated = $3,
                   created_at = CASE WHEN $4 THEN COALESCE(created_at, (SELECT min(ts) FROM trades WHERE token_id = $1))
                                     ELSE created_at END
               WHERE id = $1""",
            token_id, synced_to, truncated, discover_created)

    async def _keep_progress(self, token_id: int, since: datetime | None, result: BackfillResult) -> None:
        """После сбоя посреди обхода сохранённые страницы не теряем: сдвигаем покрытие до последней записанной сделки."""
        try:
            last = result.last_ts
            if last is not None and (since is None or last > since):
                await self._store_coverage(token_id, last, truncated=True, discover_created=False)
        except Exception:  # noqa: BLE001
            log.exception("could not save partial progress for token %s", token_id)

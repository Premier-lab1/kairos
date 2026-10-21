"""Конвейер целиком в одном процессе:

- сборщик PumpPortal (новые токены и миграции);
- планировщик: поиск CA во Frontrun, дозагрузка сделок Helius для зашилленных и мигрировавших токенов;
- воркер Frontrun и воркер Helius (у каждого свой лимит запросов и свой дневной бюджет кредитов);
- регулярный расчёт исходов токенов (правила v1 и v2);
- пересчёт сигналов (скорер s1, docs/signals.md) раз в SIGNAL_INTERVAL_S секунд.

Запуск: python -m kairos.pipeline.run [--duration СЕКУНД] [--daily-credits N] [--helius-daily-credits N]
                                      [--no-collector] [--no-helius]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Sequence
from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import asyncpg

from kairos.collectors.pumpportal import TokenStore
from kairos.collectors.pumpportal import run as run_collector
from kairos.config import Settings, get_settings
from kairos.db import create_pool
from kairos.outcomes import compute_due_outcomes
from kairos.pipeline import jobs
from kairos.pipeline.budget import CreditBudget
from kairos.pipeline.frontrun_tasks import KIND_HISTORY, KIND_SEARCH, KIND_SMARTS
from kairos.pipeline.frontrun_tasks import KINDS as FRONTRUN_KINDS
from kairos.pipeline.frontrun_tasks import PROVIDER as FRONTRUN
from kairos.pipeline.frontrun_tasks import FrontrunTasks, schedule_smart_followers
from kairos.pipeline.helius_tasks import KINDS as HELIUS_KINDS
from kairos.pipeline.helius_tasks import PROVIDER as HELIUS
from kairos.pipeline.helius_tasks import HeliusTasks, schedule_backfills
from kairos.pipeline.kol_wallets import (
    FRONTRUN_KINDS as WALLET_FRONTRUN_KINDS, HELIUS_KINDS as WALLET_HELIUS_KINDS,
    KIND_WALLETS, WALLETS_COST, KolWalletTasks, WalletTaskRouter, schedule_kol_wallets, schedule_kol_wallet_polls,
)
from kairos.narratives import classify_pending
from kairos.pipeline.scheduler import link_mentions, schedule_searches
from kairos.sources.frontrun import FrontrunClient
from kairos.sources.helius import HeliusClient

log = logging.getLogger("kairos.pipeline")

SCHEDULE_EVERY = 30.0
OUTCOMES_EVERY = 300.0
IDLE_SLEEP = 2.0
STALE_AFTER = timedelta(minutes=10)


class Tasks(Protocol):
    async def run_job(self, job: jobs.Job) -> None: ...


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def frontrun_shares(settings: Settings) -> dict[str, float]:
    """Гарантированные доли дневного бюджета Frontrun по видам задач (см. CreditBudget.shares);
    поиску CA — всё, что не отдано остальным."""
    shares = {KIND_SMARTS: settings.frontrun_share_smart_followers_pct,
              KIND_WALLETS: settings.frontrun_share_kol_wallets_pct,
              KIND_HISTORY: settings.frontrun_share_ca_history_pct}
    if any(v < 0 for v in shares.values()) or sum(shares.values()) > 100:
        raise ValueError("FRONTRUN_SHARE_*_PCT must be nonnegative and sum to at most 100")
    shares[KIND_SEARCH] = 100 - sum(shares.values())
    return {kind: pct / 100 for kind, pct in shares.items()}


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def scheduler_loop(pool: asyncpg.Pool, settings: Settings, stop: asyncio.Event, helius: bool) -> None:
    while not stop.is_set():
        try:
            now = utc_now()
            searches = await schedule_searches(pool, settings, now)
            linked = await link_mentions(pool)
            backfills = await schedule_backfills(pool, settings, now) if helius else 0
            smarts = await schedule_smart_followers(pool, now)
            await schedule_kol_wallets(pool, now, settings)
            if helius:
                await schedule_kol_wallet_polls(pool, now, settings)
            await classify_pending(pool)  # нарратив по названию/тикеру (без внешних API)
            stale = await jobs.requeue_stale(pool, STALE_AFTER, now)
            if searches or linked or backfills or smarts or stale:
                log.info("scheduler: +%d searches, +%d backfills, +%d smart-follower checks, %d mentions linked, "
                         "%d stale requeued", searches, backfills, smarts, linked, stale)
        except Exception:
            log.exception("scheduler iteration failed")
        await _sleep_or_stop(stop, SCHEDULE_EVERY)


async def worker_loop(pool: asyncpg.Pool, tasks: Tasks, kinds: Sequence[str], stop: asyncio.Event) -> None:
    while not stop.is_set():
        job = await jobs.claim(pool, kinds)
        if job is None:
            await _sleep_or_stop(stop, IDLE_SLEEP)
            continue
        try:
            await tasks.run_job(job)
        except Exception as exc:
            log.exception("job %s %s crashed", job.kind, job.key)
            await jobs.fail(pool, job.id, f"crash: {exc!r}", retry_in=timedelta(minutes=5))


async def outcomes_loop(pool: asyncpg.Pool, stop: asyncio.Event) -> None:
    from kairos.outcomes import RULESETS

    while not stop.is_set():
        try:
            n = 0
            for rules in RULESETS:
                n += await compute_due_outcomes(pool, now=utc_now(), rules=rules)
            if n:
                log.info("outcomes: %d tokens labelled", n)
        except Exception:
            log.exception("outcomes iteration failed")
        await _sleep_or_stop(stop, OUTCOMES_EVERY)


async def signals_loop(pool: asyncpg.Pool, settings: Settings, stop: asyncio.Event) -> None:
    from kairos.scoring import score_due_tokens

    while not stop.is_set():
        try:
            n = await score_due_tokens(pool, now=utc_now(), settings=settings)
            if n:
                log.info("signals: %d tokens scored", n)
        except Exception:
            log.exception("signals iteration failed")
        await _sleep_or_stop(stop, settings.signal_interval_s)


async def main(duration: float | None, daily_credits: int | None, helius_daily_credits: int | None,
               collector: bool, helius: bool) -> None:
    settings = get_settings()
    if settings.frontrun_api_key is None:
        raise SystemExit("FRONTRUN_API_KEY не задан в .env")
    if helius and settings.helius_api_key is None:
        log.warning("HELIUS_API_KEY не задан: дозагрузка сделок и исходы отключены")
        helius = False
    pool = await create_pool(min_size=2, max_size=10)
    stop = asyncio.Event()
    if duration:
        asyncio.get_running_loop().call_later(duration, stop.set)
    shares = frontrun_shares(settings)
    fr_budget = CreditBudget(pool, FRONTRUN, daily_credits or settings.frontrun_daily_credits,
                             pace=settings.budget_pacing, shares=shares)
    if fr_budget.daily_limit * shares[KIND_WALLETS] < WALLETS_COST:
        log.warning("%s: доля кошельков %.0f%% от %d кредитов меньше стоимости поиска (%d) — "
                    "кошельки шиллеров будут искаться только из общего остатка",
                    FRONTRUN, shares[KIND_WALLETS] * 100, fr_budget.daily_limit, WALLETS_COST)
    budgets: dict[str, CreditBudget] = {FRONTRUN: fr_budget}
    try:
        async with AsyncExitStack() as stack:
            fr_client = await stack.enter_async_context(FrontrunClient.from_settings(settings))
            loops: list[Any] = [
                scheduler_loop(pool, settings, stop, helius),
                worker_loop(pool, WalletTaskRouter(FrontrunTasks(pool, fr_client, fr_budget, settings),
                    KolWalletTasks(pool, fr_client, fr_budget, settings)),
                    (*FRONTRUN_KINDS, *WALLET_FRONTRUN_KINDS), stop),
                signals_loop(pool, settings, stop),
            ]
            if helius:
                he_budget = CreditBudget(pool, HELIUS, helius_daily_credits or settings.helius_daily_credits,
                                         pace=settings.budget_pacing)
                budgets[HELIUS] = he_budget
                he_client = await stack.enter_async_context(HeliusClient.from_settings(settings))
                loops += [
                    worker_loop(pool, WalletTaskRouter(HeliusTasks(pool, he_client, he_budget, settings),
                        KolWalletTasks(pool, he_client, he_budget, settings)),
                        (*HELIUS_KINDS, *WALLET_HELIUS_KINDS), stop),
                    outcomes_loop(pool, stop),
                ]
            if collector:
                loops.append(run_collector(TokenStore(pool), settings.pumpportal_ws_url, stop))
            for name, budget in budgets.items():
                log.info("%s budget today: %s of %s credits left", name, await budget.remaining(), budget.daily_limit)
            await asyncio.gather(*loops)
        spent = {name: str(await b.spent_today()) for name, b in budgets.items()}
        log.info("stopped; credits spent today: %s; jobs: %s", spent, await jobs.counts(pool))
    finally:
        await pool.close()


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--duration", type=float, default=None, help="остановиться через N секунд")
    parser.add_argument("--daily-credits", type=int, default=None, help="дневной лимит кредитов Frontrun")
    parser.add_argument("--helius-daily-credits", type=int, default=None, help="дневной лимит кредитов Helius")
    parser.add_argument("--no-collector", action="store_true", help="без PumpPortal (только очередь)")
    parser.add_argument("--no-helius", action="store_true", help="без дозагрузки сделок и исходов")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(main(args.duration, args.daily_credits, args.helius_daily_credits,
                         not args.no_collector, not args.no_helius))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()

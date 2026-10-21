"""Задачи конвейера Frontrun: поиск CA нового токена, история CA шиллера и число его smart followers.

Поток: токен (PumpPortal) → задача frontrun_search_ca → твиты с CA → авторы в kols, твиты в mentions →
авторы с достаточной аудиторией становятся отслеживаемыми → задача frontrun_ca_history → их прошлые
упоминания CA в mentions. Шиллеры с упоминаниями получают задачу frontrun_smart_followers (число «умных»
подписчиков, 3 кредита, раз в 14 дней; самые активные — первыми). Каждый вызов API пишется в api_calls с фактически списанными кредитами,
перед вызовом проверяется дневной бюджет — с видом задачи, чтобы редкие дорогие виды не голодали (CreditBudget.shares).
Из поиска также сохраняются аватар автора (kols.avatar_url, только https, размер 400x400) и просмотры твита
(mentions.view_count) — без дополнительных вызовов API.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from kairos.config import Settings
from kairos.pipeline import jobs
from kairos.pipeline.budget import CreditBudget, record_call
from kairos.sources.frontrun import CREDIT_COST, CaHistory, CaTweet, FrontrunClient, FrontrunError, Tweet

log = logging.getLogger(__name__)

PROVIDER = "frontrun"
KIND_SEARCH = "frontrun_search_ca"
KIND_HISTORY = "frontrun_ca_history"
KIND_SMARTS = "frontrun_smart_followers"
KINDS = (KIND_SEARCH, KIND_HISTORY, KIND_SMARTS)
JOB_COST = {KIND_SEARCH: CREDIT_COST["search_tweets"], KIND_HISTORY: CREDIT_COST["ca_history"],
            KIND_SMARTS: CREDIT_COST["smart_followers_count"]}
SMARTS_REFRESH = timedelta(days=14)
SMARTS_UNRESOLVED_REFRESH = timedelta(days=1)  # Frontrun ещё досчитывает (meta.resolved = false)
SMARTS_BATCH = 50
# Выше выборочного поиска новых токенов (1), ниже поиска по миграциям (10) и истории CA (5): иначе в очереди из
# тысяч поисков смарты не дождались бы бюджета. Разово ≈3,8 тыс. кредитов на всех шиллеров, дальше ≈270 в сутки.
SMARTS_PRIORITY = 2

HISTORY_POLL = timedelta(seconds=90)   # асинхронная история CA: если данных ещё нет, повтор через ~90 с
HISTORY_MAX_POLLS = 4
RETRY_ON_ERROR = timedelta(minutes=5)

_CHAINS = {"SOL": "SOLANA", "SOLANA": "SOLANA"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_chain(chain: str | None) -> str:
    value = (chain or "").strip().upper()
    return _CHAINS.get(value, value or "UNKNOWN")


def tweet_mentions(tweet: Tweet, ca: str) -> bool:
    """Поиск Frontrun — полнотекстовый; оставляем только твиты, где адрес действительно есть."""
    if ca in (tweet.text or ""):
        return True
    return tweet.entities is not None and ca in json.dumps(tweet.entities)


def avatar_url(raw: Any) -> str | None:
    """Ссылка на аватар для kols.avatar_url: только https, суффикс размера X «_normal» (48 px)
    заменяется на «_400x400», чтобы картинка была резкой. Иначе None — COALESCE не затрёт сохранённую."""
    if not isinstance(raw, str):
        return None
    url = raw.strip()
    if not url.lower().startswith("https://") or any(ch.isspace() for ch in url):
        return None
    return re.sub(r"_normal(\.[A-Za-z0-9]{2,5})$", r"_400x400\1", url)


_COUNT_SUFFIX = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def parse_count(raw: Any) -> int | None:
    """Число из поля вроде Tweet.views: Frontrun отдаёт строку («10», «1,234», «1.2K», «3M»).
    Не разобрали — None, счётчик в базе не трогаем."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, float):
        return int(raw) if raw >= 0 and raw == raw else None
    if not isinstance(raw, str):
        return None
    text = raw.strip().replace(",", "").replace(" ", "").upper()
    if not text:
        return None
    multiplier = 1
    if text[-1] in _COUNT_SUFFIX:
        multiplier, text = _COUNT_SUFFIX[text[-1]], text[:-1]
    try:
        value = float(text)
    except ValueError:
        return None
    if value < 0 or value != value or value == float("inf"):
        return None
    return int(round(value * multiplier))


class FrontrunTasks:
    def __init__(self, pool: asyncpg.Pool, client: FrontrunClient, budget: CreditBudget,
                 settings: Settings, clock: Callable[[], datetime] = utc_now):
        self.pool = pool
        self.client = client
        self.budget = budget
        self.settings = settings
        self.clock = clock

    async def run_job(self, job: jobs.Job) -> None:
        cost = JOB_COST[job.kind]
        if not await self.budget.can_spend(cost, job.kind):
            await jobs.reschedule(self.pool, job.id, self.budget.next_reset(), "daily credit budget exhausted")
            return
        before = self.client.credits_spent
        ok, status, note = True, 200, None
        try:
            if job.kind == KIND_SEARCH:
                await self._search_ca(job)
            elif job.kind == KIND_SMARTS:
                await self._smart_followers(job)
            else:
                await self._ca_history(job)
        except FrontrunError as exc:
            ok, status, note = False, exc.http_status, f"{exc.error_code or ''} {exc.message}".strip()
            if exc.http_status in (400, 404):
                await jobs.skip(self.pool, job.id, note)
            else:
                await jobs.fail(self.pool, job.id, note, retry_in=RETRY_ON_ERROR)
            log.warning("%s %s failed: %s", job.kind, job.key, note)
        finally:
            await record_call(self.pool, PROVIDER, job.kind, self.client.credits_spent - before,
                              ok=ok, http_status=status, note=note)

    # ───────────── поиск CA ─────────────

    async def _search_ca(self, job: jobs.Job) -> None:
        mint = job.payload["mint"]
        page = await self.client.search_tweets(mint, page_size=self.settings.search_page_size)
        matched = [t for t in page.tweets if tweet_mentions(t, mint) and t.created_at is not None]
        new_mentions, new_kols, tracked = 0, 0, []
        async with self.pool.acquire() as conn, conn.transaction():
            for tweet in matched:
                kol_id, created = await self._upsert_kol_from_tweet(conn, tweet)
                new_kols += created
                new_mentions += await self._save_mention(
                    conn, kol_id, chain="SOLANA", ca=mint, tweet_id=tweet.tweet_id, tweet_time=tweet.created_at,
                    content=tweet.text, is_deleted=False, reply_count=tweet.replies, retweet_count=tweet.retweets,
                    quote_count=tweet.quotes, favorite_count=tweet.favorites, source="frontrun_search",
                    view_count=parse_count(tweet.views),
                )
                username = await self._maybe_track(conn, kol_id, tweet)
                if username:
                    tracked.append(username)
        await jobs.complete(self.pool, job.id, {
            "tweets": len(page.tweets), "matched": len(matched), "new_mentions": new_mentions,
            "new_kols": new_kols, "tracked": tracked, "more_pages": page.next_cursor is not None,
        })

    async def _upsert_kol_from_tweet(self, conn: asyncpg.Connection, tweet: Tweet) -> tuple[int, bool]:
        user = tweet.user_info
        twitter_id = user.rest_id if user else None
        username = (user.screen_name if user and user.screen_name else tweet.screen_name) or "unknown"
        row = None
        if twitter_id:
            row = await conn.fetchrow("SELECT id, username FROM kols WHERE twitter_id = $1", twitter_id)
        if row is None:
            # Шиллер, известный только по нику (например, из истории CA), получает twitter_id здесь.
            row = await conn.fetchrow(
                "SELECT id, username FROM kols WHERE lower(username) = lower($1) AND twitter_id IS NULL "
                "ORDER BY id LIMIT 1", username)
        followers = user.followers_count if user else None
        avatar = avatar_url(user.avatar) if user else None
        if row is None:
            kol_id = await conn.fetchval(
                """INSERT INTO kols (twitter_id, username, display_name, bio, followers_count, avatar_url)
                   VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
                twitter_id, username, user.name if user else None, user.description if user else None, followers,
                avatar)
            return kol_id, True
        if row["username"].lower() != username.lower():
            await conn.execute(
                """INSERT INTO kol_username_history (kol_id, username, changed_at, source)
                   VALUES ($1, $2, $3, 'observed') ON CONFLICT DO NOTHING""",
                row["id"], row["username"], self.clock())
        await conn.execute(
            """UPDATE kols SET twitter_id = COALESCE(twitter_id, $2), username = $3,
                   display_name = COALESCE($4, display_name), followers_count = COALESCE($5, followers_count),
                   avatar_url = COALESCE($6, avatar_url), updated_at = now()
               WHERE id = $1""",
            row["id"], twitter_id, username, user.name if user else None, followers, avatar)
        return row["id"], False

    async def _maybe_track(self, conn: asyncpg.Connection, kol_id: int, tweet: Tweet) -> str | None:
        """Автор с аудиторией от порога становится отслеживаемым; его историю CA ставим в очередь."""
        followers = tweet.user_info.followers_count if tweet.user_info else None
        if followers is None or followers < self.settings.kol_min_followers:
            return None
        row = await conn.fetchrow(
            "UPDATE kols SET tracked = true WHERE id = $1 RETURNING username, ca_history_fetched_at", kol_id)
        if row["ca_history_fetched_at"] and \
                self.clock() - row["ca_history_fetched_at"] < timedelta(days=self.settings.kol_history_refresh_days):
            return None
        bucket = int(self.clock().timestamp() // 86400) // self.settings.kol_history_refresh_days
        created = await jobs.enqueue(conn, KIND_HISTORY, f"{kol_id}:{bucket}",
                                     {"kol_id": kol_id, "username": row["username"]}, priority=5)
        return row["username"] if created else None

    # ───────────── история CA шиллера ─────────────

    async def _ca_history(self, job: jobs.Job) -> None:
        kol_id, username = job.payload["kol_id"], job.payload["username"]
        history = await self.client.ca_history(username)
        if history is None:
            # Frontrun запустил сбор в фоне; повторяем, пока не отдаст (каждый опрос стоит кредитов).
            await jobs.fail(self.pool, job.id, "ca history not ready", retry_in=HISTORY_POLL,
                            max_attempts=HISTORY_MAX_POLLS)
            return
        new_mentions = await self._save_history(kol_id, history)
        await jobs.complete(self.pool, job.id, {
            "new_mentions": new_mentions, "source": history.source, "cache_hit": history.cache_hit,
            "undeleted_ca": history.undeleted_ca_count, "deleted_ca": history.deleted_ca_count,
            "undeleted_tweets": history.undeleted_tweet_count, "deleted_tweets": history.deleted_tweet_count,
        })

    async def _save_history(self, kol_id: int, history: CaHistory) -> int:
        tweets: list[CaTweet] = [*history.top_undeleted_tweets, *history.top_deleted_tweets]
        new = 0
        async with self.pool.acquire() as conn, conn.transaction():
            for t in tweets:
                if not t.tweet_id or not t.ca or t.tweet_time is None:
                    continue
                new += await self._save_mention(
                    conn, kol_id, chain=normalize_chain(t.chain), ca=t.ca, tweet_id=t.tweet_id,
                    tweet_time=t.tweet_time, content=t.content, is_deleted=bool(t.is_deleted),
                    reply_count=t.reply_count, retweet_count=t.retweet_count, quote_count=t.quote_count,
                    favorite_count=t.favorite_count, source="frontrun_ca_history",
                )
            await conn.execute(
                "UPDATE kols SET ca_history_fetched_at = $2, updated_at = now() WHERE id = $1",
                kol_id, self.clock())
        return new

    # ───────────── smart followers шиллера ─────────────

    async def _smart_followers(self, job: jobs.Job) -> None:
        kol_id, username = job.payload["kol_id"], job.payload["username"]
        res = await self.client.smart_followers_count(username)
        final = res.meta.resolved if res.meta is not None else None
        await self.pool.execute(
            """UPDATE kols SET smart_followers_count = COALESCE($2, smart_followers_count),
                   smart_followers_final = $3, smart_followers_fetched_at = $4, updated_at = now()
               WHERE id = $1""", kol_id, res.total_count, final, self.clock())
        await jobs.complete(self.pool, job.id, {"smart_followers": res.total_count, "resolved": final})

    # ───────────── общее ─────────────

    @staticmethod
    async def _save_mention(conn: asyncpg.Connection, kol_id: int, *, chain: str, ca: str, tweet_id: str,
                            tweet_time: datetime, content: str | None, is_deleted: bool,
                            reply_count: Any, retweet_count: Any, quote_count: Any, favorite_count: Any,
                            source: str, view_count: int | None = None) -> int:
        """Возвращает 1, если упоминание новое. Повтор обновляет счётчики и флаг удаления."""
        inserted = await conn.fetchval(
            """INSERT INTO mentions (kol_id, chain, ca, token_id, tweet_id, tweet_time, content, is_deleted,
                                     reply_count, retweet_count, quote_count, favorite_count, source, view_count)
               VALUES ($1, $2, $3, (SELECT id FROM tokens WHERE chain = $2 AND mint = $3),
                       $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
               ON CONFLICT (tweet_id, ca) DO UPDATE SET
                   is_deleted = mentions.is_deleted OR EXCLUDED.is_deleted,
                   token_id = COALESCE(mentions.token_id, EXCLUDED.token_id),
                   reply_count = COALESCE(EXCLUDED.reply_count, mentions.reply_count),
                   retweet_count = COALESCE(EXCLUDED.retweet_count, mentions.retweet_count),
                   quote_count = COALESCE(EXCLUDED.quote_count, mentions.quote_count),
                   favorite_count = COALESCE(EXCLUDED.favorite_count, mentions.favorite_count),
                   view_count = COALESCE(EXCLUDED.view_count, mentions.view_count)
               RETURNING (xmax = 0)""",
            kol_id, chain, ca, tweet_id, tweet_time, content, is_deleted,
            reply_count, retweet_count, quote_count, favorite_count, source, view_count)
        await conn.execute(
            "UPDATE kols SET last_mention_at = GREATEST(last_mention_at, $2) WHERE id = $1", kol_id, tweet_time)
        return int(bool(inserted))


async def schedule_smart_followers(pool: asyncpg.Pool, now: datetime, limit: int = SMARTS_BATCH) -> int:
    """Ставит задачи числа smart followers: шиллеры с упоминаниями, без свежего значения, самые активные первыми.

    Свежее — запрошено меньше 14 дней назад (или меньше суток, если Frontrun ещё не досчитал). Приоритет —
    ``SMARTS_PRIORITY``. Дубли отсекает условие «нет ждущей или
    выполняемой задачи этого шиллера»; ключ задачи включает номер суток, чтобы после выполнения можно было
    поставить следующую.
    """
    rows = await pool.fetch(
        """SELECT k.id, k.username FROM kols k
           JOIN (SELECT kol_id, count(DISTINCT ca) AS cas, max(tweet_time) AS last FROM mentions GROUP BY kol_id) m
             ON m.kol_id = k.id
           WHERE k.username <> 'unknown'
             AND (k.smart_followers_fetched_at IS NULL
                  OR k.smart_followers_fetched_at < $1::timestamptz - $2::interval
                  OR (k.smart_followers_final IS NOT TRUE AND k.smart_followers_fetched_at < $1::timestamptz - $3::interval))
             AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind = $4 AND j.payload->>'kol_id' = k.id::text
                             AND j.status IN ('pending', 'running'))
           ORDER BY m.cas DESC, m.last DESC, k.id
           LIMIT $5""", now, SMARTS_REFRESH, SMARTS_UNRESOLVED_REFRESH, KIND_SMARTS, limit)
    period = int(now.timestamp() // SMARTS_UNRESOLVED_REFRESH.total_seconds())
    created = 0
    for r in rows:
        if await jobs.enqueue(pool, KIND_SMARTS, f"{r['id']}:smarts:{period}",
                              {"kol_id": r["id"], "username": r["username"]}, priority=SMARTS_PRIORITY, run_after=now):
            created += 1
    return created

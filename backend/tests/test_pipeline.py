import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from kairos.config import Settings
from kairos.pipeline import jobs
from kairos.pipeline.budget import CreditBudget, record_call
from kairos.pipeline.frontrun_tasks import (
    KIND_HISTORY, KIND_SEARCH, KIND_SMARTS, FrontrunTasks, avatar_url, normalize_chain, parse_count,
    schedule_smart_followers,
)
from kairos.pipeline.scheduler import link_mentions, schedule_searches
from kairos.sources.frontrun import FrontrunClient

BASE = "https://api.frontrun.pro/api/v1"
MINT = "EWLaWR498Y7F9vBBj4q2QbQgvEHTAdznjj4AGHLupump"
NOW = datetime(2026, 10, 18, 14, 0, tzinfo=timezone.utc)


def ok(data):
    return httpx.Response(200, json={"data": data, "code": 200, "status": True, "message": "success"})


def tweet(tweet_id, screen_name, rest_id, followers, text=f"CA: {MINT}", created="Sat Oct 18 13:55:00 +0000 2026"):
    return {
        "type": "tweet", "tweet_id": tweet_id, "url": f"https://x.com/{screen_name}/status/{tweet_id}",
        "screen_name": screen_name, "text": text, "lang": "en", "created_at": created,
        "retweets": 1, "replies": 2, "quotes": 0, "favorites": 5, "views": "10",
        "entities": {"urls": []}, "media": [],
        "user_info": {"screen_name": screen_name, "name": screen_name.title(), "rest_id": rest_id,
                      "followers_count": followers, "description": "bio"},
    }


def settings(**kw):
    base = dict(frontrun_api_key="test-key", kol_min_followers=1000, search_page_size=100,
                search_new_token_sample_pct=100, search_new_token_delay_s=300, kol_history_refresh_days=7)
    return Settings(**{**base, **kw})


class Clock:
    def __init__(self):
        self.mono = 0.0

    def clock(self):
        return self.mono

    async def sleep(self, s):
        self.mono += s


@pytest.fixture
async def env(db_pool):
    c = Clock()
    with respx.mock(assert_all_called=False) as router:
        async with FrontrunClient("test-key", clock=c.clock, sleep=c.sleep) as client:
            budget = CreditBudget(db_pool, "frontrun", 1000, clock=lambda: NOW)
            tasks = FrontrunTasks(db_pool, client, budget, settings(), clock=lambda: NOW)
            yield db_pool, router, tasks


async def add_token(pool, mint=MINT, created=NOW - timedelta(minutes=10), migrated=None):
    return await pool.fetchval(
        "INSERT INTO tokens (mint, created_at, migrated_at, source) VALUES ($1, $2, $3, 'test') RETURNING id",
        mint, created, migrated)


async def run_one(pool, tasks, kinds=(KIND_SEARCH, KIND_HISTORY)):
    job = await jobs.claim(pool, list(kinds), now=max(NOW, datetime.now(timezone.utc)) + timedelta(days=1))  # задачи ставятся по реальным часам
    assert job is not None
    await tasks.run_job(job)
    return job


async def test_search_stores_kols_mentions_and_tracks_big_accounts(env):
    pool, router, tasks = env
    token_id = await add_token(pool)
    router.get(f"{BASE}/pro/twitter/tweets/search").mock(return_value=ok({"tweets": [
        tweet("1", "bigkol", "100", 50_000),
        tweet("2", "smallguy", "200", 300),
        tweet("3", "noise", "300", 9_000, text="pump fun is wild"),   # без CA — отбрасывается
    ], "nextCursor": None}))
    await jobs.enqueue(pool, KIND_SEARCH, f"{MINT}:migrate", {"mint": MINT})
    job = await run_one(pool, tasks)

    rows = await pool.fetch("SELECT k.username, k.tracked, m.token_id, m.source FROM mentions m "
                            "JOIN kols k ON k.id = m.kol_id ORDER BY k.username")
    assert [(r["username"], r["tracked"]) for r in rows] == [("bigkol", True), ("smallguy", False)]
    assert all(r["token_id"] == token_id and r["source"] == "frontrun_search" for r in rows)
    assert await pool.fetchval("SELECT count(*) FROM kols WHERE username = 'noise'") == 0
    hist = await pool.fetch("SELECT key, payload FROM jobs WHERE kind = $1", KIND_HISTORY)
    assert len(hist) == 1 and json.loads(hist[0]["payload"])["username"] == "bigkol"
    done = await pool.fetchrow("SELECT status, result FROM jobs WHERE id = $1", job.id)
    assert done["status"] == "done" and json.loads(done["result"])["matched"] == 2 and json.loads(done["result"])["new_mentions"] == 2
    call = await pool.fetchrow("SELECT endpoint, credits, ok FROM api_calls")
    assert call["endpoint"] == KIND_SEARCH and call["credits"] == 2 and call["ok"]


async def test_repeat_search_is_idempotent_and_tracks_rename(env):
    pool, router, tasks = env
    route = router.get(f"{BASE}/pro/twitter/tweets/search")
    route.mock(return_value=ok({"tweets": [tweet("1", "oldname", "100", 50_000)]}))
    await jobs.enqueue(pool, KIND_SEARCH, f"{MINT}:create", {"mint": MINT})
    await run_one(pool, tasks)
    route.mock(return_value=ok({"tweets": [tweet("1", "newname", "100", 51_000), tweet("9", "newname", "100", 51_000)]}))
    await jobs.enqueue(pool, KIND_SEARCH, f"{MINT}:migrate", {"mint": MINT})
    await run_one(pool, tasks, kinds=[KIND_SEARCH])
    assert await pool.fetchval("SELECT count(*) FROM kols") == 1
    assert await pool.fetchval("SELECT username FROM kols") == "newname"
    assert await pool.fetchval("SELECT username FROM kol_username_history") == "oldname"
    assert await pool.fetchval("SELECT count(*) FROM mentions") == 2
    # история CA поставлена один раз за период обновления
    assert await pool.fetchval("SELECT count(*) FROM jobs WHERE kind = $1", KIND_HISTORY) == 1


async def test_ca_history_pending_then_ready(env):
    pool, router, tasks = env
    kol_id = await pool.fetchval("INSERT INTO kols (username, tracked) VALUES ('bigkol', true) RETURNING id")
    old_token = await add_token(pool, mint="OldMint111pump")
    route = router.get(f"{BASE}/pro/twitter/bigkol/tweets-with-ca-cache")
    route.mock(return_value=ok({"cacheHit": False, "data": None}))   # реальный ответ «сбор начат»
    await jobs.enqueue(pool, KIND_HISTORY, f"{kol_id}:1", {"kol_id": kol_id, "username": "bigkol"})
    job = await run_one(pool, tasks)
    row = await pool.fetchrow("SELECT status, run_after FROM jobs WHERE id = $1", job.id)
    assert row["status"] == "pending"   # повтор через ~90 с

    ca_tweet = {"tweetId": "t1", "twitterHandle": "bigkol", "chain": "SOL", "ca": "OldMint111pump",
                "content": "aping OldMint111pump", "isDeleted": False, "quoteCount": 0, "replyCount": 1,
                "retweetCount": 2, "favoriteCount": 3, "tweetTime": "2026-09-01T10:00:00.000Z"}
    deleted = {**ca_tweet, "tweetId": "t2", "ca": "0xabc", "chain": "BASE", "isDeleted": True}
    route.mock(return_value=ok({"cacheHit": True, "data": {
        "source": "database", "topUndeletedTweets": [ca_tweet], "topDeletedTweets": [deleted],
        "deletedCACount": 1, "deletedTweetCount": 1, "undeletedCACount": 1, "undeletedTweetCount": 1}}))
    await run_one(pool, tasks)
    rows = await pool.fetch("SELECT chain, ca, token_id, is_deleted FROM mentions ORDER BY tweet_id")
    assert [(r["chain"], r["token_id"], r["is_deleted"]) for r in rows] == [("SOLANA", old_token, False),
                                                                            ("BASE", None, True)]
    assert await pool.fetchval("SELECT ca_history_fetched_at FROM kols") == NOW
    assert await pool.fetchval("SELECT sum(credits) FROM api_calls") == 100   # два опроса по 50


async def test_budget_exhausted_reschedules_to_next_day(env):
    pool, router, tasks = env
    tasks.budget = CreditBudget(pool, "frontrun", 1, clock=lambda: NOW)
    route = router.get(f"{BASE}/pro/twitter/tweets/search").mock(return_value=ok({"tweets": []}))
    await jobs.enqueue(pool, KIND_SEARCH, f"{MINT}:create", {"mint": MINT})
    job = await run_one(pool, tasks)
    row = await pool.fetchrow("SELECT status, run_after, attempts FROM jobs WHERE id = $1", job.id)
    assert row["status"] == "pending" and row["run_after"] == datetime(2026, 10, 19, tzinfo=timezone.utc)
    assert row["attempts"] == 0 and not route.called
    assert await pool.fetchval("SELECT count(*) FROM api_calls") == 0


async def test_api_error_is_recorded_and_retried(env):
    pool, router, tasks = env
    router.get(f"{BASE}/pro/twitter/tweets/search").mock(
        return_value=httpx.Response(403, json={"statusCode": 403, "errorCode": "NO_CREDITS", "message": "no"}))
    await jobs.enqueue(pool, KIND_SEARCH, f"{MINT}:create", {"mint": MINT})
    job = await run_one(pool, tasks)
    assert await pool.fetchval("SELECT status FROM jobs WHERE id = $1", job.id) == "pending"
    call = await pool.fetchrow("SELECT credits, ok, http_status, note FROM api_calls")
    assert call["credits"] == 0 and not call["ok"] and call["http_status"] == 403 and "NO_CREDITS" in call["note"]


async def test_404_skips_job(env):
    pool, router, tasks = env
    router.get(f"{BASE}/pro/twitter/ghost/tweets-with-ca-cache").mock(
        return_value=httpx.Response(404, json={"statusCode": 404, "errorCode": "NOT_FOUND", "message": "x"}))
    await jobs.enqueue(pool, KIND_HISTORY, "1:1", {"kol_id": 1, "username": "ghost"})
    job = await run_one(pool, tasks)
    assert await pool.fetchval("SELECT status FROM jobs WHERE id = $1", job.id) == "skipped"


async def test_scheduler_migrations_and_sample(db_pool):
    await add_token(db_pool, mint="Migrated1", created=NOW - timedelta(minutes=40), migrated=NOW - timedelta(minutes=5))
    await add_token(db_pool, mint="Fresh1", created=NOW - timedelta(minutes=2))       # ещё рано
    await add_token(db_pool, mint="Ready1", created=NOW - timedelta(minutes=6))
    await add_token(db_pool, mint="Ancient1", created=NOW - timedelta(hours=5))       # слишком старый
    s = settings(search_new_token_sample_pct=100)
    assert await schedule_searches(db_pool, s, NOW) == 3   # Migrated1:migrate, Migrated1:create, Ready1:create
    assert await schedule_searches(db_pool, s, NOW) == 0
    keys = {r["key"]: r["priority"] for r in await db_pool.fetch("SELECT key, priority FROM jobs")}
    assert keys == {"Migrated1:migrate": 10, "Migrated1:create": 1, "Ready1:create": 1}


async def test_scheduler_sampling_off(db_pool):
    await add_token(db_pool, mint="Ready1", created=NOW - timedelta(minutes=6))
    assert await schedule_searches(db_pool, settings(search_new_token_sample_pct=0), NOW) == 0


async def test_link_mentions_backfills_token_id(db_pool):
    kol = await db_pool.fetchval("INSERT INTO kols (username) VALUES ('a') RETURNING id")
    await db_pool.execute(
        "INSERT INTO mentions (kol_id, chain, ca, tweet_id, tweet_time, source) VALUES ($1, 'SOLANA', 'LateMint', 't', $2, 'x')",
        kol, NOW)
    token_id = await add_token(db_pool, mint="LateMint")
    assert await link_mentions(db_pool) == 1
    assert await db_pool.fetchval("SELECT token_id FROM mentions") == token_id


def test_normalize_chain():
    assert normalize_chain("SOL") == "SOLANA" and normalize_chain("solana") == "SOLANA"
    assert normalize_chain("BASE") == "BASE" and normalize_chain(None) == "UNKNOWN"


AVATAR = "https://pbs.twimg.com/profile_images/1/abc_normal.jpg"


@pytest.mark.parametrize("raw,expected", [
    (AVATAR, "https://pbs.twimg.com/profile_images/1/abc_400x400.jpg"),
    ("https://pbs.twimg.com/profile_images/1/abc_normal.PNG", "https://pbs.twimg.com/profile_images/1/abc_400x400.PNG"),
    ("https://pbs.twimg.com/profile_images/1/abc_400x400.jpg", "https://pbs.twimg.com/profile_images/1/abc_400x400.jpg"),
    ("https://pbs.twimg.com/profile_images/1/abc.jpg ", "https://pbs.twimg.com/profile_images/1/abc.jpg"),
    ("https://pbs.twimg.com/normal_person/abc.jpg", "https://pbs.twimg.com/normal_person/abc.jpg"),  # не суффикс
    ("http://pbs.twimg.com/profile_images/1/abc_normal.jpg", None),
    ("javascript:alert(1)", None), ("https://x y.z/a_normal.jpg", None), ("", None), (None, None), (42, None),
])
def test_avatar_url(raw, expected):
    assert avatar_url(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("10", 10), (" 1,234 ", 1234), ("1.2K", 1200), ("1.2k", 1200), ("3M", 3_000_000), ("2.5B", 2_500_000_000),
    ("0", 0), (7, 7), (7.9, 7), ("", None), ("n/a", None), ("-5", None), (-1, None), (None, None), (True, None),
    ("1e400", None), ("K", None), ([], None),
])
def test_parse_count(raw, expected):
    assert parse_count(raw) == expected


async def test_search_stores_avatar_and_views(env):
    pool, router, tasks = env
    await add_token(pool)
    first = tweet("1", "bigkol", "100", 50_000)
    first["user_info"]["avatar"] = AVATAR
    first["views"] = "1.2K"
    second = tweet("2", "shy", "200", 5_000)      # без аватара и просмотров
    second["views"] = None
    route = router.get(f"{BASE}/pro/twitter/tweets/search").mock(return_value=ok({"tweets": [first, second]}))
    await jobs.enqueue(pool, KIND_SEARCH, f"{MINT}:create", {"mint": MINT})
    await run_one(pool, tasks, kinds=[KIND_SEARCH])
    kols = {r["username"]: r["avatar_url"] for r in await pool.fetch("SELECT username, avatar_url FROM kols")}
    assert kols == {"bigkol": "https://pbs.twimg.com/profile_images/1/abc_400x400.jpg", "shy": None}
    views = {r["tweet_id"]: r["view_count"] for r in await pool.fetch("SELECT tweet_id, view_count FROM mentions")}
    assert views == {"1": 1200, "2": None}

    # Повтор: пропавший аватар и просмотры не затирают сохранённые (COALESCE), новые — обновляют.
    first["user_info"]["avatar"], first["views"] = None, "garbage"
    second["user_info"]["avatar"], second["views"] = "https://pbs.twimg.com/profile_images/2/shy_normal.png", "15"
    route.mock(return_value=ok({"tweets": [first, second]}))
    await jobs.enqueue(pool, KIND_SEARCH, f"{MINT}:migrate", {"mint": MINT})
    await run_one(pool, tasks, kinds=[KIND_SEARCH])
    kols = {r["username"]: r["avatar_url"] for r in await pool.fetch("SELECT username, avatar_url FROM kols")}
    assert kols == {"bigkol": "https://pbs.twimg.com/profile_images/1/abc_400x400.jpg",
                    "shy": "https://pbs.twimg.com/profile_images/2/shy_400x400.png"}
    views = {r["tweet_id"]: r["view_count"] for r in await pool.fetch("SELECT tweet_id, view_count FROM mentions")}
    assert views == {"1": 1200, "2": 15}
    first["user_info"]["avatar"] = "https://pbs.twimg.com/profile_images/3/new_normal.jpg"
    route.mock(return_value=ok({"tweets": [first]}))
    await jobs.enqueue(pool, KIND_SEARCH, f"{MINT}:again", {"mint": MINT})
    await run_one(pool, tasks, kinds=[KIND_SEARCH])
    assert await pool.fetchval("SELECT avatar_url FROM kols WHERE username='bigkol'") == "https://pbs.twimg.com/profile_images/3/new_400x400.jpg"


async def test_budget_check_passes_job_kind(env):
    """Проверка бюджета идёт с видом задачи: доля поиска исчерпана — поиск ждёт, хотя кредиты провайдера есть."""
    pool, router, tasks = env
    await jobs.enqueue(pool, KIND_SMARTS, "hold", {"kol_id": 1, "username": "x"}, run_after=NOW)   # держит резерв смартов
    tasks.budget = CreditBudget(pool, "frontrun", 100, clock=lambda: NOW, shares={KIND_SEARCH: .9, KIND_SMARTS: .1})
    route = router.get(f"{BASE}/pro/twitter/tweets/search").mock(return_value=ok({"tweets": []}))
    await record_call(pool, "frontrun", KIND_SEARCH, 89, ok=True, ts=NOW)
    await jobs.enqueue(pool, KIND_SEARCH, f"{MINT}:create", {"mint": MINT})
    job = await run_one(pool, tasks, kinds=[KIND_SEARCH])
    assert await pool.fetchval("SELECT status FROM jobs WHERE id = $1", job.id) == "pending" and not route.called
    await jobs.complete(pool, (await jobs.claim(pool, [KIND_SMARTS], NOW)).id)   # смарты сделаны — их доля свободна
    await jobs.reschedule(pool, job.id, NOW)
    await run_one(pool, tasks, kinds=[KIND_SEARCH])
    assert route.called and await pool.fetchval("SELECT status FROM jobs WHERE id = $1", job.id) == "done"


async def test_smart_followers_scheduled_by_activity_and_stored(env):
    pool, router, tasks = env
    busy = await pool.fetchval("INSERT INTO kols (username) VALUES ('busy') RETURNING id")
    quiet = await pool.fetchval("INSERT INTO kols (username) VALUES ('quiet') RETURNING id")
    fresh = await pool.fetchval(
        "INSERT INTO kols (username, smart_followers_count, smart_followers_final, smart_followers_fetched_at) "
        "VALUES ('fresh', 7, true, $1) RETURNING id", NOW - timedelta(days=3))
    await pool.execute("INSERT INTO kols (username) VALUES ('silent')")  # без упоминаний — не нужен
    rows = [(busy, f"ca{i}", f"b{i}") for i in range(3)] + [(quiet, "ca0", "q0"), (fresh, "ca9", "f0")]
    await pool.executemany("""INSERT INTO mentions (kol_id, chain, ca, tweet_id, tweet_time, source)
                              VALUES ($1, 'SOLANA', $2, $3, $4, 'test')""", [(*r, NOW) for r in rows])
    assert await schedule_smart_followers(pool, NOW) == 2
    assert await schedule_smart_followers(pool, NOW) == 0  # повтор не дублирует
    queued = await pool.fetch("SELECT payload->>'username' AS u, priority FROM jobs WHERE kind = $1 ORDER BY id", KIND_SMARTS)
    assert [r["u"] for r in queued] == ["busy", "quiet"] and {r["priority"] for r in queued} == {2}

    router.get(f"{BASE}/pro/twitter/busy/smart-followers/count").mock(
        return_value=ok({"totalCount": 450, "meta": {"resolved": True}}))
    router.get(f"{BASE}/pro/twitter/quiet/smart-followers/count").mock(
        return_value=ok({"totalCount": 12, "meta": {"resolved": False}}))
    await run_one(pool, tasks, kinds=(KIND_SMARTS,))
    await run_one(pool, tasks, kinds=(KIND_SMARTS,))
    got = {r["username"]: (r["smart_followers_count"], r["smart_followers_final"]) for r in await pool.fetch(
        "SELECT username, smart_followers_count, smart_followers_final FROM kols WHERE smart_followers_fetched_at = $1", NOW)}
    assert got == {"busy": (450, True), "quiet": (12, False)}
    assert await pool.fetchval("SELECT sum(credits) FROM api_calls WHERE endpoint = $1", KIND_SMARTS) == 6
    # незавершённый подсчёт перепроверяется через сутки, готовый — через 14 дней
    assert await schedule_smart_followers(pool, NOW + timedelta(days=2)) == 1
    assert await schedule_smart_followers(pool, NOW + timedelta(days=15)) == 2  # busy и fresh

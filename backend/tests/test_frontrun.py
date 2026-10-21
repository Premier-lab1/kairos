"""All HTTP traffic is intercepted by respx; clocks advance without real sleeps."""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import format_datetime
from types import SimpleNamespace

import httpx
import pytest
import respx
from pydantic import SecretStr

from kairos.sources.frontrun import CREDIT_COST, FrontrunClient, FrontrunError


BASE = "https://api.frontrun.pro/api/v1"
KEY = "test-private-api-key"


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake():
    return FakeTime()


@pytest.fixture
async def client(fake):
    async with FrontrunClient(KEY, clock=fake.clock, sleep=fake.sleep) as instance:
        yield instance


@pytest.fixture(autouse=True)
def mock_http():
    # Unregistered calls fail rather than reaching the network.
    with respx.mock(assert_all_called=False) as router:
        yield router


def ok(data):
    return httpx.Response(200, json={"data": data, "code": 200, "status": True, "message": "success"})


def ca_data():
    return {
        "source": "cache", "cacheHit": True,
        "topUndeletedTweets": [{
            "tweetId": "1", "twitterHandle": "alice", "chain": "SOL", "ca": "abc",
            "content": "token", "isDeleted": False, "quoteCount": 1, "replyCount": 2,
            "retweetCount": 3, "favoriteCount": 4, "tweetTime": "2026-08-29T06:32:25Z",
        }],
        "topDeletedTweets": [], "deletedCACount": 0, "deletedTweetCount": 0,
        "undeletedCACount": 1, "undeletedTweetCount": 1,
    }


async def test_headers_points_and_lifecycle(mock_http, fake):
    route = mock_http.get(f"{BASE}/user/paid-api/points/{KEY}").mock(return_value=ok({
        "currentPoints": 100, "includedCredits": "100", "topUpCredits": "0", "totalCredits": "100",
    }))
    async with FrontrunClient(KEY, clock=fake.clock, sleep=fake.sleep) as client:
        result = await client.get_points()
        assert result.current_points == 100
        assert result.top_up_credits == "0"
        assert client.credits_spent == 0
    assert client._client.is_closed
    headers = route.calls[0].request.headers
    assert headers["authorization"] == f"Bearer {KEY}"
    assert headers["accept"] == "application/json"
    assert headers["x-copilot-client-language"] == "en"


async def test_key_redacted_from_repr_errors_logs(mock_http, client, caplog):
    caplog.set_level(logging.DEBUG)
    mock_http.get(f"{BASE}/user/paid-api/points/{KEY}").mock(return_value=httpx.Response(
        403, json={"statusCode": 403, "errorCode": "DENIED", "message": f"Invalid key {KEY}"},
    ))
    with pytest.raises(FrontrunError) as caught:
        await client.get_points()
    assert KEY not in str(client) + repr(client) + str(caught.value) + repr(caught.value)
    assert KEY not in caplog.text
    assert "REDACTED" in caught.value.endpoint
    assert KEY not in str(vars(caught.value))


async def test_envelope_unwrap_and_raw_extras(mock_http, client):
    payload = {"accounts": [{"twitter": "alice"}], "newField": 3}
    mock_http.get(f"{BASE}/pro/trending-accounts", params={"window": "24h"}).mock(return_value=ok(payload))
    assert await client.trending_accounts() == payload


async def test_error_body_mapping(mock_http, client):
    mock_http.get(f"{BASE}/pro/twitter/alice/info").mock(return_value=httpx.Response(
        404, json={"statusCode": 404, "errorCode": "PROFILE_NOT_FOUND", "message": "No profile"},
    ))
    with pytest.raises(FrontrunError) as caught:
        await client.profile_info("alice")
    error = caught.value
    assert (error.http_status, error.error_code, error.message, error.endpoint) == (
        404, "PROFILE_NOT_FOUND", "No profile", "/pro/twitter/alice/info",
    )
    assert client.credits_spent == 0


async def test_status_false_http_200(mock_http, client):
    mock_http.get(f"{BASE}/pro/trending-accounts").mock(return_value=httpx.Response(
        200, json={"status": False, "message": "Denied", "data": None},
    ))
    with pytest.raises(FrontrunError, match="Denied") as caught:
        await client.trending_accounts()
    assert caught.value.http_status == 200
    assert client.credits_spent == 0


@pytest.mark.parametrize("status", [429, 503])
async def test_retry_then_success(status, mock_http, client, fake):
    route = mock_http.get(f"{BASE}/pro/trending-accounts").mock(side_effect=[
        httpx.Response(status, json={"message": "retry"}), ok([]),
    ])
    assert await client.trending_accounts() == []
    assert route.call_count == 2
    assert fake.sleeps == [1.0]
    assert client.credits_spent == 30


async def test_no_retry_400(mock_http, client, fake):
    route = mock_http.get(f"{BASE}/pro/trending-accounts").mock(return_value=httpx.Response(400, json={"message": "Bad request"}))
    with pytest.raises(FrontrunError):
        await client.trending_accounts()
    assert route.call_count == 1
    assert fake.sleeps == []


async def test_max_retries_exhausted(mock_http, client, fake):
    route = mock_http.get(f"{BASE}/pro/trending-accounts").mock(return_value=httpx.Response(503, json={"message": "Unavailable"}))
    with pytest.raises(FrontrunError) as caught:
        await client.trending_accounts()
    assert caught.value.http_status == 503
    assert route.call_count == 4
    assert fake.sleeps == [1, 2, 4]
    assert client.credits_spent == 0


async def test_retry_after_seconds(mock_http, client, fake):
    mock_http.get(f"{BASE}/pro/trending-accounts").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "12"}), ok([]),
    ])
    await client.trending_accounts()
    assert fake.sleeps == [12]


async def test_retry_after_http_date(mock_http, client, fake):
    retry_date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
    mock_http.get(f"{BASE}/pro/trending-accounts").mock(side_effect=[
        httpx.Response(503, headers={"Retry-After": retry_date}), ok([]),
    ])
    await client.trending_accounts()
    assert 28 <= fake.sleeps[0] <= 30


async def test_timeout_retry_and_sanitized_exhaustion(mock_http, client, fake):
    route = mock_http.get(f"{BASE}/user/paid-api/points/{KEY}").mock(
        side_effect=httpx.ReadTimeout(f"Timed out at {KEY}"),
    )
    with pytest.raises(FrontrunError) as caught:
        await client.get_points()
    assert KEY not in str(caught.value)
    assert caught.value.http_status is None
    assert caught.value.error_code == "ReadTimeout"
    assert route.call_count == 4
    assert fake.sleeps == [1, 2, 4]


async def test_timeout_then_success(mock_http, client):
    route = mock_http.get(f"{BASE}/pro/trending-accounts").mock(side_effect=[httpx.ReadTimeout("timeout"), ok([])])
    assert await client.trending_accounts() == []
    assert route.call_count == 2


async def test_rolling_limit_blocks_61st_concurrently(mock_http):
    now = 0.0
    sleeping = asyncio.Event()
    advance = asyncio.Event()

    async def sleep(seconds):
        assert seconds == 60
        sleeping.set()
        await advance.wait()

    route = mock_http.get(f"{BASE}/pro/trending-accounts").mock(return_value=ok([]))
    async with FrontrunClient(KEY, clock=lambda: now, sleep=sleep) as client:
        await asyncio.gather(*(client.trending_accounts() for _ in range(60)))
        pending = asyncio.create_task(client.trending_accounts())
        try:
            await asyncio.wait_for(sleeping.wait(), timeout=2)
            assert route.call_count == 60
            assert not pending.done()
            now = 60
            advance.set()
            await asyncio.wait_for(pending, timeout=2)
            assert route.call_count == 61
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


async def test_rate_window_does_not_reset_at_minute_boundary(mock_http, fake):
    mock_http.get(f"{BASE}/pro/trending-accounts").mock(return_value=ok([]))
    async with FrontrunClient(KEY, rpm=2, clock=fake.clock, sleep=fake.sleep) as client:
        fake.now = 59
        await client.trending_accounts()
        fake.now = 60
        await client.trending_accounts()
        await client.trending_accounts()
    assert fake.sleeps == [59]


async def test_search_parsing(mock_http, client):
    tweet = {
        "type": "tweet", "tweet_id": "12", "url": "https://x.com/alice/status/12",
        "screen_name": "alice", "text": "token", "lang": "en",
        "created_at": "Sat Aug 29 06:32:25 +0000 2026", "conversation_id": "12",
        "retweets": 1, "replies": 2, "quotes": 3, "favorites": 4,
        "bookmarks": 5, "views": "12345", "entities": {}, "media": [],
        "user_info": {"screen_name": "alice", "name": "Alice", "description": "bio",
                      "rest_id": "42", "followers_count": 10, "favourites_count": 20,
                      "avatar": "avatar", "verified": True, "friends_count": 3, "location": "Earth"},
        "newTweetField": True,
    }
    mock_http.get(f"{BASE}/pro/twitter/tweets/search", params={"q": "token", "page_size": 25, "cursor": "next"}).mock(
        return_value=ok({"tweets": [tweet], "nextCursor": None, "newResponseField": 1}),
    )
    result = await client.search_tweets("token", page_size=25, cursor="next")
    assert result.next_cursor is None
    assert result.tweets[0].created_at == datetime(2026, 8, 29, 6, 32, 25, tzinfo=timezone.utc)
    assert result.tweets[0].user_info.followers_count == 10
    assert result.tweets[0].views == "12345"
    assert result.model_extra == {"newResponseField": 1}
    assert result.tweets[0].model_extra == {"newTweetField": True}


@pytest.mark.parametrize("size", [0, 101])
async def test_page_size_validation(client, size):
    with pytest.raises(ValueError):
        await client.search_tweets("x", page_size=size)


async def test_profile_null(mock_http, client):
    mock_http.get(f"{BASE}/pro/twitter/alice/info", params={"twitterId": "42"}).mock(return_value=ok(None))
    assert await client.profile_info("alice", twitter_id="42") is None


async def test_profile_typed(mock_http, client):
    mock_http.get(f"{BASE}/pro/twitter/alice/info").mock(return_value=ok({
        "name": "Alice", "twitterId": "42", "twitterUsername": "alice", "bio": "bio",
        "profilePhoto": None, "followersCount": 3, "followingsCount": 4,
        "primaryLabel": "KOL", "compactLabel": {"label": "trader"}, "extra": "preserved",
    }))
    result = await client.profile_info("alice")
    assert result.twitter_id == "42"
    assert result.followers_count == 3
    assert result.compact_label == {"label": "trader"}
    assert result.model_extra["extra"] == "preserved"


@pytest.mark.parametrize("status", [200, 404])
async def test_fomo_not_found(mock_http, client, status):
    mock_http.get(f"{BASE}/pro/wallet/fomo", params={"fomoUsername": "alice"}).mock(return_value=httpx.Response(
        status, json={"statusCode": 404, "errorCode": "FOMO_PROFILE_WALLETS_NOT_FOUND", "message": "No wallets"},
    ))
    assert (await client.fomo_wallets(username="alice")).wallets == []
    assert client.credits_spent == 100


@pytest.mark.parametrize("kwargs", [{}, {"username": "alice", "user_id": "42"}])
async def test_fomo_arguments(client, kwargs):
    with pytest.raises(ValueError):
        await client.fomo_wallets(**kwargs)


async def test_fomo_success(mock_http, client):
    mock_http.get(f"{BASE}/pro/wallet/fomo", params={"fomoUserId": "42"}).mock(return_value=ok({
        "wallets": [{"chain": "SOLANA", "address": "abc", "activeChains": ["SOLANA"]}],
    }))
    result = await client.fomo_wallets(user_id="42")
    assert result.wallets[0].active_chains == ["SOLANA"]
    assert client.credits_spent == 1000


async def test_ca_async_pending_then_ready(mock_http, client):
    mock_http.get(f"{BASE}/pro/twitter/alice/tweets-with-ca-cache").mock(side_effect=[ok(None), ok(ca_data())])
    assert await client.ca_history("alice") is None
    result = await client.ca_history("alice")
    assert result.cache_hit is True
    assert result.undeleted_ca_count == 1
    assert result.top_undeleted_tweets[0].tweet_time.tzinfo is not None
    assert client.credits_spent == 100


async def test_ca_sync_timeout(mock_http, client):
    route = mock_http.get(f"{BASE}/pro/twitter/alice/tweets-with-ca").mock(return_value=ok(ca_data()))
    assert (await client.ca_history("alice", sync=True)).source == "cache"
    assert route.calls[0].request.extensions["timeout"]["read"] == 120.0
    assert client.credits_spent == 100


async def test_ca_wait_timeout(mock_http, client, fake):
    route = mock_http.get(f"{BASE}/pro/twitter/alice/tweets-with-ca-cache").mock(return_value=ok(None))
    with pytest.raises(TimeoutError):
        await client.ca_history_wait("alice", poll_interval=90, max_wait=200)
    assert fake.sleeps == [90, 90, 20]
    assert fake.now == 200
    assert route.call_count == 3


async def test_ca_wait_ready(mock_http, client, fake):
    mock_http.get(f"{BASE}/pro/twitter/alice/tweets-with-ca-cache").mock(side_effect=[ok(None), ok(ca_data())])
    result = await client.ca_history_wait("alice")
    assert result.cache_hit is True
    assert fake.sleeps == [90]


async def test_high_pnl_decimal(mock_http, client):
    mock_http.get(f"{BASE}/pro/high-pnl", params={"chain": "BSC", "period": "30d", "limit": 5, "offset": 10}).mock(return_value=ok({
        "chain": "BSC", "period": "30d", "updatedAt": "2026-10-18T12:00:00Z", "total": 1,
        "wallets": [{"address": "0xabc", "pnlUsd": "123456.789123456789"}],
    }))
    result = await client.high_pnl(chain="BSC", period="30d", limit=5, offset=10)
    assert result.wallets[0].pnl_usd == Decimal("123456.789123456789")
    assert result.updated_at.tzinfo is not None


@pytest.mark.parametrize("kwargs", [{"chain": "ETH"}, {"period": "2d"}, {"limit": 0}, {"limit": 501}, {"offset": -1}])
async def test_high_pnl_validation(client, kwargs):
    with pytest.raises(ValueError):
        await client.high_pnl(**kwargs)


async def test_credits_across_calls_and_batch_body(mock_http, client):
    mock_http.get(f"{BASE}/pro/trending-accounts").mock(return_value=ok([]))
    mock_http.get(f"{BASE}/pro/twitter/alice/associated-wallets").mock(return_value=ok({"wallets": []}))
    mock_http.get(f"{BASE}/pro/twitter/alice/mentioned-wallets").mock(return_value=ok(None))
    batch = mock_http.post(f"{BASE}/pro/twitter/wallets-batch-query").mock(return_value=ok([]))
    await client.trending_accounts()
    await client.associated_wallets("alice")
    await client.mentioned_wallets("alice")
    await client.wallets_batch_query([("SOLANA", "abc"), ("EVM", "0x123")])
    assert json.loads(batch.calls[0].request.content) == {"wallets": [
        {"chain": "SOLANA", "address": "abc"}, {"chain": "EVM", "address": "0x123"},
    ]}
    assert client.credits_spent == 30 + 400 + 500 + 200
    assert CREDIT_COST["high_pnl"] == 5000


async def test_old_username_and_smart_followers(mock_http, client):
    mock_http.get(f"{BASE}/pro/twitter/lookup-by-old-username/old").mock(return_value=ok({
        "found": True, "profiles": [{"currentTwitterUsername": "alice", "twitterId": "42",
                                    "name": "Alice", "profilePhoto": None, "changedAt": "2026-10-18T12:00:00Z"}],
    }))
    mock_http.get(f"{BASE}/pro/twitter/alice/is-smart-follower", params={"twitterId": "42"}).mock(return_value=ok({"smart": True}))
    mock_http.get(f"{BASE}/pro/twitter/alice/smart-followers/count").mock(return_value=ok({"totalCount": 1, "meta": {"resolved": False}}))
    mock_http.get(f"{BASE}/pro/twitter/alice/smart-followers").mock(return_value=ok({
        "totalCount": 1, "meta": {"resolved": True}, "smartFollowers": [{
            "name": "Bob", "twitterId": "7", "twitter": "bob", "bio": "bio", "aiSummary": "summary",
            "profilePhoto": None, "followersCount": 10, "followingsCount": 20,
            "primaryLabel": None, "userDefinedLabel": "trader", "position": 1,
        }],
    }))
    lookup = await client.lookup_by_old_username("old")
    assert lookup.profiles[0].changed_at.tzinfo is not None
    assert lookup.profiles[0].current_twitter_username == "alice"
    assert await client.is_smart_follower("alice", twitter_id="42") == {"smart": True}
    assert (await client.smart_followers_count("alice")).meta.resolved is False
    followers = await client.smart_followers("alice")
    assert followers.meta.resolved is True
    assert followers.smart_followers[0].ai_summary == "summary"


async def test_from_settings_and_explicit_close():
    settings = SimpleNamespace(frontrun_api_key=SecretStr(KEY), frontrun_base_url=BASE, frontrun_rpm=20)
    client = FrontrunClient.from_settings(settings)
    assert client._rpm == 20
    assert str(client._client.base_url) == BASE + "/"
    await client.aclose()
    assert client._client.is_closed
    settings.frontrun_api_key = None
    with pytest.raises(ValueError):
        FrontrunClient.from_settings(settings)


async def test_transport_injection_and_no_retry_connection_error(mock_http, fake):
    transport = httpx.MockTransport(lambda request: ok([]))
    async with FrontrunClient(KEY, transport=transport, clock=fake.clock, sleep=fake.sleep) as client:
        assert await client.trending_accounts() == []
    mock_http.get(f"{BASE}/pro/trending-accounts").mock(side_effect=httpx.ConnectError(KEY))
    async with FrontrunClient(KEY, clock=fake.clock, sleep=fake.sleep) as client:
        with pytest.raises(FrontrunError) as caught:
            await client.trending_accounts()
        assert KEY not in str(caught.value)
        assert fake.sleeps == []


async def test_invalid_json_and_model_errors_are_safe(mock_http, client):
    route = mock_http.get(f"{BASE}/user/paid-api/points/{KEY}").mock(side_effect=[
        httpx.Response(200, text=KEY), ok({"currentPoints": KEY}),
    ])
    for _ in range(2):
        with pytest.raises(FrontrunError) as caught:
            await client.get_points()
        assert KEY not in str(caught.value)
    assert route.call_count == 2


async def test_other_input_validation(client):
    with pytest.raises(ValueError):
        await client.trending_accounts("2h")
    with pytest.raises(ValueError):
        await client.wallets_batch_query([("BTC", "abc")])
    with pytest.raises(ValueError):
        await client.ca_history_wait("alice", poll_interval=0)


async def test_partial_payload_does_not_break_parsing(client, mock_http):
    # В реальном ответе может не быть части полей из примера документации.
    mock_http.get(f"{BASE}/pro/twitter/tweets/search").mock(
        return_value=ok({"tweets": [{"tweet_id": "1", "text": "CA: x", "newField": 5}]})
    )
    page = await client.search_tweets("x")
    assert page.tweets[0].tweet_id == "1" and page.tweets[0].user_info is None
    assert page.next_cursor is None


async def test_ca_history_real_nested_envelope(client, mock_http):
    # Формат, который реально отдаёт Frontrun (18.10.2026): история вложена в data.data.
    route = mock_http.get(f"{BASE}/pro/twitter/alice/tweets-with-ca-cache")
    route.mock(return_value=ok({"cacheHit": False, "data": None}))
    assert await client.ca_history("alice") is None
    inner = {k: v for k, v in ca_data().items() if k != "cacheHit"}
    route.mock(return_value=ok({"cacheHit": True, "data": inner}))
    history = await client.ca_history("alice")
    assert history.cache_hit is True and history.undeleted_ca_count == inner["undeletedCACount"]
    assert len(history.top_undeleted_tweets) == len(inner["topUndeletedTweets"])
    assert client.credits_spent == 100

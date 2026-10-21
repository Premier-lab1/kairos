"""Async Frontrun REST client with rolling rate limits and estimated credits."""

from __future__ import annotations

import asyncio
import functools
import ssl
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, field_validator
from pydantic.alias_generators import to_camel


CREDIT_COST = {
    "get_points": 0, "search_tweets": 2, "trending_accounts": 30,
    "is_smart_follower": 5, "lookup_by_old_username": 10, "profile_info": 10,
    "wallets_batch_query": 100, "associated_wallets": 400,
    "mentioned_wallets": 500, "smart_followers_count": 3, "smart_followers": 10,
    "fomo_wallets": 1000, "fomo_wallets_not_found": 100,
    "ca_history": 50, "ca_history_sync": 100, "high_pnl": 5000, "health": 0,
}
RawData = dict[str, Any] | list[Any] | None


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True, alias_generator=to_camel)


class Points(ApiModel):
    current_points: int | None = None
    included_credits: str | None = None
    top_up_credits: str | None = None
    total_credits: str | None = None


class TweetUser(ApiModel):
    screen_name: str | None = None
    name: str | None = None
    description: str | None = None
    rest_id: str | None = None
    followers_count: int | None = None
    favourites_count: int | None = None
    avatar: str | None = None
    verified: bool | None = None
    friends_count: int | None = None
    location: str | None = None


class Tweet(ApiModel):
    type: str | None = None
    tweet_id: str | None = None
    url: str | None = None
    screen_name: str | None = None
    text: str | None = None
    lang: str | None = None
    created_at: datetime | None = None
    conversation_id: str | None = None
    retweets: int | None = None
    replies: int | None = None
    quotes: int | None = None
    favorites: int | None = None
    bookmarks: int | None = None
    views: str | None = None
    entities: Any = None
    media: Any = None
    user_info: TweetUser | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_twitter_date(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
            except ValueError:
                pass
        return value


class TweetSearch(ApiModel):
    tweets: list[Tweet] = []
    next_cursor: str | None = None


class OldUsernameProfile(ApiModel):
    current_twitter_username: str | None = None
    twitter_id: str | None = None
    name: str | None = None
    profile_photo: str | None = None
    changed_at: datetime | None = None


class OldUsernameLookup(ApiModel):
    found: bool | None = None
    profiles: list[OldUsernameProfile] = []


class ProfileInfo(ApiModel):
    name: str | None = None
    twitter_id: str | None = None
    twitter_username: str | None = None
    bio: str | None = None
    profile_photo: str | None = None
    followers_count: int | None = None
    followings_count: int | None = None
    primary_label: Any = None
    compact_label: Any = None


class ResolutionMeta(ApiModel):
    resolved: bool | None = None


class SmartFollowersCount(ApiModel):
    total_count: int | None = None
    meta: ResolutionMeta | None = None


class SmartFollower(ApiModel):
    name: str | None = None
    twitter_id: str | None = None
    twitter: str | None = None
    bio: str | None = None
    ai_summary: str | None = None
    profile_photo: str | None = None
    followers_count: int | None = None
    followings_count: int | None = None
    primary_label: Any = None
    user_defined_label: Any = None
    position: Any = None


class SmartFollowers(SmartFollowersCount):
    smart_followers: list[SmartFollower] = []


class FomoWallet(ApiModel):
    chain: str | None = None
    address: str | None = None
    active_chains: list[str] = []


class FomoWallets(ApiModel):
    wallets: list[FomoWallet] = []


class CaTweet(ApiModel):
    tweet_id: str | None = None
    twitter_handle: str | None = None
    chain: str | None = None
    ca: str | None = None
    content: str | None = None
    is_deleted: bool | None = None
    quote_count: int | None = None
    reply_count: int | None = None
    retweet_count: int | None = None
    favorite_count: int | None = None
    tweet_time: datetime | None = None


class CaHistory(ApiModel):
    source: str | None = None
    top_undeleted_tweets: list[CaTweet] = []
    top_deleted_tweets: list[CaTweet] = []
    deleted_ca_count: int | None = None
    deleted_tweet_count: int | None = None
    undeleted_ca_count: int | None = None
    undeleted_tweet_count: int | None = None
    cache_hit: bool | None = None

    # CA is an acronym in the API's counters.
    model_config = ConfigDict(extra="allow", populate_by_name=True,
                             alias_generator=lambda name: to_camel(name).replace("CaCount", "CACount"))


class PnlWallet(ApiModel):
    address: str | None = None
    pnl_usd: Decimal | None = None


class HighPnl(ApiModel):
    chain: str | None = None
    period: str | None = None
    updated_at: datetime | None = None
    total: int | None = None
    wallets: list[PnlWallet] = []


class FrontrunError(Exception):
    """A sanitized HTTP, API, transport, or response validation failure."""

    def __init__(self, http_status: int | None, error_code: str | None,
                 message: str, endpoint: str):
        self.http_status = http_status
        self.error_code = error_code
        self.message = message
        self.endpoint = endpoint
        super().__init__(f"Frontrun {http_status} {error_code or ''}: {message} ({endpoint})")


class _PrivatePathTransport(httpx.AsyncBaseTransport):
    """Expose only a redacted points URL to HTTPX's request logger."""

    def __init__(self, inner: httpx.AsyncBaseTransport, key: str):
        self.inner = inner
        self.key = SecretStr(key)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        original = request.url
        if original.path.endswith("/user/paid-api/points/REDACTED"):
            request.url = original.copy_with(
                raw_path=original.raw_path.replace(b"REDACTED", quote(self.key.get_secret_value(), safe="").encode())
            )
        try:
            return await self.inner.handle_async_request(request)
        finally:
            request.url = original

    async def aclose(self) -> None:
        await self.inner.aclose()


@functools.cache
def _ssl_context() -> ssl.SSLContext:
    # Создание SSL-контекста занимает около секунды (загрузка сертификатов), поэтому он один на процесс.
    return httpx.create_ssl_context()


class FrontrunClient:
    def __init__(self, api_key: str, base_url: str = "https://api.frontrun.pro/api/v1",
                 rpm: int = 60, timeout: float = 30.0, max_retries: int = 3,
                 transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        if not api_key or rpm < 1 or max_retries < 0 or timeout <= 0:
            raise ValueError("A key, positive rpm/timeout and nonnegative max_retries are required")
        self._key = SecretStr(api_key)
        self._clock, self._sleep = clock, sleep
        self._rpm, self._max_retries = rpm, max_retries
        self._requests: deque[float] = deque()
        self._lock = asyncio.Lock()
        self.credits_spent = 0
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/", timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "accept": "application/json",
                     "X-Copilot-Client-Language": "en"},
            transport=_PrivatePathTransport(transport if transport is not None else httpx.AsyncHTTPTransport(verify=_ssl_context()), api_key),
        )

    @classmethod
    def from_settings(cls, settings: Any) -> FrontrunClient:
        if settings.frontrun_api_key is None:
            raise ValueError("frontrun_api_key is required")
        return cls(settings.frontrun_api_key.get_secret_value(),
                   base_url=settings.frontrun_base_url, rpm=settings.frontrun_rpm)

    async def __aenter__(self) -> FrontrunClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _redact(self, value: Any) -> str:
        key = self._key.get_secret_value()
        return str(value).replace(key, "REDACTED").replace(quote(key, safe=""), "REDACTED")

    async def _acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                while self._requests and now - self._requests[0] >= 60:
                    self._requests.popleft()
                if len(self._requests) < self._rpm:
                    self._requests.append(now)
                    return
                await self._sleep(max(0.0, 60 - (now - self._requests[0])))

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        delay = float(2 ** attempt)
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                retry_delay = float(retry_after)
            except ValueError:
                try:
                    retry_delay = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    return delay
            delay = max(delay, retry_delay)
        return delay

    async def _request(self, endpoint: str, cost: int, model: type[ApiModel] | None = None,
                       method: str = "GET", **kwargs: Any) -> Any:
        safe_endpoint = self._redact(endpoint)
        for attempt in range(self._max_retries + 1):
            await self._acquire()
            try:
                response = await self._client.request(method, endpoint.lstrip("/"), **kwargs)
            except httpx.RequestError as exc:
                if isinstance(exc, httpx.TimeoutException) and attempt < self._max_retries:
                    await self._sleep(float(2 ** attempt))
                    continue
                raise FrontrunError(None, type(exc).__name__, "Transport request failed", safe_endpoint) from None
            if (response.status_code == 429 or response.status_code >= 500) and attempt < self._max_retries:
                await self._sleep(self._retry_delay(response, attempt))
                continue
            try:
                body = response.json()
            except ValueError:
                raise FrontrunError(response.status_code, None, "Invalid JSON response", safe_endpoint) from None
            if not isinstance(body, dict):
                raise FrontrunError(response.status_code, None, "Invalid response envelope", safe_endpoint)
            if not response.is_success or body.get("status") is False or body.get("errorCode") or body.get("statusCode", 200) >= 400:
                raise FrontrunError(response.status_code,
                                    self._redact(body["errorCode"]) if body.get("errorCode") else None,
                                    self._redact(body.get("message", "Request failed")), safe_endpoint)
            data = body.get("data")
            self.credits_spent += cost
            if model is None or data is None:
                return data
            try:
                return model.model_validate(data)
            except ValidationError:
                raise FrontrunError(response.status_code, None, "Invalid response data", safe_endpoint) from None

    async def get_points(self) -> Points:
        return await self._request("/user/paid-api/points/REDACTED", CREDIT_COST["get_points"], Points)

    async def search_tweets(self, q: str, page_size: int = 20, cursor: str | None = None) -> TweetSearch:
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        params: dict[str, Any] = {"q": q, "page_size": page_size}
        if cursor is not None:
            params["cursor"] = cursor
        return await self._request("/pro/twitter/tweets/search", CREDIT_COST["search_tweets"], TweetSearch, params=params)

    async def trending_accounts(self, window: str = "24h") -> RawData:
        if window not in {"1h", "3h", "6h", "24h", "7d"}:
            raise ValueError("Invalid trending window")
        return await self._request("/pro/trending-accounts", CREDIT_COST["trending_accounts"], params={"window": window})

    async def is_smart_follower(self, u: str, twitter_id: str | None = None) -> RawData:
        return await self._request(f"/pro/twitter/{quote(u, safe='')}/is-smart-follower", CREDIT_COST["is_smart_follower"],
                                   params={"twitterId": twitter_id} if twitter_id is not None else {})

    async def lookup_by_old_username(self, old: str) -> OldUsernameLookup:
        return await self._request(f"/pro/twitter/lookup-by-old-username/{quote(old, safe='')}", CREDIT_COST["lookup_by_old_username"], OldUsernameLookup)

    async def profile_info(self, u: str, twitter_id: str | None = None) -> ProfileInfo | None:
        return await self._request(f"/pro/twitter/{quote(u, safe='')}/info", CREDIT_COST["profile_info"], ProfileInfo,
                                   params={"twitterId": twitter_id} if twitter_id is not None else {})

    async def wallets_batch_query(self, wallets: list[tuple[str, str]]) -> RawData:
        """Estimate an upper bound of 100 credits per input wallet, matched or not."""
        if any(chain not in {"SOLANA", "EVM"} for chain, _ in wallets):
            raise ValueError("Wallet chain must be SOLANA or EVM")
        return await self._request("/pro/twitter/wallets-batch-query", CREDIT_COST["wallets_batch_query"] * len(wallets),
                                   method="POST", json={"wallets": [{"chain": c, "address": a} for c, a in wallets]})

    async def associated_wallets(self, u: str) -> RawData:
        return await self._request(f"/pro/twitter/{quote(u, safe='')}/associated-wallets", CREDIT_COST["associated_wallets"])

    async def mentioned_wallets(self, u: str) -> RawData:
        return await self._request(f"/pro/twitter/{quote(u, safe='')}/mentioned-wallets", CREDIT_COST["mentioned_wallets"])

    async def smart_followers_count(self, u: str) -> SmartFollowersCount:
        return await self._request(f"/pro/twitter/{quote(u, safe='')}/smart-followers/count", CREDIT_COST["smart_followers_count"], SmartFollowersCount)

    async def smart_followers(self, u: str) -> SmartFollowers:
        return await self._request(f"/pro/twitter/{quote(u, safe='')}/smart-followers", CREDIT_COST["smart_followers"], SmartFollowers)

    async def fomo_wallets(self, username: str | None = None, user_id: str | None = None) -> FomoWallets:
        if (username is None) == (user_id is None):
            raise ValueError("Exactly one of username and user_id is required")
        params = {"fomoUsername": username} if username is not None else {"fomoUserId": user_id}
        try:
            return await self._request("/pro/wallet/fomo", CREDIT_COST["fomo_wallets"], FomoWallets, params=params)
        except FrontrunError as exc:
            if exc.error_code != "FOMO_PROFILE_WALLETS_NOT_FOUND":
                raise
            self.credits_spent += CREDIT_COST["fomo_wallets_not_found"]
            return FomoWallets(wallets=[])

    async def ca_history(self, u: str, sync: bool = False) -> CaHistory | None:
        if sync:
            return await self._request(f"/pro/twitter/{quote(u, safe='')}/tweets-with-ca",
                                       CREDIT_COST["ca_history_sync"], CaHistory, timeout=120.0)
        # Асинхронный режим отдаёт двойную обёртку (проверено 18.10.2026):
        # {"data": {"cacheHit": bool, "data": {...история...} | null}}; внутренний null — сбор ещё идёт.
        data = await self._request(f"/pro/twitter/{quote(u, safe='')}/tweets-with-ca-cache",
                                   CREDIT_COST["ca_history"])
        if not isinstance(data, dict):
            return None
        if "data" in data:
            inner = data["data"]
            if not isinstance(inner, dict):
                return None
            data = {**inner, "cacheHit": data.get("cacheHit")}
        try:
            return CaHistory.model_validate(data)
        except ValidationError:
            raise FrontrunError(200, None, "Invalid response data", f"/pro/twitter/{u}/tweets-with-ca-cache") from None

    async def ca_history_wait(self, u: str, poll_interval: float = 90, max_wait: float = 300) -> CaHistory:
        """Poll the cache; every successful poll consumes its documented credits."""
        if poll_interval <= 0 or max_wait <= 0:
            raise ValueError("poll_interval and max_wait must be positive")
        deadline = self._clock() + max_wait
        while self._clock() < deadline:
            async with asyncio.timeout(deadline - self._clock()):
                result = await self.ca_history(u)
            if result is not None:
                return result
            await self._sleep(min(poll_interval, max(0.0, deadline - self._clock())))
        raise TimeoutError("CA history was not ready before max_wait")

    async def high_pnl(self, chain: str = "SOL", period: str = "7d", limit: int = 100, offset: int = 0) -> HighPnl:
        if chain not in {"SOL", "BSC"} or period not in {"1d", "7d", "30d"} or not 1 <= limit <= 500 or offset < 0:
            raise ValueError("Invalid high_pnl chain, period, limit or offset")
        return await self._request("/pro/high-pnl", CREDIT_COST["high_pnl"], HighPnl,
                                   params={"chain": chain, "period": period, "limit": limit, "offset": offset})

"""TwitterAPI.io client — search tweets and get user profiles.

Uses TwitterAPI.io (not official X API) for affordable access.
Pricing: pay-per-use, ~$0.15 per 1K tweets.
Docs: https://docs.twitterapi.io/
"""

import asyncio

import httpx
from loguru import logger

from src.parsers.rate_limiter import RateLimiter
from src.parsers.twitter.models import (
    TwitterAuthor,
    TwitterSearchResult,
    TwitterTweet,
)

# KOL threshold: accounts with 50K+ followers
KOL_FOLLOWER_THRESHOLD = 50_000
# Viral threshold: tweet with 1K+ likes
VIRAL_LIKE_THRESHOLD = 1_000

MAX_RETRIES = 2
RETRY_DELAYS = [1.0, 3.0]


class TwitterApiError(Exception):
    """TwitterAPI.io error."""


class TwitterClient:
    """HTTP client for TwitterAPI.io — tweet search and user info."""

    BASE_URL = "https://api.twitterapi.io"

    def __init__(
        self,
        api_key: str,
        max_rps: float = 1.0,
    ) -> None:
        self._rate_limiter = RateLimiter(max_rps)
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=15.0,
            headers={
                "X-API-Key": api_key,
                "Accept": "application/json",
            },
        )

    async def _request(self, method: str, path: str, **kwargs: object) -> dict:
        """Make rate-limited API request with retry on transient errors."""
        for attempt in range(MAX_RETRIES + 1):
            await self._rate_limiter.acquire()
            try:
                response = await self._client.request(method, path, **kwargs)
                if response.status_code == 429:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAYS[attempt])
                        continue
                    raise TwitterApiError("Rate limited (429)")
                if response.status_code >= 500 and attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue
                response.raise_for_status()
                data = response.json()
                if data.get("status") == "error":
                    raise TwitterApiError(data.get("msg", "Unknown error"))
                return data
            except httpx.HTTPStatusError as e:
                raise TwitterApiError(
                    f"HTTP {e.response.status_code}: {e.response.text[:200]}"
                ) from e
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue
                raise TwitterApiError(f"Request failed after {MAX_RETRIES + 1} attempts: {e}") from e
            except httpx.RequestError as e:
                raise TwitterApiError(f"Request failed: {e}") from e
        raise TwitterApiError("Max retries exceeded")

    async def search_token(
        self,
        symbol: str | None = None,
        name: str | None = None,
        mint_address: str | None = None,
    ) -> TwitterSearchResult:
        """Search Twitter for token mentions.

        Builds a query from symbol/name, filters out retweets,
        and aggregates engagement metrics.
        """
        query = self._build_query(symbol, name, mint_address)
        if not query:
            return TwitterSearchResult()

        try:
            data = await self._request(
                "GET",
                "/twitter/tweet/advanced_search",
                params={
                    "query": query,
                    "queryType": "Latest",
                    "cursor": "",
                },
            )
        except TwitterApiError as e:
            logger.debug(f"[TWITTER] Search failed for {symbol}: {e}")
            return TwitterSearchResult()

        return self._parse_search_response(data)

    async def get_user_info(self, username: str) -> TwitterAuthor | None:
        """Get user profile by username."""
        try:
            data = await self._request(
                "GET",
                "/twitter/user/info",
                params={"userName": username},
            )
            user_data = data.get("data", data)
            if isinstance(user_data, dict):
                return TwitterAuthor.model_validate(user_data)
            return None
        except TwitterApiError:
            return None

    def _build_query(
        self,
        symbol: str | None,
        name: str | None,
        mint_address: str | None,
    ) -> str:
        """Build Twitter search query for a token.

        Strategy: search for "$SYMBOL" (cashtag) OR "token_name" (if unique enough).
        Filter: exclude retweets, require some engagement (min_faves:3).
        """
        parts = []

        if symbol and len(symbol) >= 2:
            clean_symbol = symbol.strip().upper()
            if clean_symbol not in {"SOL", "BTC", "ETH", "USD", "USDT", "USDC"}:
                parts.append(f'"${clean_symbol}"')

        if name and len(name) >= 3:
            clean_name = name.strip()
            if len(clean_name) <= 20 and clean_name.lower() not in {
                "token", "coin", "test", "swap", "the"
            }:
                parts.append(f'"{clean_name}"')

        if not parts and mint_address:
            parts.append(f'"{mint_address[:12]}"')

        if not parts:
            return ""

        query = " OR ".join(parts)
        query += " -filter:retweets min_faves:3 lang:en"
        return query

    def _parse_search_response(self, data: dict) -> TwitterSearchResult:
        """Parse API response into aggregated result."""
        result = TwitterSearchResult()

        # API returns {tweets: [...]} directly or {data: {tweets: [...]}}
        if "tweets" in data:
            raw_tweets = data.get("tweets", [])
            result.has_next_page = data.get("has_next_page", False)
        elif "data" in data:
            tweets_data = data["data"]
            if isinstance(tweets_data, dict):
                raw_tweets = tweets_data.get("tweets", [])
                result.has_next_page = tweets_data.get("has_next_page", False)
            elif isinstance(tweets_data, list):
                raw_tweets = tweets_data
            else:
                return result
        else:
            return result

        if not raw_tweets:
            return result

        tweets: list[TwitterTweet] = []
        kol_count = 0
        verified_count = 0
        max_likes = 0
        max_views = 0
        total_engagement = 0
        top_kol_followers = 0

        for raw in raw_tweets:
            try:
                tweet = TwitterTweet.model_validate(raw)
                tweets.append(tweet)

                engagement = (
                    tweet.likeCount
                    + tweet.retweetCount
                    + tweet.replyCount
                    + tweet.quoteCount
                )
                total_engagement += engagement
                max_likes = max(max_likes, tweet.likeCount)
                max_views = max(max_views, tweet.viewCount)

                if tweet.author.followers >= KOL_FOLLOWER_THRESHOLD:
                    kol_count += 1
                    top_kol_followers = max(top_kol_followers, tweet.author.followers)

                if tweet.author.isBlueVerified:
                    verified_count += 1

            except Exception:
                continue

        result.tweets = tweets
        result.total_tweets = len(tweets)
        result.kol_mentions = kol_count
        result.verified_mentions = verified_count
        result.max_likes = max_likes
        result.max_views = max_views
        result.total_engagement = total_engagement
        result.top_kol_followers = top_kol_followers

        return result

    async def close(self) -> None:
        await self._client.aclose()

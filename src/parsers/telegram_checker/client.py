"""Telegram group/channel checker via RapidAPI.

Uses: https://rapidapi.com/akrakoro/api/telegram-channel
Ultra plan ($15/mo): 5 RPS.
"""

import asyncio

import httpx
from loguru import logger

from src.parsers.rate_limiter import RateLimiter

MAX_RETRIES = 2
RETRY_DELAYS = [1.0, 3.0]


class TelegramCheckResult:
    """Result of Telegram group/channel check."""

    def __init__(self) -> None:
        self.exists: bool = False
        self.title: str = ""
        self.member_count: int = 0
        self.description: str = ""
        self.is_verified: bool = False
        self.recent_messages: int = 0  # messages in last fetch


class TelegramCheckerClient:
    """HTTP client for Telegram Channel API on RapidAPI."""

    BASE_URL = "https://telegram-channel.p.rapidapi.com"

    def __init__(
        self,
        rapidapi_key: str,
        max_rps: float = 0.9,
    ) -> None:
        self._rate_limiter = RateLimiter(max_rps)
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=15.0,
            headers={
                "x-rapidapi-key": rapidapi_key,
                "x-rapidapi-host": "telegram-channel.p.rapidapi.com",
            },
        )

    async def _request(self, path: str, params: dict) -> httpx.Response | None:
        """Execute GET with retry on timeout/429."""
        for attempt in range(MAX_RETRIES + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.get(path, params=params)
                if resp.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[TG_CHECK] Rate limited, retrying in {delay}s")
                    await asyncio.sleep(delay)
                    continue
                return resp
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[TG_CHECK] {type(e).__name__}, retry in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"[TG_CHECK] Failed after retries: {e}")
                    return None
        return None

    async def check_channel(self, username: str) -> TelegramCheckResult:
        """Check Telegram channel/group by username.

        Args:
            username: Telegram username without @ (e.g. "solana")
        """
        result = TelegramCheckResult()

        if not username:
            return result

        # Clean username
        username = username.strip().lstrip("@")
        if "/" in username:
            username = username.rstrip("/").split("/")[-1]

        # 1. Get channel info
        try:
            resp = await self._request("/channel/info", {"channel": username})
            if not resp or resp.status_code != 200:
                return result

            data = resp.json()
            if not data or data.get("err_msg"):
                return result

            result.exists = True
            result.title = data.get("title", "")
            subs = data.get("subscribers") or data.get("members_count") or "0"
            try:
                result.member_count = int(subs)
            except (ValueError, TypeError):
                result.member_count = 0
            result.description = data.get("description", "")
            result.is_verified = data.get("verified", False) or data.get("is_verified", False)

        except Exception as e:
            logger.debug(f"[TG_CHECK] Channel info failed for {username}: {e}")
            return result

        # 2. Get recent messages to check activity
        try:
            resp = await self._request(
                "/channel/message",
                {"channel": username, "limit": "10", "max_id": "999999999"},
            )
            if resp and resp.status_code == 200:
                messages = resp.json()
                if isinstance(messages, list):
                    result.recent_messages = len(messages)
                elif isinstance(messages, dict) and not messages.get("message"):
                    msgs = messages.get("messages", [])
                    result.recent_messages = len(msgs) if isinstance(msgs, list) else 0

        except Exception as e:
            logger.debug(f"[TG_CHECK] Messages failed for {username}: {e}")

        return result

    async def close(self) -> None:
        await self._client.aclose()

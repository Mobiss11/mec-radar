"""Helius API client — enhanced transaction parsing for Solana."""

import asyncio
from typing import Any

import httpx
from loguru import logger

from src.parsers.helius.models import (
    HeliusNativeTransfer,
    HeliusSignature,
    HeliusTokenTransfer,
    HeliusTransaction,
)
from src.parsers.rate_limiter import RateLimiter

MAX_RETRIES = 2
RETRY_DELAYS = [1.0, 3.0]


class HeliusClient:
    """Async HTTP client for Helius Enhanced API."""

    def __init__(self, api_key: str, rpc_url: str = "", max_rps: float = 10.0) -> None:
        self._api_key = api_key
        self._rpc_url = rpc_url or f"https://mainnet.helius-rpc.com/?api-key={api_key}"
        self._api_url = f"https://api.helius.xyz/v0"
        self._rate_limiter = RateLimiter(max_rps)
        self._client = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_parsed_transactions(
        self, signatures: list[str]
    ) -> list[HeliusTransaction]:
        """Fetch enhanced parsed transactions by signatures (max 100)."""
        url = f"{self._api_url}/transactions?api-key={self._api_key}"
        payload = {"transactions": signatures[:100]}

        for attempt in range(MAX_RETRIES + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.post(url, json=payload)

                if resp.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    await asyncio.sleep(delay)
                    continue
                if resp.status_code != 200:
                    logger.debug(f"[HELIUS] HTTP {resp.status_code} for parsed txs")
                    return []

                return [_parse_tx(tx) for tx in resp.json()]

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                else:
                    logger.warning(f"[HELIUS] get_parsed_transactions failed: {e}")
                    return []

        return []

    async def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        """Fetch asset metadata via Helius DAS (Digital Asset Standard) API.

        Uses the getAsset JSON-RPC method on the Helius RPC endpoint.
        Returns the full asset object or None if not found/error.

        Cost: 10 Helius credits per call.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAsset",
            "params": {"id": asset_id},
        }

        for attempt in range(MAX_RETRIES + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.post(self._rpc_url, json=payload)

                if resp.status_code == 429:
                    await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                    continue
                if resp.status_code != 200:
                    logger.debug(f"[HELIUS] get_asset HTTP {resp.status_code}")
                    return None

                data = resp.json()
                if "error" in data:
                    logger.debug(f"[HELIUS] get_asset RPC error: {data['error']}")
                    return None

                return data.get("result")

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                else:
                    logger.warning(f"[HELIUS] get_asset failed: {e}")
                    return None

        return None

    async def get_signatures_for_address(
        self, address: str, *, limit: int = 50, before: str = ""
    ) -> list[HeliusSignature]:
        """Fetch transaction signatures for an address via RPC."""
        params: dict[str, Any] = {"limit": min(limit, 1000)}
        if before:
            params["before"] = before

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [address, params],
        }

        for attempt in range(MAX_RETRIES + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.post(self._rpc_url, json=payload)

                if resp.status_code == 429:
                    await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                    continue
                if resp.status_code != 200:
                    return []

                data = resp.json()
                result = data.get("result", [])
                return [
                    HeliusSignature(
                        signature=sig.get("signature", ""),
                        slot=sig.get("slot", 0),
                        timestamp=sig.get("blockTime", 0),
                        err=sig.get("err"),
                    )
                    for sig in result
                ]

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                else:
                    logger.warning(f"[HELIUS] get_signatures failed: {e}")
                    return []

        return []


def _parse_tx(data: dict) -> HeliusTransaction:
    """Parse raw Helius enhanced transaction."""
    token_transfers = [
        HeliusTokenTransfer(
            from_user_account=t.get("fromUserAccount", ""),
            to_user_account=t.get("toUserAccount", ""),
            from_token_account=t.get("fromTokenAccount", ""),
            to_token_account=t.get("toTokenAccount", ""),
            token_amount=t.get("tokenAmount", 0),
            mint=t.get("mint", ""),
            token_standard=t.get("tokenStandard", ""),
        )
        for t in data.get("tokenTransfers", [])
    ]

    native_transfers = [
        HeliusNativeTransfer(
            from_user_account=t.get("fromUserAccount", ""),
            to_user_account=t.get("toUserAccount", ""),
            amount=t.get("amount", 0),
        )
        for t in data.get("nativeTransfers", [])
    ]

    return HeliusTransaction(
        signature=data.get("signature", ""),
        type=data.get("type", ""),
        source=data.get("source", ""),
        fee=data.get("fee", 0),
        fee_payer=data.get("feePayer", ""),
        timestamp=data.get("timestamp", 0),
        description=data.get("description", ""),
        token_transfers=token_transfers,
        native_transfers=native_transfers,
        transaction_error=data.get("transactionError"),
    )

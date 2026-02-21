"""REST client for Solana RPC (getTransaction, getAccountInfo) and Meteora DAMM v2 API."""

import asyncio

import httpx
from loguru import logger

from src.parsers.meteora.constants import DAMM_V2_BASE_URL
from src.parsers.meteora.decoder import decode_virtual_pool
from src.parsers.meteora.models import MeteoraDAMMPool, MeteoraVirtualPool
from src.parsers.rate_limiter import RateLimiter


class MeteoraClient:
    """Async client for Solana RPC + Meteora DAMM v2 REST API."""

    def __init__(
        self,
        solana_rpc_url: str,
        rate_limiter: RateLimiter | None = None,
        max_rps: float = 5.0,
    ) -> None:
        self._rpc_url = solana_rpc_url
        self._rpc_client = httpx.AsyncClient(timeout=15.0)
        self._damm_client = httpx.AsyncClient(
            base_url=DAMM_V2_BASE_URL,
            timeout=10.0,
            headers={"Accept": "application/json"},
        )
        self._rate_limiter = rate_limiter or RateLimiter(max_rps)

    async def get_transaction(
        self, signature: str, *, retries: int = 4, initial_delay: float = 5.0
    ) -> dict | None:
        """Fetch parsed transaction via Solana RPC getTransaction.

        logsSubscribe delivers signatures before the RPC index is ready,
        so we wait ``initial_delay`` seconds before the first attempt and
        use exponential backoff on subsequent retries.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
            ],
        }
        await asyncio.sleep(initial_delay)
        delay = 5.0
        for attempt in range(retries):
            await self._rate_limiter.acquire()
            try:
                resp = await self._rpc_client.post(self._rpc_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                result = data.get("result")
                if result is not None:
                    return result
                if attempt < retries - 1:
                    await asyncio.sleep(delay)
                    delay *= 2  # 5 → 10 → 20
            except Exception as e:
                logger.debug(f"[MDBC] getTransaction failed for {signature[:16]}: {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(delay)
                    delay *= 2
        return None

    async def get_virtual_pool(self, pool_address: str) -> MeteoraVirtualPool | None:
        """Fetch and decode a VirtualPool account via getAccountInfo."""
        await self._rate_limiter.acquire()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [
                pool_address,
                {"encoding": "base64"},
            ],
        }
        try:
            resp = await self._rpc_client.post(self._rpc_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result")
            if not result or not result.get("value"):
                return None
            account_data = result["value"]["data"]
            if isinstance(account_data, list):
                b64_data = account_data[0]
            else:
                b64_data = account_data
            return decode_virtual_pool(pool_address, b64_data)
        except Exception as e:
            logger.debug(f"[MDBC] getAccountInfo failed for {pool_address[:16]}: {e}")
            return None

    async def get_damm_pool(self, pool_address: str) -> MeteoraDAMMPool | None:
        """Fetch post-graduation pool data from DAMM v2 REST API."""
        await self._rate_limiter.acquire()
        try:
            resp = await self._damm_client.get(f"/pools/{pool_address}")
            resp.raise_for_status()
            data = resp.json()
            return MeteoraDAMMPool.model_validate(data)
        except Exception as e:
            logger.debug(f"[MDBC] DAMM v2 pool fetch failed for {pool_address[:16]}: {e}")
            return None

    async def close(self) -> None:
        await self._rpc_client.aclose()
        await self._damm_client.aclose()

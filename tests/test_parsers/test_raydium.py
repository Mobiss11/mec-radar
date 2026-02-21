"""Tests for Raydium LP verification client."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.parsers.raydium.client import RaydiumClient, _parse_pool
from src.parsers.raydium.models import RaydiumPoolInfo


class TestRaydiumModels:
    def test_lp_burned(self) -> None:
        pool = RaydiumPoolInfo(burn_percent=85.0)
        assert pool.lp_burned is True

    def test_lp_not_burned(self) -> None:
        pool = RaydiumPoolInfo(burn_percent=10.0)
        assert pool.lp_burned is False


class TestRaydiumClient:
    @pytest.mark.asyncio
    async def test_pool_found_lp_burned(self) -> None:
        """Pool exists with burned LP."""
        client = RaydiumClient(max_rps=100.0)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "data": [
                    {
                        "id": "pool123",
                        "mintA": {"address": "TokenMint"},
                        "mintB": {"address": "SOLMint"},
                        "lpMint": {"address": "LPMint"},
                        "lpAmount": "1000000",
                        "tvl": 50000,
                        "burnPercent": 85.5,
                    }
                ]
            }
        }
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=mock_resp)

        pool = await client.get_pool_info("TokenMint")

        assert pool is not None
        assert pool.lp_burned is True
        assert pool.burn_percent == 85.5
        assert pool.tvl == Decimal("50000")

    @pytest.mark.asyncio
    async def test_no_pool(self) -> None:
        """Token has no Raydium pool."""
        client = RaydiumClient(max_rps=100.0)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"data": []}}
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=mock_resp)

        pool = await client.get_pool_info("NoPoolMint")
        assert pool is None

    @pytest.mark.asyncio
    async def test_lp_not_burned(self) -> None:
        """Pool with LP held (not burned)."""
        client = RaydiumClient(max_rps=100.0)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "data": [
                    {
                        "id": "pool456",
                        "mintA": {"address": "Token"},
                        "mintB": {"address": "SOL"},
                        "lpMint": {"address": "LP"},
                        "lpAmount": "500000",
                        "tvl": 20000,
                        "burnPercent": 0,
                    }
                ]
            }
        }
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=mock_resp)

        pool = await client.get_pool_info("Token")

        assert pool is not None
        assert pool.lp_burned is False

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        """Timeout returns None."""
        import httpx

        client = RaydiumClient(max_rps=100.0)
        client._client = AsyncMock()
        client._client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        pool = await client.get_pool_info("Mint")
        assert pool is None

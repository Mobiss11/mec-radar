"""Tests for Jupiter sell simulation — honeypot detection via quote API."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.parsers.jupiter.client import JupiterClient
from src.parsers.jupiter.models import SellSimResult


@pytest.fixture
def jupiter() -> JupiterClient:
    client = JupiterClient(max_rps=100.0)
    return client


class TestSimulateSell:
    """Tests for JupiterClient.simulate_sell."""

    @pytest.mark.asyncio
    async def test_sellable_token(self, jupiter: JupiterClient) -> None:
        """Token with valid route returns sellable=True."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "inputMint": "TokenMint123",
            "outputMint": "So11111111111111111111111111111111111111112",
            "outAmount": "500000000",  # 0.5 SOL
            "priceImpactPct": "2.5",
        }

        jupiter._client = AsyncMock()
        jupiter._client.get = AsyncMock(return_value=mock_resp)

        result = await jupiter.simulate_sell("TokenMint123", amount_tokens=1000)

        assert result.sellable is True
        assert result.output_amount == Decimal("0.5")
        assert result.price_impact_pct == 2.5
        assert result.error is None

    @pytest.mark.asyncio
    async def test_no_route_found(self, jupiter: JupiterClient) -> None:
        """No route = potential honeypot."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "error": "No route found for the given input and output mints"
        }

        jupiter._client = AsyncMock()
        jupiter._client.get = AsyncMock(return_value=mock_resp)

        result = await jupiter.simulate_sell("HoneypotMint")

        assert result.sellable is False
        assert result.error is not None
        assert "No route" in result.error

    @pytest.mark.asyncio
    async def test_timeout(self, jupiter: JupiterClient) -> None:
        """Timeout returns sellable=False with api_error=True."""
        import httpx

        jupiter._client = AsyncMock()
        jupiter._client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        result = await jupiter.simulate_sell("SlowMint")

        assert result.sellable is False
        assert result.error is not None
        assert result.api_error is True

    @pytest.mark.asyncio
    async def test_401_marks_api_error(self, jupiter: JupiterClient) -> None:
        """401 Unauthorized marks api_error=True (not a token problem)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        jupiter._client = AsyncMock()
        jupiter._client.get = AsyncMock(return_value=mock_resp)

        result = await jupiter.simulate_sell("AnyMint")

        assert result.sellable is False
        assert result.api_error is True
        assert "401" in result.error

    @pytest.mark.asyncio
    async def test_no_route_not_api_error(self, jupiter: JupiterClient) -> None:
        """400 'No route found' is a real token problem, not api_error."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": "No route found"}

        jupiter._client = AsyncMock()
        jupiter._client.get = AsyncMock(return_value=mock_resp)

        result = await jupiter.simulate_sell("HoneypotMint")

        assert result.sellable is False
        assert result.api_error is False

    @pytest.mark.asyncio
    async def test_high_price_impact(self, jupiter: JupiterClient) -> None:
        """High price impact token is sellable but risky."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "outAmount": "100000",  # Very small output
            "priceImpactPct": "45.0",
        }

        jupiter._client = AsyncMock()
        jupiter._client.get = AsyncMock(return_value=mock_resp)

        result = await jupiter.simulate_sell("LowLiqMint")

        assert result.sellable is True
        assert result.price_impact_pct == 45.0

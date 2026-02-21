"""Tests for Pump.fun creator history client."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.parsers.pumpfun.client import PumpfunClient, _parse_creator_history
from src.parsers.pumpfun.models import PumpfunCreatorHistory, PumpfunToken


class TestPumpfunModels:
    def test_dead_token(self) -> None:
        token = PumpfunToken(mint="X", usd_market_cap=50)
        assert token.is_dead is True

    def test_alive_token(self) -> None:
        token = PumpfunToken(mint="X", usd_market_cap=5000)
        assert token.is_dead is False

    def test_risk_boost_serial_scammer(self) -> None:
        history = PumpfunCreatorHistory(total_tokens=15, dead_token_count=12)
        assert history.is_serial_scammer is True
        assert history.risk_boost == 40

    def test_risk_boost_moderate(self) -> None:
        history = PumpfunCreatorHistory(total_tokens=5, dead_token_count=3)
        assert history.risk_boost == 10

    def test_risk_boost_clean(self) -> None:
        history = PumpfunCreatorHistory(total_tokens=2, dead_token_count=0)
        assert history.risk_boost == 0


class TestPumpfunClient:
    @pytest.mark.asyncio
    async def test_serial_scammer_detection(self) -> None:
        """Creator with many dead tokens = serial scammer."""
        client = PumpfunClient(max_rps=100.0)
        tokens_data = [
            {"mint": f"token{i}", "name": f"Dead{i}", "symbol": f"D{i}",
             "created_timestamp": 1000000 + i, "market_cap": 0, "usd_market_cap": 0}
            for i in range(12)
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = tokens_data
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=mock_resp)

        history = await client.get_creator_history("ScammerWallet")

        assert history is not None
        assert history.total_tokens == 12
        assert history.dead_token_count == 12
        assert history.is_serial_scammer is True

    @pytest.mark.asyncio
    async def test_clean_creator(self) -> None:
        """Creator with successful tokens."""
        client = PumpfunClient(max_rps=100.0)
        tokens_data = [
            {"mint": "token1", "name": "Good1", "symbol": "G1",
             "created_timestamp": 1000000, "market_cap": 50000, "usd_market_cap": 50000},
            {"mint": "token2", "name": "Good2", "symbol": "G2",
             "created_timestamp": 1000001, "market_cap": 30000, "usd_market_cap": 30000},
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = tokens_data
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=mock_resp)

        history = await client.get_creator_history("GoodWallet")

        assert history is not None
        assert history.dead_token_count == 0
        assert history.is_serial_scammer is False

    @pytest.mark.asyncio
    async def test_no_data(self) -> None:
        """Creator not found on pump.fun."""
        client = PumpfunClient(max_rps=100.0)
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=mock_resp)

        history = await client.get_creator_history("UnknownWallet")

        assert history is not None
        assert history.total_tokens == 0

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        """Timeout returns None."""
        import httpx

        client = PumpfunClient(max_rps=100.0)
        client._client = AsyncMock()
        client._client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        history = await client.get_creator_history("Wallet")
        assert history is None

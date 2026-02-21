"""Tests for SolSniffer API client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.parsers.solsniffer.client import SolSnifferClient, _parse_report
from src.parsers.solsniffer.models import SolSnifferReport


class TestSolSnifferParsing:
    def test_parse_report(self) -> None:
        """Parse typical SolSniffer response."""
        data = {
            "tokenData": {
                "snifScore": 85,
                "isMintable": False,
                "isFreezable": False,
                "isMutableMetadata": True,
                "lpBurned": True,
                "top10Percentage": 45.2,
                "liquidityUsd": 50000,
                "topHolders": [
                    {"address": "Holder1", "percentage": 15.0, "isContract": False},
                    {"address": "Holder2", "percentage": 10.0, "isContract": True},
                ],
            }
        }
        report = _parse_report(data)

        assert report.snifscore == 85
        assert report.is_mintable is False
        assert report.lp_burned is True
        assert len(report.top_holders) == 2
        assert report.top_holders[0].address == "Holder1"

    def test_parse_empty_response(self) -> None:
        """Empty response should return default values."""
        report = _parse_report({})
        assert report.snifscore == 0
        assert report.top_holders == []


class TestSolSnifferClient:
    @pytest.mark.asyncio
    async def test_monthly_cap_enforced(self) -> None:
        """Should not make API calls after monthly cap is reached."""
        client = SolSnifferClient(api_key="test_key")
        client._monthly_calls = 100

        result = await client.get_token_audit("SomeTokenMint", monthly_cap=100)

        assert result is None
        await client.close()

    @pytest.mark.asyncio
    async def test_successful_call(self) -> None:
        """Mock successful API call."""
        client = SolSnifferClient(api_key="test_key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "tokenData": {"snifScore": 75, "isMintable": False}
        }

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_token_audit("TestMint123")

        assert result is not None
        assert result.snifscore == 75
        assert client.monthly_calls == 1
        await client.close()

    @pytest.mark.asyncio
    async def test_invalid_api_key(self) -> None:
        """401 response should return None."""
        client = SolSnifferClient(api_key="bad_key")

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_token_audit("TestMint123")

        assert result is None
        await client.close()

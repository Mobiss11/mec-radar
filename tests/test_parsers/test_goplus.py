"""Tests for GoPlus Security API client."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.parsers.goplus.client import GoPlusClient, _parse_bool, _parse_report, _parse_tax
from src.parsers.goplus.models import GoPlusReport


class TestParsers:
    def test_parse_bool(self) -> None:
        assert _parse_bool("1") is True
        assert _parse_bool("0") is False
        assert _parse_bool(None) is None
        assert _parse_bool("") is None

    def test_parse_tax(self) -> None:
        assert _parse_tax("0.05") == 5.0  # 5%
        assert _parse_tax("0") == 0.0
        assert _parse_tax(None) is None


class TestGoPlusClient:
    @pytest.mark.asyncio
    async def test_honeypot_detection(self) -> None:
        """GoPlus detects honeypot token."""
        client = GoPlusClient(max_rps=100.0)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "code": 1,
            "result": {
                "TokenMint123": {
                    "is_honeypot": "1",
                    "is_open_source": "0",
                    "is_proxy": "0",
                    "is_mintable": "1",
                    "buy_tax": "0",
                    "sell_tax": "1.0",
                    "holder_count": "50",
                    "lp_holder_count": "3",
                }
            },
        }
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=mock_resp)

        report = await client.get_token_security("TokenMint123")

        assert report is not None
        assert report.is_honeypot is True
        assert report.is_mintable is True
        assert report.sell_tax == 100.0  # 100% sell tax

    @pytest.mark.asyncio
    async def test_clean_token(self) -> None:
        """GoPlus reports clean token."""
        client = GoPlusClient(max_rps=100.0)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "code": 1,
            "result": {
                "CleanMint": {
                    "is_honeypot": "0",
                    "is_open_source": "1",
                    "is_mintable": "0",
                    "buy_tax": "0",
                    "sell_tax": "0",
                    "holder_count": "500",
                    "lp_holder_count": "15",
                }
            },
        }
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=mock_resp)

        report = await client.get_token_security("CleanMint")

        assert report is not None
        assert report.is_honeypot is False
        assert report.sell_tax == 0.0

    @pytest.mark.asyncio
    async def test_empty_result(self) -> None:
        """GoPlus returns no data for unknown token."""
        client = GoPlusClient(max_rps=100.0)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 1, "result": {}}
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=mock_resp)

        report = await client.get_token_security("UnknownMint")
        assert report is None

    @pytest.mark.asyncio
    async def test_rate_limit_retry(self) -> None:
        """Client retries on 429."""
        import httpx

        client = GoPlusClient(max_rps=100.0)
        rate_resp = MagicMock()
        rate_resp.status_code = 429

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {
            "code": 1,
            "result": {"Mint": {"is_honeypot": "0", "buy_tax": "0", "sell_tax": "0"}},
        }

        client._client = AsyncMock()
        client._client.get = AsyncMock(side_effect=[rate_resp, ok_resp])

        report = await client.get_token_security("Mint")
        assert report is not None

    @pytest.mark.asyncio
    async def test_timeout_handling(self) -> None:
        """Client handles timeout gracefully."""
        import httpx

        client = GoPlusClient(max_rps=100.0)
        client._client = AsyncMock()
        client._client.get = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )

        report = await client.get_token_security("Mint")
        assert report is None

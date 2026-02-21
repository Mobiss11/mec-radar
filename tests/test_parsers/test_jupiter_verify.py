"""Tests for Jupiter VERIFY status check."""

import pytest
from unittest.mock import AsyncMock, patch

from src.parsers.jupiter_verify import (
    JupiterVerifyResult,
    check_jupiter_verify,
    _parse_verify_data,
)


class TestJupiterVerifyResult:
    def test_default_neutral(self) -> None:
        result = JupiterVerifyResult()
        assert result.score_impact == 0
        assert not result.found

    def test_strict_bonus(self) -> None:
        result = JupiterVerifyResult(found=True, is_strict=True)
        assert result.score_impact == 5

    def test_community_bonus(self) -> None:
        result = JupiterVerifyResult(found=True, is_community=True)
        assert result.score_impact == 2

    def test_banned_penalty(self) -> None:
        result = JupiterVerifyResult(found=True, is_banned=True)
        assert result.score_impact == -20

    def test_banned_overrides_strict(self) -> None:
        """If both banned and strict are set, banned takes priority."""
        result = JupiterVerifyResult(found=True, is_banned=True, is_strict=True)
        assert result.score_impact == -20


class TestParseVerifyData:
    def test_parse_strict_token(self) -> None:
        data = {
            "name": "USD Coin",
            "symbol": "USDC",
            "tags": ["verified", "community"],
            "daily_volume": 1_000_000.0,
        }
        result = JupiterVerifyResult()
        parsed = _parse_verify_data(data, result)
        assert parsed.found is True
        assert parsed.is_strict is True
        assert parsed.verification_status == "strict"
        assert parsed.name == "USD Coin"
        assert parsed.daily_volume == 1_000_000.0

    def test_parse_community_token(self) -> None:
        data = {"name": "SomeToken", "symbol": "SOME", "tags": ["community"]}
        result = JupiterVerifyResult()
        parsed = _parse_verify_data(data, result)
        assert parsed.is_community is True
        assert parsed.verification_status == "community"

    def test_parse_banned_token(self) -> None:
        data = {"name": "ScamToken", "symbol": "SCAM", "tags": ["banned"]}
        result = JupiterVerifyResult()
        parsed = _parse_verify_data(data, result)
        assert parsed.is_banned is True
        assert parsed.verification_status == "banned"

    def test_parse_token2022(self) -> None:
        data = {"name": "T22Token", "symbol": "T22", "tags": ["token-2022"]}
        result = JupiterVerifyResult()
        parsed = _parse_verify_data(data, result)
        assert parsed.verification_status == "token2022"
        assert not parsed.is_strict
        assert not parsed.is_banned

    def test_parse_listed_no_tags(self) -> None:
        data = {"name": "NewToken", "symbol": "NEW", "tags": []}
        result = JupiterVerifyResult()
        parsed = _parse_verify_data(data, result)
        assert parsed.found is True
        assert parsed.verification_status == "listed"

    def test_parse_strict_tag(self) -> None:
        """The 'strict' tag alone should also trigger is_strict."""
        data = {"name": "StrictToken", "symbol": "STR", "tags": ["strict"]}
        result = JupiterVerifyResult()
        parsed = _parse_verify_data(data, result)
        assert parsed.is_strict is True


class TestCheckJupiterVerify:
    @pytest.mark.asyncio
    async def test_404_returns_default(self) -> None:
        mock_response = AsyncMock()
        mock_response.status_code = 404

        with patch("src.parsers.jupiter_verify.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_cls.return_value = mock_client

            result = await check_jupiter_verify("token123")
            assert not result.found
            assert result.score_impact == 0

    @pytest.mark.asyncio
    async def test_429_returns_default(self) -> None:
        mock_response = AsyncMock()
        mock_response.status_code = 429

        with patch("src.parsers.jupiter_verify.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_cls.return_value = mock_client

            result = await check_jupiter_verify("token123")
            assert not result.found

    @pytest.mark.asyncio
    async def test_successful_strict(self) -> None:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = lambda: {
            "name": "USDC",
            "symbol": "USDC",
            "tags": ["verified"],
            "daily_volume": 500_000.0,
        }
        mock_response.raise_for_status = lambda: None

        with patch("src.parsers.jupiter_verify.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_cls.return_value = mock_client

            result = await check_jupiter_verify("usdc_mint")
            assert result.found
            assert result.is_strict
            assert result.score_impact == 5

    @pytest.mark.asyncio
    async def test_network_error(self) -> None:
        with patch("src.parsers.jupiter_verify.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.side_effect = Exception("timeout")
            mock_cls.return_value = mock_client

            result = await check_jupiter_verify("token123")
            assert not result.found
            assert result.score_impact == 0

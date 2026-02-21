"""Tests for Solana Tracker Risk API."""

import pytest
from unittest.mock import AsyncMock, patch

from src.parsers.solana_tracker import (
    SolanaTrackerRisk,
    get_token_risk,
    _parse_risk_data,
)


class TestSolanaTrackerRisk:
    def test_no_data_zero_impact(self) -> None:
        result = SolanaTrackerRisk()
        assert result.score_impact == 0
        assert not result.is_high_risk

    def test_high_risk_score(self) -> None:
        result = SolanaTrackerRisk(risk_score=9)
        assert result.score_impact == -12
        assert result.is_high_risk

    def test_medium_risk_score(self) -> None:
        result = SolanaTrackerRisk(risk_score=7)
        assert result.score_impact == -6
        assert result.is_high_risk

    def test_low_risk_bonus(self) -> None:
        result = SolanaTrackerRisk(risk_score=1)
        assert result.score_impact == 3
        assert not result.is_high_risk

    def test_many_snipers_penalty(self) -> None:
        result = SolanaTrackerRisk(risk_score=5, sniper_count=10)
        assert result.score_impact == -5  # snipers only

    def test_high_insider_pct_penalty(self) -> None:
        result = SolanaTrackerRisk(risk_score=5, insider_pct=40.0)
        assert result.score_impact == -5  # insider only

    def test_combined_penalties(self) -> None:
        result = SolanaTrackerRisk(
            risk_score=8, sniper_count=10, insider_pct=50.0
        )
        assert result.score_impact == -22  # -12 + -5 + -5


class TestParseRiskData:
    def test_parse_nested_risk(self) -> None:
        data = {
            "risk": {"score": 8, "sniperCount": 5, "insiderPct": 25.0},
            "holders": {"count": 150, "top10Pct": 35.0},
            "isVerified": True,
        }
        result = _parse_risk_data(data)
        assert result.risk_score == 8
        assert result.sniper_count == 5
        assert result.insider_pct == 25.0
        assert result.holder_count == 150
        assert result.top10_pct == 35.0
        assert result.is_verified is True

    def test_parse_flat_risk(self) -> None:
        data = {"score": 3, "sniper_count": 2, "insider_pct": 10.0}
        result = _parse_risk_data(data)
        assert result.risk_score == 3
        assert result.sniper_count == 2
        assert result.insider_pct == 10.0

    def test_parse_missing_fields(self) -> None:
        data = {"risk": {}}
        result = _parse_risk_data(data)
        assert result.risk_score is None
        assert result.sniper_count is None
        assert result.insider_pct is None

    def test_parse_invalid_types(self) -> None:
        data = {"risk": {"score": "invalid", "sniperCount": None}}
        result = _parse_risk_data(data)
        assert result.risk_score is None
        assert result.sniper_count is None


class TestGetTokenRisk:
    @pytest.mark.asyncio
    async def test_404_returns_none(self) -> None:
        mock_response = AsyncMock()
        mock_response.status_code = 404
        mock_response.raise_for_status = lambda: None

        with patch("src.parsers.solana_tracker.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = await get_token_risk("token123")
            assert result is None

    @pytest.mark.asyncio
    async def test_successful_fetch(self) -> None:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = lambda: {
            "risk": {"score": 6, "sniperCount": 3},
            "holders": {"count": 200},
        }
        mock_response.raise_for_status = lambda: None

        with patch("src.parsers.solana_tracker.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = await get_token_risk("token123")
            assert result is not None
            assert result.risk_score == 6
            assert result.sniper_count == 3

    @pytest.mark.asyncio
    async def test_network_error(self) -> None:
        with patch("src.parsers.solana_tracker.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.side_effect = Exception("timeout")
            mock_client_cls.return_value = mock_client

            result = await get_token_risk("token123")
            assert result is None

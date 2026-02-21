"""Tests for RugCheck Insider Networks API."""

import pytest
from unittest.mock import AsyncMock, patch

import httpx

from src.parsers.rugcheck_insiders import (
    InsiderNetworkResult,
    InsiderNode,
    InsiderEdge,
    get_insider_network,
    _parse_insider_graph,
)


class TestInsiderNetworkResult:
    def test_no_insiders_zero_impact(self) -> None:
        result = InsiderNetworkResult()
        assert result.score_impact == 0
        assert not result.is_high_risk

    def test_high_insider_pct(self) -> None:
        result = InsiderNetworkResult(insider_pct=55.0, insider_count=5, total_nodes=9)
        assert result.score_impact == -15
        assert result.is_high_risk

    def test_medium_insider_pct(self) -> None:
        result = InsiderNetworkResult(insider_pct=35.0)
        assert result.score_impact == -10
        assert result.is_high_risk

    def test_low_insider_pct(self) -> None:
        result = InsiderNetworkResult(insider_pct=20.0)
        assert result.score_impact == -5
        assert not result.is_high_risk

    def test_minimal_insider_pct(self) -> None:
        result = InsiderNetworkResult(insider_pct=10.0)
        assert result.score_impact == 0


class TestParseInsiderGraph:
    def test_parse_empty_graph(self) -> None:
        data = {"nodes": [], "edges": []}
        result = _parse_insider_graph(data)
        assert result.total_nodes == 0
        assert result.insider_count == 0
        assert result.insider_pct == 0.0

    def test_parse_with_insiders(self) -> None:
        data = {
            "nodes": [
                {"id": "wallet1", "isInsider": True, "balancePct": 15.0},
                {"id": "wallet2", "isInsider": True, "balancePct": 10.0},
                {"id": "wallet3", "isInsider": False, "balancePct": 5.0},
                {"id": "wallet4", "isInsider": False, "balancePct": 3.0},
            ],
            "edges": [
                {"source": "wallet1", "target": "wallet2", "type": "funded_by"},
            ],
        }
        result = _parse_insider_graph(data)
        assert result.total_nodes == 4
        assert result.insider_count == 2
        assert result.insider_pct == 50.0
        assert len(result.edges) == 1
        assert result.edges[0].relationship == "funded_by"

    def test_parse_alternative_field_names(self) -> None:
        """Handle alternative field names from API."""
        data = {
            "nodes": [
                {"address": "w1", "insider": True, "percentage": 20.0, "type": "holder"},
            ],
            "links": [
                {"from": "w1", "to": "w2", "relationship": "same_fee_payer"},
            ],
        }
        result = _parse_insider_graph(data)
        assert result.insider_count == 1
        assert result.insiders[0].address == "w1"
        assert result.insiders[0].label == "holder"
        assert len(result.edges) == 1
        assert result.edges[0].source == "w1"


class TestGetInsiderNetwork:
    @pytest.mark.asyncio
    async def test_404_returns_none(self) -> None:
        mock_response = AsyncMock()
        mock_response.status_code = 404
        mock_response.raise_for_status = lambda: None

        with patch("src.parsers.rugcheck_insiders.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = await get_insider_network("token123")
            assert result is None

    @pytest.mark.asyncio
    async def test_successful_fetch(self) -> None:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = lambda: {
            "nodes": [
                {"id": "w1", "isInsider": True, "balancePct": 25.0},
                {"id": "w2", "isInsider": False, "balancePct": 5.0},
            ],
            "edges": [],
        }
        mock_response.raise_for_status = lambda: None

        with patch("src.parsers.rugcheck_insiders.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = await get_insider_network("token123")
            assert result is not None
            assert result.insider_count == 1
            assert result.insider_pct == 50.0

"""Tests for Bubblemaps Data API client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.parsers.bubblemaps.client import BubblemapsClient, _parse_report
from src.parsers.bubblemaps.models import BubblemapsReport


SAMPLE_RESPONSE = {
    "metadata": {"dt_update": "2026-02-19", "ts_update": 1000000},
    "nodes": {
        "top_holders": [
            {
                "address": "Holder1",
                "address_details": {
                    "is_contract": False,
                    "is_cex": False,
                    "is_dex": False,
                    "label": "",
                },
                "holder_data": {"share": 0.15, "amount": 1500000, "rank": 1},
                "is_shown_on_map": True,
            },
            {
                "address": "Holder2",
                "address_details": {
                    "is_contract": True,
                    "is_cex": False,
                    "is_dex": True,
                    "label": "Raydium",
                },
                "holder_data": {"share": 0.10, "amount": 1000000, "rank": 2},
                "is_shown_on_map": True,
            },
        ],
    },
    "relationships": [
        {
            "from_address": "Holder1",
            "to_address": "Holder3",
            "data": {"total_value": 50000, "total_transfers": 5},
        },
    ],
    "decentralization_score": 0.65,
    "clusters": [
        {
            "share": 0.45,
            "amount": 4500000,
            "holder_count": 3,
            "holders": ["Holder1", "Holder3", "Holder5"],
        },
        {
            "share": 0.15,
            "amount": 1500000,
            "holder_count": 2,
            "holders": ["Holder2", "Holder4"],
        },
    ],
}


class TestBubblemapsParsing:
    def test_parse_full_response(self) -> None:
        """Parse complete Bubblemaps response."""
        report = _parse_report(SAMPLE_RESPONSE)

        assert report.decentralization_score == 0.65
        assert len(report.clusters) == 2
        assert report.largest_cluster_share == 0.45
        assert len(report.top_holders) == 2
        assert report.top_holders[0].address == "Holder1"
        assert report.top_holders[0].share == 0.15
        assert report.top_holders[1].is_dex is True
        assert len(report.relationships) == 1
        assert report.relationships[0].total_value == 50000

    def test_high_concentration_risk(self) -> None:
        """Low decentralization + large cluster = high risk."""
        data = {
            "decentralization_score": 0.2,
            "clusters": [
                {"share": 0.6, "amount": 6000000, "holder_count": 2, "holders": ["A", "B"]},
            ],
            "relationships": [
                {"from_address": "A", "to_address": "B", "data": {"total_value": 15000, "total_transfers": 3}},
                {"from_address": "B", "to_address": "C", "data": {"total_value": 20000, "total_transfers": 2}},
                {"from_address": "C", "to_address": "A", "data": {"total_value": 12000, "total_transfers": 1}},
            ],
            "nodes": {"top_holders": []},
        }
        report = _parse_report(data)

        assert report.decentralization_score == 0.2
        assert report.largest_cluster_share == 0.6
        assert report.risk_boost >= 35  # 15 (low decentr) + 20 (large cluster)

    def test_decentralized_token(self) -> None:
        """Well-decentralized token = low risk."""
        data = {
            "decentralization_score": 0.85,
            "clusters": [
                {"share": 0.1, "amount": 1000, "holder_count": 2, "holders": ["A", "B"]},
            ],
            "relationships": [],
            "nodes": {"top_holders": []},
        }
        report = _parse_report(data)

        assert report.risk_boost == 0


class TestBubblemapsClient:
    @pytest.mark.asyncio
    async def test_successful_fetch(self) -> None:
        """Mock successful API call."""
        client = BubblemapsClient(api_key="test_key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_RESPONSE

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_map_data("TestMint123")

        assert result is not None
        assert result.decentralization_score == 0.65
        assert len(result.clusters) == 2
        await client.close()

    @pytest.mark.asyncio
    async def test_token_not_found(self) -> None:
        """404 response for unknown token."""
        client = BubblemapsClient(api_key="test_key")

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_map_data("UnknownMint")

        assert result is None
        await client.close()

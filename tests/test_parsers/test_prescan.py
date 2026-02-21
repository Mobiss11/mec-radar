"""Tests for PRE_SCAN stage — instant reject of obvious scams."""

from unittest.mock import AsyncMock, patch

import pytest

from src.parsers.enrichment_types import EnrichmentPriority, EnrichmentStage, EnrichmentTask
from src.parsers.jupiter.models import SellSimResult
from src.parsers.mint_parser import MintInfo


# Import the function under test
from src.parsers.worker import _run_prescan


def _make_task(address: str = "TokenMint123456") -> EnrichmentTask:
    return EnrichmentTask(
        priority=EnrichmentPriority.NORMAL,
        scheduled_at=100.0,
        address=address,
        stage=EnrichmentStage.PRE_SCAN,
        discovery_time=95.0,
    )


class TestRunPrescan:
    """Tests for _run_prescan function."""

    @pytest.mark.asyncio
    async def test_reject_mint_and_freeze_authority(self) -> None:
        """Both mint + freeze authority active → hard reject."""
        mint_info = MintInfo(
            supply=1_000_000,
            decimals=6,
            mint_authority="SomeAuthority",
            freeze_authority="SomeFreezer",
        )

        with patch("src.parsers.worker.parse_mint_account", new_callable=AsyncMock) as mock_parse:
            mock_parse.return_value = mint_info
            result = await _run_prescan(
                _make_task(), rpc_url="https://rpc.test", jupiter=None,
            )

        assert result is None  # Rejected

    @pytest.mark.asyncio
    async def test_reject_permanent_delegate(self) -> None:
        """Token2022 with permanentDelegate → hard reject."""
        mint_info = MintInfo(
            supply=1_000_000,
            decimals=6,
            mint_authority="Auth",
            is_token2022=True,
            dangerous_extensions=["PERMANENT_DELEGATE"],
        )

        with patch("src.parsers.worker.parse_mint_account", new_callable=AsyncMock) as mock_parse:
            mock_parse.return_value = mint_info
            result = await _run_prescan(
                _make_task(), rpc_url="https://rpc.test", jupiter=None,
            )

        assert result is None  # Rejected

    @pytest.mark.asyncio
    async def test_reject_non_transferable(self) -> None:
        """Token2022 with nonTransferable → hard reject."""
        mint_info = MintInfo(
            supply=1_000_000,
            decimals=6,
            is_token2022=True,
            dangerous_extensions=["NON_TRANSFERABLE"],
        )

        with patch("src.parsers.worker.parse_mint_account", new_callable=AsyncMock) as mock_parse:
            mock_parse.return_value = mint_info
            result = await _run_prescan(
                _make_task(), rpc_url="https://rpc.test", jupiter=None,
            )

        assert result is None  # Rejected

    @pytest.mark.asyncio
    async def test_pass_clean_token(self) -> None:
        """Clean standard SPL token passes PRE_SCAN."""
        mint_info = MintInfo(
            supply=1_000_000,
            decimals=6,
            mint_authority=None,
            freeze_authority=None,
        )
        sell_sim = SellSimResult(sellable=True, price_impact_pct=2.0)

        mock_jupiter = AsyncMock()
        mock_jupiter.simulate_sell = AsyncMock(return_value=sell_sim)

        with patch("src.parsers.worker.parse_mint_account", new_callable=AsyncMock) as mock_parse:
            mock_parse.return_value = mint_info
            result = await _run_prescan(
                _make_task(), rpc_url="https://rpc.test", jupiter=mock_jupiter,
            )

        assert result is not None
        assert result.prescan_risk_boost == 0

    @pytest.mark.asyncio
    async def test_soft_flags_transfer_fee(self) -> None:
        """Token2022 with transferFee → passes with risk boost."""
        mint_info = MintInfo(
            supply=1_000_000,
            decimals=6,
            mint_authority=None,
            freeze_authority=None,
            is_token2022=True,
            risky_extensions=["TRANSFER_FEE_CONFIG"],
        )

        with patch("src.parsers.worker.parse_mint_account", new_callable=AsyncMock) as mock_parse:
            mock_parse.return_value = mint_info
            result = await _run_prescan(
                _make_task(), rpc_url="https://rpc.test", jupiter=None,
            )

        assert result is not None
        assert result.prescan_risk_boost >= 10

    @pytest.mark.asyncio
    async def test_unsellable_with_mint_authority_rejected(self) -> None:
        """Jupiter says unsellable + mint authority active → reject."""
        mint_info = MintInfo(
            supply=1_000_000,
            decimals=6,
            mint_authority="ActiveAuth",
            freeze_authority=None,
        )
        sell_sim = SellSimResult(sellable=False, error="No route found")

        mock_jupiter = AsyncMock()
        mock_jupiter.simulate_sell = AsyncMock(return_value=sell_sim)

        with patch("src.parsers.worker.parse_mint_account", new_callable=AsyncMock) as mock_parse:
            mock_parse.return_value = mint_info
            result = await _run_prescan(
                _make_task(), rpc_url="https://rpc.test", jupiter=mock_jupiter,
            )

        assert result is None  # Rejected

    @pytest.mark.asyncio
    async def test_api_error_does_not_reject(self) -> None:
        """Jupiter API unavailable (401) should NOT cause false rejection."""
        mint_info = MintInfo(
            supply=1_000_000,
            decimals=6,
            mint_authority="ActiveAuth",
            freeze_authority=None,
        )
        # API error — NOT a token problem
        sell_sim = SellSimResult(sellable=False, error="HTTP 401", api_error=True)

        mock_jupiter = AsyncMock()
        mock_jupiter.simulate_sell = AsyncMock(return_value=sell_sim)

        with patch("src.parsers.worker.parse_mint_account", new_callable=AsyncMock) as mock_parse:
            mock_parse.return_value = mint_info
            result = await _run_prescan(
                _make_task(), rpc_url="https://rpc.test", jupiter=mock_jupiter,
            )

        assert result is not None  # Should NOT be rejected — API was just down

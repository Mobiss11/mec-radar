"""Tests for convergence analysis — token destination consolidation detection."""

from unittest.mock import AsyncMock

import pytest

from src.parsers.convergence_analyzer import (
    ConvergenceResult,
    analyze_convergence,
)
from src.parsers.helius.models import (
    HeliusSignature,
    HeliusTokenTransfer,
    HeliusTransaction,
)


TOKEN = "TokenMint123456"
CREATOR = "Creator111"
CONSOLIDATION_WALLET = "ConsolidationWallet"


def _make_sig(sig: str, slot: int = 100) -> HeliusSignature:
    return HeliusSignature(signature=sig, slot=slot, timestamp=1000000)


def _make_token_transfer_tx(
    from_addr: str, to_addr: str, mint: str = TOKEN
) -> HeliusTransaction:
    return HeliusTransaction(
        signature=f"tx_{from_addr[:6]}",
        type="TRANSFER",
        fee=5000,
        fee_payer=from_addr,
        timestamp=1000000,
        token_transfers=[
            HeliusTokenTransfer(
                from_user_account=from_addr,
                to_user_account=to_addr,
                token_amount=1000,
                mint=mint,
            )
        ],
    )


class TestConvergenceResult:
    def test_risk_boost_converging(self) -> None:
        result = ConvergenceResult(converging=True, convergence_pct=0.8)
        assert result.risk_boost == 35

    def test_risk_boost_partial(self) -> None:
        result = ConvergenceResult(converging=False, convergence_pct=0.4)
        assert result.risk_boost == 15

    def test_risk_boost_none(self) -> None:
        result = ConvergenceResult(converging=False, convergence_pct=0.1)
        assert result.risk_boost == 0


class TestAnalyzeConvergence:
    @pytest.mark.asyncio
    async def test_convergence_detected(self) -> None:
        """All buyers send tokens to one wallet — convergence detected."""
        helius = AsyncMock()
        buyers = ["Buyer1", "Buyer2", "Buyer3", "Buyer4"]

        helius.get_signatures_for_address = AsyncMock(
            return_value=[_make_sig("sig1")]
        )

        # All 4 buyers send to CONSOLIDATION_WALLET
        async def mock_get_txs(signatures: list) -> list:
            # Infer buyer from the signature context
            return [_make_token_transfer_tx(buyers[0], CONSOLIDATION_WALLET)]

        # More precise mock: different buyer per call
        call_idx = 0

        async def mock_get_txs_per_buyer(signatures: list) -> list:
            nonlocal call_idx
            buyer = buyers[min(call_idx, len(buyers) - 1)]
            call_idx += 1
            return [_make_token_transfer_tx(buyer, CONSOLIDATION_WALLET)]

        helius.get_parsed_transactions = AsyncMock(side_effect=mock_get_txs_per_buyer)

        result = await analyze_convergence(helius, TOKEN, buyers, CREATOR)

        assert result.converging is True
        assert result.convergence_pct > 0.5
        assert result.main_destination == CONSOLIDATION_WALLET
        assert result.risk_boost == 35

    @pytest.mark.asyncio
    async def test_no_convergence(self) -> None:
        """Buyers send to different wallets — no convergence."""
        helius = AsyncMock()
        buyers = ["Buyer1", "Buyer2", "Buyer3"]

        helius.get_signatures_for_address = AsyncMock(
            return_value=[_make_sig("sig1")]
        )

        call_idx = 0
        destinations = ["Dest1", "Dest2", "Dest3"]

        async def mock_get_txs(signatures: list) -> list:
            nonlocal call_idx
            buyer = buyers[min(call_idx, len(buyers) - 1)]
            dest = destinations[min(call_idx, len(destinations) - 1)]
            call_idx += 1
            return [_make_token_transfer_tx(buyer, dest)]

        helius.get_parsed_transactions = AsyncMock(side_effect=mock_get_txs)

        result = await analyze_convergence(helius, TOKEN, buyers, CREATOR)

        assert result.converging is False
        assert result.convergence_pct <= 0.5

    @pytest.mark.asyncio
    async def test_too_few_buyers(self) -> None:
        """Less than 2 buyers — skip analysis."""
        helius = AsyncMock()

        result = await analyze_convergence(helius, TOKEN, ["OnlyBuyer"], CREATOR)

        assert result.converging is False
        assert result.total_tracked == 1

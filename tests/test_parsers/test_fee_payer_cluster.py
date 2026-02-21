"""Tests for fee payer clustering — sybil attack detection via shared fee payers."""

from unittest.mock import AsyncMock

import pytest

from src.parsers.fee_payer_cluster import (
    FeePayerClusterResult,
    cluster_by_fee_payer,
)
from src.parsers.helius.models import (
    HeliusSignature,
    HeliusTokenTransfer,
    HeliusTransaction,
)


CREATOR = "CreatorWallet111"
TOKEN = "TokenMint123456"


def _make_sig(sig: str, slot: int, err: dict | None = None) -> HeliusSignature:
    return HeliusSignature(signature=sig, slot=slot, timestamp=1000000, err=err)


def _make_tx(
    sig: str,
    fee_payer: str,
    token_receivers: list[str] | None = None,
) -> HeliusTransaction:
    token_transfers = []
    if token_receivers:
        for receiver in token_receivers:
            token_transfers.append(
                HeliusTokenTransfer(
                    from_user_account=CREATOR,
                    to_user_account=receiver,
                    token_amount=1000,
                    mint=TOKEN,
                )
            )
    return HeliusTransaction(
        signature=sig,
        type="SWAP",
        source="RAYDIUM",
        fee=5000,
        fee_payer=fee_payer,
        timestamp=1000000,
        description="",
        token_transfers=token_transfers,
        native_transfers=[],
        transaction_error=None,
    )


class TestFeePayerClusterResult:
    def test_risk_boost_high_sybil(self) -> None:
        result = FeePayerClusterResult(sybil_score=0.6)
        assert result.risk_boost == 25

    def test_risk_boost_medium_sybil(self) -> None:
        result = FeePayerClusterResult(sybil_score=0.35)
        assert result.risk_boost == 15

    def test_risk_boost_low_sybil(self) -> None:
        result = FeePayerClusterResult(sybil_score=0.1)
        assert result.risk_boost == 0


class TestClusterByFeePayer:
    @pytest.mark.asyncio
    async def test_single_payer_sybil(self) -> None:
        """All buyers share the same fee payer — max sybil."""
        helius = AsyncMock()

        # 5 txs in same slot
        sigs = [_make_sig(f"sig{i}", slot=100) for i in range(5)]
        helius.get_signatures_for_address = AsyncMock(return_value=sigs)

        # All 4 buyer txs share the same fee payer "SybilMaster"
        txs = [
            _make_tx("sig0", CREATOR),
            _make_tx("sig1", "SybilMaster", ["Buyer1"]),
            _make_tx("sig2", "SybilMaster", ["Buyer2"]),
            _make_tx("sig3", "SybilMaster", ["Buyer3"]),
            _make_tx("sig4", "SybilMaster", ["Buyer4"]),
        ]
        helius.get_parsed_transactions = AsyncMock(return_value=txs)

        result = await cluster_by_fee_payer(helius, TOKEN, CREATOR)

        assert result.sybil_score > 0.5
        assert result.risk_boost == 25
        assert result.largest_cluster_size >= 4

    @pytest.mark.asyncio
    async def test_multiple_payers_clean(self) -> None:
        """Each buyer has their own fee payer — no sybil."""
        helius = AsyncMock()

        sigs = [_make_sig(f"sig{i}", slot=100) for i in range(5)]
        helius.get_signatures_for_address = AsyncMock(return_value=sigs)

        txs = [
            _make_tx("sig0", CREATOR),
            _make_tx("sig1", "Buyer1"),
            _make_tx("sig2", "Buyer2"),
            _make_tx("sig3", "Buyer3"),
            _make_tx("sig4", "Buyer4"),
        ]
        helius.get_parsed_transactions = AsyncMock(return_value=txs)

        result = await cluster_by_fee_payer(helius, TOKEN, CREATOR)

        assert result.sybil_score == 0.0
        assert result.risk_boost == 0

    @pytest.mark.asyncio
    async def test_partial_overlap(self) -> None:
        """Some buyers share fee payer, some don't."""
        helius = AsyncMock()

        sigs = [_make_sig(f"sig{i}", slot=100) for i in range(6)]
        helius.get_signatures_for_address = AsyncMock(return_value=sigs)

        # SharedPayer controls Buyer1+Buyer2, rest are independent
        txs = [
            _make_tx("sig0", CREATOR),
            _make_tx("sig1", "SharedPayer", ["Buyer1"]),
            _make_tx("sig2", "SharedPayer", ["Buyer2"]),
            _make_tx("sig3", "IndependentA"),
            _make_tx("sig4", "IndependentB"),
            _make_tx("sig5", "IndependentC"),
        ]
        helius.get_parsed_transactions = AsyncMock(return_value=txs)

        result = await cluster_by_fee_payer(helius, TOKEN, CREATOR)

        # 4 unique payers (SharedPayer, IndependentA/B/C) out of 5+ buyers
        assert 0.0 < result.sybil_score < 0.5
        assert result.largest_cluster_size >= 2

    @pytest.mark.asyncio
    async def test_no_signatures(self) -> None:
        """No signatures found — error result."""
        helius = AsyncMock()
        helius.get_signatures_for_address = AsyncMock(return_value=[])

        result = await cluster_by_fee_payer(helius, TOKEN, CREATOR)

        assert result.error is not None

    @pytest.mark.asyncio
    async def test_too_few_txs(self) -> None:
        """Less than 3 txs — not enough for meaningful clustering."""
        helius = AsyncMock()

        sigs = [_make_sig("sig0", slot=100), _make_sig("sig1", slot=100)]
        helius.get_signatures_for_address = AsyncMock(return_value=sigs)

        result = await cluster_by_fee_payer(helius, TOKEN, CREATOR)

        assert result.total_buyers == 2
        assert result.sybil_score == 0.0

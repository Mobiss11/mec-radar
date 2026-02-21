"""Tests for bundled buy detection — coordinated first-block sybil attacks."""

from unittest.mock import AsyncMock

import pytest

from src.parsers.bundled_buy_detector import BundledBuyResult, detect_bundled_buys
from src.parsers.helius.models import (
    HeliusNativeTransfer,
    HeliusSignature,
    HeliusTransaction,
)


CREATOR = "CreatorWallet111"
TOKEN = "TokenMint123456"


def _make_sig(sig: str, slot: int, err: dict | None = None) -> HeliusSignature:
    return HeliusSignature(signature=sig, slot=slot, timestamp=1000000, err=err)


def _make_tx(
    sig: str, fee_payer: str, native_transfers: list[HeliusNativeTransfer] | None = None
) -> HeliusTransaction:
    return HeliusTransaction(
        signature=sig,
        type="SWAP",
        source="RAYDIUM",
        fee=5000,
        fee_payer=fee_payer,
        timestamp=1000000,
        description="",
        token_transfers=[],
        native_transfers=native_transfers or [],
        transaction_error=None,
    )


class TestBundledBuyResult:
    def test_risk_boost_high(self) -> None:
        result = BundledBuyResult(bundled_pct=60)
        assert result.risk_boost == 30

    def test_risk_boost_medium(self) -> None:
        result = BundledBuyResult(bundled_pct=30)
        assert result.risk_boost == 15

    def test_risk_boost_none(self) -> None:
        result = BundledBuyResult(bundled_pct=10)
        assert result.risk_boost == 0


class TestDetectBundledBuys:
    @pytest.mark.asyncio
    async def test_bundled_detected(self) -> None:
        """Creator funded multiple buyers in first block."""
        helius = AsyncMock()

        # 5 signatures in same slot
        sigs = [_make_sig(f"sig{i}", slot=100) for i in range(5)]
        helius.get_signatures_for_address = AsyncMock(return_value=sigs)

        # 4 buyers, 3 funded by creator
        txs = [
            _make_tx("sig0", CREATOR),
            _make_tx("sig1", "Buyer1", [
                HeliusNativeTransfer(from_user_account=CREATOR, to_user_account="Buyer1", amount=100000000),
            ]),
            _make_tx("sig2", "Buyer2", [
                HeliusNativeTransfer(from_user_account=CREATOR, to_user_account="Buyer2", amount=100000000),
            ]),
            _make_tx("sig3", "Buyer3", [
                HeliusNativeTransfer(from_user_account=CREATOR, to_user_account="Buyer3", amount=100000000),
            ]),
            _make_tx("sig4", "Buyer4"),
        ]
        helius.get_parsed_transactions = AsyncMock(return_value=txs)

        result = await detect_bundled_buys(helius, TOKEN, CREATOR)

        assert result.is_bundled is True
        assert result.funded_by_creator == 3
        assert result.bundled_pct > 50

    @pytest.mark.asyncio
    async def test_no_bundle(self) -> None:
        """Normal organic buying — no bundling."""
        helius = AsyncMock()

        sigs = [_make_sig(f"sig{i}", slot=100 + i) for i in range(5)]
        helius.get_signatures_for_address = AsyncMock(return_value=sigs)

        # Only first sig is in creation slot, rest are later
        txs = [_make_tx("sig0", CREATOR)]
        helius.get_parsed_transactions = AsyncMock(return_value=txs)

        result = await detect_bundled_buys(helius, TOKEN, CREATOR)

        assert result.is_bundled is False

    @pytest.mark.asyncio
    async def test_no_signatures(self) -> None:
        """No signatures found — error result."""
        helius = AsyncMock()
        helius.get_signatures_for_address = AsyncMock(return_value=[])

        result = await detect_bundled_buys(helius, TOKEN, CREATOR)

        assert result.error is not None

    @pytest.mark.asyncio
    async def test_creator_only_buyer(self) -> None:
        """Only creator bought — not enough txs to detect bundling."""
        helius = AsyncMock()

        sigs = [_make_sig("sig0", slot=100)]
        helius.get_signatures_for_address = AsyncMock(return_value=sigs)

        result = await detect_bundled_buys(helius, TOKEN, CREATOR)

        assert result.is_bundled is False
        assert result.first_block_buyers == 1  # Only 1 tx, early return

    @pytest.mark.asyncio
    async def test_failed_txs_excluded(self) -> None:
        """Failed transactions are excluded from analysis."""
        helius = AsyncMock()

        sigs = [
            _make_sig("sig0", slot=100),
            _make_sig("sig1", slot=100, err={"InstructionError": [0, "Custom"]}),
            _make_sig("sig2", slot=100),
        ]
        helius.get_signatures_for_address = AsyncMock(return_value=sigs)

        txs = [
            _make_tx("sig0", CREATOR),
            _make_tx("sig2", "Buyer1"),
        ]
        helius.get_parsed_transactions = AsyncMock(return_value=txs)

        result = await detect_bundled_buys(helius, TOKEN, CREATOR)

        # Only sig0 and sig2 are valid (sig1 failed)
        assert result.first_block_buyers == 1  # Buyer1
        assert result.is_bundled is False

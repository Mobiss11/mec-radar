"""Tests for on-chain honeypot detection via Helius."""

from decimal import Decimal

import pytest

from src.parsers.honeypot_detector import detect_honeypot_onchain
from src.parsers.helius.models import HeliusSignature, HeliusTokenTransfer, HeliusTransaction

MINT = "testmint123456789012345678901234567890123"


class FakeHelius:
    """Minimal Helius client stub for testing."""

    def __init__(
        self,
        signatures: list[HeliusSignature],
        transactions: list[HeliusTransaction],
    ):
        self._sigs = signatures
        self._txs = transactions

    async def get_signatures_for_address(self, address: str, *, limit: int = 50):
        return self._sigs

    async def get_parsed_transactions(self, signatures: list[str]):
        return self._txs


def _sell_tx(sig: str) -> HeliusTransaction:
    """Create a SWAP tx with a token transfer matching the mint."""
    return HeliusTransaction(
        signature=sig,
        type="SWAP",
        source="RAYDIUM",
        token_transfers=[
            HeliusTokenTransfer(
                from_user_account="user1",
                to_user_account="pool1",
                mint=MINT,
                token_amount=Decimal("1000"),
            )
        ],
    )


@pytest.mark.asyncio
async def test_detect_honeypot_confirmed():
    """Many failed sigs + few successful sells → confirmed honeypot."""
    # 8 failed, 2 successful → total = sells(2) + failed(8) = 10, ratio = 80%
    sigs = []
    for i in range(8):
        sigs.append(HeliusSignature(
            signature=f"fail{i}", slot=100 + i, timestamp=1000 + i,
            err={"InstructionError": [0, {"Custom": 6}]},
        ))
    for i in range(2):
        sigs.append(HeliusSignature(
            signature=f"ok{i}", slot=200 + i, timestamp=2000 + i,
        ))

    txs = [_sell_tx(f"ok{i}") for i in range(2)]
    helius = FakeHelius(sigs, txs)
    result = await detect_honeypot_onchain(helius, MINT)
    assert result is not None
    assert result.is_honeypot
    assert result.failed_ratio > 0.3
    assert result.score_impact == -25


@pytest.mark.asyncio
async def test_detect_honeypot_not_suspected():
    """Most txs succeed → no honeypot flag."""
    sigs = []
    # 1 failed, 10 successful
    sigs.append(HeliusSignature(
        signature="fail0", slot=100, timestamp=1000,
        err={"InstructionError": [0, {"Custom": 6}]},
    ))
    for i in range(10):
        sigs.append(HeliusSignature(
            signature=f"ok{i}", slot=200 + i, timestamp=2000 + i,
        ))

    txs = [_sell_tx(f"ok{i}") for i in range(10)]
    helius = FakeHelius(sigs, txs)
    result = await detect_honeypot_onchain(helius, MINT)
    assert result is not None
    assert not result.is_honeypot
    assert not result.is_suspected
    assert result.score_impact == 0


@pytest.mark.asyncio
async def test_detect_honeypot_insufficient_data():
    """Fewer than 5 sigs → returns None."""
    sigs = [
        HeliusSignature(signature=f"sig{i}", slot=100 + i, timestamp=1000 + i)
        for i in range(3)
    ]
    helius = FakeHelius(sigs, [])
    result = await detect_honeypot_onchain(helius, MINT)
    assert result is None


@pytest.mark.asyncio
async def test_detect_honeypot_empty():
    """No signatures → returns None."""
    helius = FakeHelius([], [])
    result = await detect_honeypot_onchain(helius, MINT)
    assert result is None

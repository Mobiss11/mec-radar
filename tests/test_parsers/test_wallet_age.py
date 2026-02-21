"""Tests for wallet age check (sybil detection)."""

import time

import pytest

from src.parsers.helius.models import HeliusSignature
from src.parsers.wallet_age import check_wallet_ages


class FakeHelius:
    """Helius client stub for wallet age testing."""

    def __init__(self, sig_map: dict[str, list[HeliusSignature]]):
        self._sig_map = sig_map

    async def get_signatures_for_address(self, address: str, *, limit: int = 50):
        return self._sig_map.get(address, [])


@pytest.mark.asyncio
async def test_fresh_wallets_sybil():
    """50%+ wallets under 1h → sybil suspected."""
    now_ts = int(time.time())
    # All wallets created very recently
    sig_map = {
        f"wallet_{i}aaaaaaaaaaaaaaaaaaaaaaaa": [
            HeliusSignature(
                signature=f"sig_{i}",
                slot=100,
                timestamp=now_ts - 60 * 10,  # 10 minutes ago
            )
        ]
        for i in range(6)
    }
    helius = FakeHelius(sig_map)
    addresses = list(sig_map.keys())

    result = await check_wallet_ages(helius, addresses)
    assert result is not None
    assert result.pct_under_1h > 50
    assert result.is_sybil_suspected
    assert result.score_impact <= -8


@pytest.mark.asyncio
async def test_established_wallets_no_flag():
    """Old wallets → no sybil flag."""
    now_ts = int(time.time())
    sig_map = {
        f"old_wallet_{i}aaaaaaaaaaaaaaaaaaaaaa": [
            HeliusSignature(
                signature=f"old_sig_{i}",
                slot=100,
                timestamp=now_ts - 86400 * 30,  # 30 days ago
            )
        ]
        for i in range(5)
    }
    helius = FakeHelius(sig_map)
    addresses = list(sig_map.keys())

    result = await check_wallet_ages(helius, addresses)
    assert result is not None
    assert result.pct_under_1h == 0
    assert not result.is_sybil_suspected
    assert result.score_impact == 0


@pytest.mark.asyncio
async def test_mixed_wallets():
    """Mix of old and new wallets → moderate result."""
    now_ts = int(time.time())
    sig_map = {}
    for i in range(4):
        # 2 fresh, 2 old
        ts = now_ts - 60 * 5 if i < 2 else now_ts - 86400 * 10
        sig_map[f"mix_wallet_{i}aaaaaaaaaaaaaaaaaaaaa"] = [
            HeliusSignature(signature=f"mix_sig_{i}", slot=100, timestamp=ts)
        ]
    helius = FakeHelius(sig_map)
    addresses = list(sig_map.keys())

    result = await check_wallet_ages(helius, addresses)
    assert result is not None
    assert result.pct_under_1h == 50.0
    # 50% is borderline — exactly at threshold


@pytest.mark.asyncio
async def test_empty_addresses():
    """Empty address list returns None."""
    helius = FakeHelius({})
    result = await check_wallet_ages(helius, [])
    assert result is None

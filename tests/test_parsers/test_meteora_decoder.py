"""Test Meteora DBC VirtualPool decoder."""

import base64
import struct

from src.parsers.meteora.constants import VIRTUAL_POOL_DISCRIMINATOR
from src.parsers.meteora.decoder import VIRTUAL_POOL_SIZE, decode_virtual_pool


def _build_valid_pool_data() -> bytes:
    """Build minimal valid VirtualPool account data (424 bytes)."""
    buf = bytearray(VIRTUAL_POOL_SIZE)

    # Discriminator (8 bytes)
    buf[0:8] = VIRTUAL_POOL_DISCRIMINATOR

    # creator at 104:136 — 32 bytes of 0x01
    buf[104:136] = bytes([1] * 32)

    # base_mint at 136:168 — 32 bytes of 0x02
    buf[136:168] = bytes([2] * 32)

    # quote_vault at 200:232 — 32 bytes of 0x03
    buf[200:232] = bytes([3] * 32)

    # base_reserve at 232 (u64 LE)
    struct.pack_into("<Q", buf, 232, 1_000_000_000)

    # quote_reserve at 240 (u64 LE)
    struct.pack_into("<Q", buf, 240, 500_000_000)

    # is_migrated at 305 (u8)
    buf[305] = 0

    return bytes(buf)


def test_decode_returns_none_on_short_data():
    short_data = base64.b64encode(b"tooshort").decode()
    result = decode_virtual_pool("test_pool", short_data)
    assert result is None


def test_decode_returns_none_on_wrong_discriminator():
    buf = bytearray(VIRTUAL_POOL_SIZE)
    buf[0:8] = b"\x00\x00\x00\x00\x00\x00\x00\x00"
    data_b64 = base64.b64encode(buf).decode()
    result = decode_virtual_pool("test_pool", data_b64)
    assert result is None


def test_decode_returns_none_on_invalid_base64():
    result = decode_virtual_pool("test_pool", "not-valid-base64!!!")
    assert result is None


def test_decode_valid_pool():
    pool_data = _build_valid_pool_data()
    data_b64 = base64.b64encode(pool_data).decode()
    result = decode_virtual_pool("pool_addr_abc", data_b64)
    assert result is not None
    assert result.pool_address == "pool_addr_abc"
    assert result.base_reserve == 1_000_000_000
    assert result.quote_reserve == 500_000_000
    assert result.is_migrated is False
    # creator, base_mint, quote_mint should be valid base58 strings
    assert len(result.creator) > 0
    assert len(result.base_mint) > 0
    assert len(result.quote_mint) > 0
    # Bonding curve progress: 500M / 85B * 100 ≈ 0.59%
    assert result.bonding_curve_progress_pct is not None
    assert float(result.bonding_curve_progress_pct) < 1.0


def test_decode_migrated_pool():
    pool_data = bytearray(_build_valid_pool_data())
    pool_data[305] = 1  # is_migrated = true
    data_b64 = base64.b64encode(pool_data).decode()
    result = decode_virtual_pool("migrated_pool", data_b64)
    assert result is not None
    assert result.is_migrated is True


def test_decode_near_graduation_pool():
    """Pool with ~80 SOL (near graduation) should show high progress %."""
    pool_data = bytearray(_build_valid_pool_data())
    # 80 SOL = 80_000_000_000 lamports
    struct.pack_into("<Q", pool_data, 240, 80_000_000_000)
    data_b64 = base64.b64encode(pool_data).decode()
    result = decode_virtual_pool("near_grad", data_b64)
    assert result is not None
    # 80 / 85 * 100 ≈ 94.1%
    assert result.bonding_curve_progress_pct is not None
    progress = float(result.bonding_curve_progress_pct)
    assert 93 < progress < 95


def test_decode_correct_discriminator():
    """Verify our discriminator constant matches expected bytes."""
    assert VIRTUAL_POOL_DISCRIMINATOR == bytes([213, 224, 5, 209, 98, 69, 119, 92])
    assert len(VIRTUAL_POOL_DISCRIMINATOR) == 8

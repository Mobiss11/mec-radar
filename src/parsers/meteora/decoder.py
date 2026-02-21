"""Decode Meteora DBC VirtualPool on-chain account data.

Layout from https://github.com/MeteoraAg/dynamic-bonding-curve
Account size: 424 bytes (8 discriminator + 416 struct, repr(C) packed).

Key field offsets:
  72:104  config (Pubkey 32b)
  104:136 creator (Pubkey 32b)
  136:168 base_mint (Pubkey 32b)
  232:240 base_reserve (u64 LE)
  240:248 quote_reserve (u64 LE)
  305     is_migrated (u8 bool)

NOTE: quote_mint is NOT in VirtualPool — it lives in the PoolConfig account.
"""

import base64
import struct
from decimal import Decimal

from loguru import logger
from solders.pubkey import Pubkey  # type: ignore[import-untyped]

from src.parsers.meteora.constants import (
    DBC_GRADUATION_THRESHOLD_LAMPORTS,
    VIRTUAL_POOL_DISCRIMINATOR,
)
from src.parsers.meteora.models import MeteoraVirtualPool

VIRTUAL_POOL_SIZE = 424


def decode_virtual_pool(pool_address: str, data_b64: str) -> MeteoraVirtualPool | None:
    """Decode base64-encoded VirtualPool account data.

    Returns None on invalid data (short, wrong discriminator, decode error).
    """
    try:
        data = base64.b64decode(data_b64)
    except Exception:
        logger.debug(f"[MDBC] Failed to base64-decode pool data for {pool_address[:12]}")
        return None

    if len(data) < VIRTUAL_POOL_SIZE:
        logger.debug(
            f"[MDBC] Pool data too short: {len(data)} < {VIRTUAL_POOL_SIZE} "
            f"for {pool_address[:12]}"
        )
        return None

    if data[:8] != VIRTUAL_POOL_DISCRIMINATOR:
        logger.debug(f"[MDBC] Wrong discriminator for {pool_address[:12]}")
        return None

    try:
        creator = str(Pubkey.from_bytes(data[104:136]))
        base_mint = str(Pubkey.from_bytes(data[136:168]))

        # quote_mint not in VirtualPool — we'll get it from PoolConfig or skip
        # Use a placeholder; the REST client can fill it via PoolConfig
        quote_vault = str(Pubkey.from_bytes(data[200:232]))

        (base_reserve, quote_reserve) = struct.unpack_from("<2Q", data, 232)

        is_migrated = data[305] != 0

        # Bonding curve progress: quote_reserve / graduation_threshold * 100
        progress: Decimal | None = None
        if DBC_GRADUATION_THRESHOLD_LAMPORTS > 0 and quote_reserve > 0:
            progress = min(
                Decimal(quote_reserve * 100) / Decimal(DBC_GRADUATION_THRESHOLD_LAMPORTS),
                Decimal(100),
            )

        return MeteoraVirtualPool(
            pool_address=pool_address,
            creator=creator,
            base_mint=base_mint,
            quote_mint=quote_vault,  # Using quote_vault as proxy
            base_reserve=base_reserve,
            quote_reserve=quote_reserve,
            is_migrated=is_migrated,
            bonding_curve_progress_pct=progress,
        )
    except Exception as e:
        logger.debug(f"[MDBC] Error decoding pool {pool_address[:12]}: {e}")
        return None

"""Wallet age check via Helius — detect sybil attacks with fresh wallets."""

from dataclasses import dataclass
import asyncio

from loguru import logger

from src.parsers.helius.client import HeliusClient


@dataclass
class WalletAge:
    """Age and activity data for a single wallet."""

    address: str
    first_tx_timestamp: int  # unix timestamp of first tx
    age_hours: float  # hours since first tx
    tx_count: int


@dataclass
class WalletAgeResult:
    """Summary of wallet age analysis for a group."""

    wallets: list[WalletAge]
    pct_under_1h: float  # % of wallets < 1 hour old
    pct_under_24h: float  # % of wallets < 24 hours old
    is_sybil_suspected: bool
    score_impact: int


async def check_wallet_ages(
    helius: HeliusClient,
    addresses: list[str],
    *,
    max_concurrent: int = 10,
) -> WalletAgeResult | None:
    """Check wallet ages for a list of addresses.

    Returns WalletAgeResult or None on error.
    """
    if not addresses:
        return None

    try:
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _check_one(addr: str) -> WalletAge | None:
            async with semaphore:
                sigs = await helius.get_signatures_for_address(addr, limit=1)
                if not sigs:
                    return WalletAge(
                        address=addr, first_tx_timestamp=0, age_hours=0, tx_count=0
                    )
                oldest = sigs[-1]
                if oldest.timestamp <= 0:
                    return WalletAge(
                        address=addr, first_tx_timestamp=0, age_hours=0, tx_count=0
                    )

                import time
                age_sec = time.time() - oldest.timestamp
                age_hours = max(age_sec / 3600, 0)

                return WalletAge(
                    address=addr,
                    first_tx_timestamp=oldest.timestamp,
                    age_hours=round(age_hours, 1),
                    tx_count=len(sigs),
                )

        results = await asyncio.gather(
            *[_check_one(a) for a in addresses[:max_concurrent]],
            return_exceptions=True,
        )

        wallets = [r for r in results if isinstance(r, WalletAge)]
        if not wallets:
            return None

        total = len(wallets)
        under_1h = sum(1 for w in wallets if 0 < w.age_hours < 1)
        under_24h = sum(1 for w in wallets if 0 < w.age_hours < 24)

        pct_1h = under_1h / total * 100 if total > 0 else 0
        pct_24h = under_24h / total * 100 if total > 0 else 0

        is_sybil = pct_1h > 50

        if is_sybil:
            impact = -8
        elif pct_24h > 50:
            impact = -4
        else:
            impact = 0

        if is_sybil:
            logger.info(
                f"[WALLET-AGE] Sybil suspected: {under_1h}/{total} wallets < 1h old"
            )

        return WalletAgeResult(
            wallets=wallets,
            pct_under_1h=round(pct_1h, 1),
            pct_under_24h=round(pct_24h, 1),
            is_sybil_suspected=is_sybil,
            score_impact=impact,
        )

    except Exception as e:
        logger.debug(f"[WALLET-AGE] Error: {e}")
        return None

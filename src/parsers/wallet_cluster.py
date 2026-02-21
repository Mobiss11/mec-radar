"""Wallet clustering — detect coordinated traders and sybil wallets.

Detects wallets belonging to the same entity by:
1. Coordinated trades: wallets buying the same token within a 5-min window
2. Holder overlap: wallets appearing together in top holders of 3+ tokens
"""

import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import TokenTopHolder, TokenTrade
from src.models.wallet import WalletCluster


async def detect_coordinated_traders(
    session: AsyncSession,
    token_id: int,
    *,
    time_window_minutes: int = 5,
    min_group_size: int = 3,
) -> list[list[str]]:
    """Find wallets that bought the same token within a tight time window.

    Returns groups of wallet addresses that traded together.
    """
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    stmt = (
        select(TokenTrade)
        .where(
            TokenTrade.token_id == token_id,
            TokenTrade.side == "buy",
            TokenTrade.timestamp >= cutoff,
            TokenTrade.wallet_address.isnot(None),
        )
        .order_by(TokenTrade.timestamp)
    )
    result = await session.execute(stmt)
    trades = list(result.scalars().all())

    if len(trades) < min_group_size:
        return []

    # Sliding window: group trades within time_window_minutes
    groups: list[list[str]] = []
    window = timedelta(minutes=time_window_minutes)

    for i, anchor in enumerate(trades):
        group_wallets = set()
        for j in range(i, len(trades)):
            if trades[j].timestamp - anchor.timestamp > window:
                break
            if trades[j].wallet_address:
                group_wallets.add(trades[j].wallet_address)
        if len(group_wallets) >= min_group_size:
            groups.append(sorted(group_wallets))

    # Deduplicate groups (same set of wallets)
    seen: set[str] = set()
    unique_groups: list[list[str]] = []
    for g in groups:
        key = ",".join(g)
        if key not in seen:
            seen.add(key)
            unique_groups.append(g)

    return unique_groups


async def detect_holder_overlap(
    session: AsyncSession,
    wallet_addresses: list[str],
    *,
    min_overlap: int = 3,
) -> list[tuple[str, str, int]]:
    """Find pairs of wallets appearing in top holders of multiple tokens.

    Returns (wallet_a, wallet_b, overlap_count) tuples.
    """
    if len(wallet_addresses) < 2:
        return []

    stmt = (
        select(TokenTopHolder.token_id, TokenTopHolder.address)
        .where(TokenTopHolder.address.in_(wallet_addresses))
    )
    result = await session.execute(stmt)
    rows = result.all()

    # Build wallet → set of tokens mapping
    wallet_tokens: dict[str, set[int]] = defaultdict(set)
    for token_id, address in rows:
        wallet_tokens[address].add(token_id)

    # Find overlapping pairs
    addresses = list(wallet_tokens.keys())
    pairs: list[tuple[str, str, int]] = []
    for i in range(len(addresses)):
        for j in range(i + 1, len(addresses)):
            overlap = len(wallet_tokens[addresses[i]] & wallet_tokens[addresses[j]])
            if overlap >= min_overlap:
                pairs.append((addresses[i], addresses[j], overlap))

    return pairs


async def check_clustered_holders(
    session: AsyncSession,
    holder_addresses: list[str],
) -> list[str]:
    """Check if any holder addresses belong to known clusters.

    Returns list of cluster_ids found.
    """
    if not holder_addresses:
        return []

    stmt = (
        select(WalletCluster.cluster_id)
        .where(WalletCluster.wallet_address.in_(holder_addresses))
        .distinct()
    )
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


async def save_clusters(
    session: AsyncSession,
    wallets: list[str],
    method: str,
    confidence: float = 0.8,
) -> str:
    """Save a group of wallets as a cluster. Returns cluster_id."""
    cluster_id = f"cluster_{uuid.uuid4().hex[:12]}"
    for addr in wallets:
        session.add(WalletCluster(
            cluster_id=cluster_id,
            wallet_address=addr,
            confidence=Decimal(str(confidence)),
            method=method,
        ))
    logger.info(f"[CLUSTER] Saved cluster {cluster_id} ({len(wallets)} wallets, method={method})")
    return cluster_id

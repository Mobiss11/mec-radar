"""Cross-token whale correlation — detect coordinated pump operations.

If the same wallets appear as top holders in multiple recently discovered tokens,
it's likely a coordinated pump-and-dump scheme.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import Token, TokenSnapshot, TokenTopHolder


@dataclass
class CoordinatedPumpResult:
    """Result of cross-token whale analysis."""

    shared_wallets: list[str]
    token_ids: list[int]
    token_count: int
    wallet_count: int
    score_impact: int


async def detect_cross_token_coordination(
    session: AsyncSession,
    token_id: int,
    *,
    lookback_hours: int = 2,
    min_shared_wallets: int = 3,
    min_shared_tokens: int = 2,
) -> CoordinatedPumpResult | None:
    """Check if top holders of this token also appear in other recent tokens.

    Compares top holders against all tokens discovered in the last N hours.
    If >= min_shared_wallets appear in >= min_shared_tokens, flag as coordinated.
    """
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=lookback_hours)

    # Get recent token IDs (excluding the current one)
    recent_tokens_stmt = (
        select(Token.id)
        .where(
            and_(
                Token.first_seen_at >= cutoff,
                Token.id != token_id,
            )
        )
    )
    result = await session.execute(recent_tokens_stmt)
    recent_ids = [row[0] for row in result.all()]

    if not recent_ids:
        return None

    # Get top holders of current token
    cur_holders_stmt = (
        select(TokenTopHolder.address)
        .where(TokenTopHolder.token_id == token_id)
        .order_by(TokenTopHolder.rank)
        .limit(10)
    )
    result = await session.execute(cur_holders_stmt)
    current_holders = {row[0] for row in result.all()}

    if not current_holders:
        return None

    # Find which of these wallets also hold other recent tokens
    overlap_stmt = (
        select(
            TokenTopHolder.address,
            TokenTopHolder.token_id,
        )
        .where(
            and_(
                TokenTopHolder.token_id.in_(recent_ids),
                TokenTopHolder.address.in_(current_holders),
            )
        )
    )
    result = await session.execute(overlap_stmt)
    overlaps = result.all()

    if not overlaps:
        return None

    # Group: which wallets appear in how many tokens
    wallet_tokens: dict[str, set[int]] = defaultdict(set)
    for addr, tid in overlaps:
        wallet_tokens[addr].add(tid)

    # Filter wallets that appear in enough tokens
    coordinated_wallets = {
        addr: tids
        for addr, tids in wallet_tokens.items()
        if len(tids) >= min_shared_tokens
    }

    if len(coordinated_wallets) < min_shared_wallets:
        return None

    all_token_ids = set()
    for tids in coordinated_wallets.values():
        all_token_ids.update(tids)
    all_token_ids.add(token_id)

    # Score impact scales with number of shared wallets
    n_wallets = len(coordinated_wallets)
    if n_wallets >= 5:
        score_impact = -15
    elif n_wallets >= 4:
        score_impact = -12
    else:
        score_impact = -10

    logger.warning(
        f"[WHALE-CROSS] Coordinated pump detected for token_id={token_id}: "
        f"{n_wallets} shared wallets across {len(all_token_ids)} tokens"
    )

    return CoordinatedPumpResult(
        shared_wallets=list(coordinated_wallets.keys()),
        token_ids=list(all_token_ids),
        token_count=len(all_token_ids),
        wallet_count=n_wallets,
        score_impact=score_impact,
    )

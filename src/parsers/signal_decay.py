"""Signal decay — automatically downgrade stale signals by TTL.

Transition rules:
  strong_buy → buy   (after strong_buy_ttl_hours)
  buy        → watch (after buy_ttl_hours)
  watch      → expired (after watch_ttl_hours)

Uses updated_at (not created_at) so re-confirmed signals reset their TTL.

Before each downgrade, any existing signal of the *target* status for the
same token_id is expired first to avoid violating the partial unique index
``uq_signals_token_status_active(token_id, status)``.
"""

from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.signal import Signal


async def decay_stale_signals(
    session: AsyncSession,
    *,
    strong_buy_ttl_hours: int = 4,
    buy_ttl_hours: int = 6,
    watch_ttl_hours: int = 12,
) -> int:
    """Downgrade signals that have exceeded their TTL.

    Returns the total number of signals decayed.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    total = 0

    # --- strong_buy → buy ---
    cutoff_sb = now - timedelta(hours=strong_buy_ttl_hours)
    # Token IDs whose strong_buy will decay into buy
    decaying_sb_ids = (
        select(Signal.token_id)
        .where(Signal.status == "strong_buy", Signal.updated_at < cutoff_sb)
        .scalar_subquery()
    )
    # Expire existing buy signals for those tokens to avoid unique violation
    await session.execute(
        update(Signal)
        .where(Signal.token_id.in_(decaying_sb_ids), Signal.status == "buy")
        .values(status="expired", updated_at=func.now())
    )
    # Now safely downgrade
    result = await session.execute(
        update(Signal)
        .where(Signal.status == "strong_buy", Signal.updated_at < cutoff_sb)
        .values(status="buy", updated_at=func.now())
    )
    total += result.rowcount

    # --- buy → watch ---
    cutoff_buy = now - timedelta(hours=buy_ttl_hours)
    decaying_buy_ids = (
        select(Signal.token_id)
        .where(Signal.status == "buy", Signal.updated_at < cutoff_buy)
        .scalar_subquery()
    )
    await session.execute(
        update(Signal)
        .where(Signal.token_id.in_(decaying_buy_ids), Signal.status == "watch")
        .values(status="expired", updated_at=func.now())
    )
    result = await session.execute(
        update(Signal)
        .where(Signal.status == "buy", Signal.updated_at < cutoff_buy)
        .values(status="watch", updated_at=func.now())
    )
    total += result.rowcount

    # --- watch → expired ---
    cutoff_watch = now - timedelta(hours=watch_ttl_hours)
    result = await session.execute(
        update(Signal)
        .where(Signal.status == "watch", Signal.updated_at < cutoff_watch)
        .values(status="expired", updated_at=func.now())
    )
    total += result.rowcount

    if total > 0:
        logger.info(f"[DECAY] Decayed {total} stale signals")

    return total

"""Creator repeat launch detector — flag serial token launchers."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import Token


@dataclass
class CreatorActivity:
    """Recent activity of a token creator."""

    creator_address: str
    recent_launches: int  # tokens launched in time window
    is_serial_launcher: bool  # 3+ launches in window
    risk_boost: int  # additional risk score


async def check_creator_recent_launches(
    session: AsyncSession,
    creator_address: str,
    *,
    hours: int = 4,
) -> CreatorActivity | None:
    """Check how many tokens the creator launched recently.

    A creator launching 3+ tokens in 4 hours is likely a serial scammer.

    Returns None if creator_address is empty.
    """
    if not creator_address:
        return None

    try:
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours)

        stmt = (
            select(func.count())
            .select_from(Token)
            .where(
                Token.creator_address == creator_address,
                Token.first_seen_at >= cutoff,
            )
        )
        result = await session.execute(stmt)
        count = result.scalar_one()

        is_serial = count >= 3
        if count >= 5:
            risk_boost = 40
        elif count >= 3:
            risk_boost = 30
        elif count >= 2:
            risk_boost = 15
        else:
            risk_boost = 0

        if is_serial:
            logger.info(
                f"[CREATOR] Serial launcher: {creator_address} "
                f"launched {count} tokens in {hours}h"
            )

        return CreatorActivity(
            creator_address=creator_address,
            recent_launches=count,
            is_serial_launcher=is_serial,
            risk_boost=risk_boost,
        )

    except Exception as e:
        logger.debug(f"[CREATOR] Error checking {creator_address}: {e}")
        return None

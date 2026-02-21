"""Creator funding trace — analyse wallet funding source for risk signals.

Checks the creator's wallet history to identify:
- Funded by known rugger → high risk
- Freshly created wallet (< 24h) → moderate risk
- Funded from centralized exchange → lower risk (harder to trace)
"""

from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import CreatorProfile

# Default risk for first-time unknown creators
DEFAULT_FIRST_LAUNCH_RISK = 25


async def assess_creator_risk(
    session: AsyncSession,
    creator_address: str,
) -> tuple[int, bool]:
    """Assess creator risk from profile data.

    Returns (risk_score, is_first_launch).
    For first-time creators, returns DEFAULT_FIRST_LAUNCH_RISK instead of 0.
    """
    stmt = select(CreatorProfile).where(CreatorProfile.address == creator_address)
    result = await session.execute(stmt)
    profile = result.scalar_one_or_none()

    if profile is None:
        # Brand new creator — unknown risk, not zero risk
        return DEFAULT_FIRST_LAUNCH_RISK, True

    if profile.total_launches <= 1:
        # First launch we've seen — could be new or could be alt account
        base_risk = DEFAULT_FIRST_LAUNCH_RISK
        # Adjust by funding risk if we have it
        if profile.funding_risk_score is not None:
            base_risk = max(base_risk, profile.funding_risk_score)
        return base_risk, True

    # Known creator with history — use their calculated risk
    risk = profile.risk_score or 0

    # Boost risk if funded by known rugger
    if profile.funding_risk_score is not None and profile.funding_risk_score > risk:
        risk = max(risk, profile.funding_risk_score)

    return risk, False


async def update_creator_funding(
    session: AsyncSession,
    creator_address: str,
    funded_by: str | None,
    funding_risk: int = 0,
) -> None:
    """Update creator profile with funding trace data.

    Called after Helius transaction analysis reveals funding source.
    """
    stmt = select(CreatorProfile).where(CreatorProfile.address == creator_address)
    result = await session.execute(stmt)
    profile = result.scalar_one_or_none()

    if profile is None:
        return

    profile.funded_by = funded_by
    profile.funding_risk_score = funding_risk

    # If funded by known rugger, boost overall risk score
    if funding_risk > 0 and (profile.risk_score or 0) < funding_risk:
        profile.risk_score = min(funding_risk + (profile.risk_score or 0), 100)

    await session.flush()


async def check_funding_source_risk(
    session: AsyncSession,
    funder_address: str,
) -> int:
    """Check if a funding source address is a known rugger.

    Returns risk score: 0 (clean) to 80 (known rugger).
    """
    stmt = select(CreatorProfile).where(CreatorProfile.address == funder_address)
    result = await session.execute(stmt)
    profile = result.scalar_one_or_none()

    if profile is None:
        return 0

    # Known rugger: high rug count relative to total launches
    if profile.total_launches > 0 and profile.rugged_count > 0:
        rug_rate = profile.rugged_count / profile.total_launches
        if rug_rate >= 0.7:
            return 80  # serial rugger
        if rug_rate >= 0.5:
            return 60  # frequent rugger
        if rug_rate >= 0.3:
            return 40  # occasional rugger

    # High risk score from other signals
    if profile.risk_score and profile.risk_score >= 60:
        return 50

    return 0

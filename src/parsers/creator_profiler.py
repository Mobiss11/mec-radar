"""Creator wallet profiling — tracks launch history and scam patterns.

Checks creator's past tokens via local DB. Computes risk score from:
- Total launches (serial launchers are risky)
- Rug rate (>50% rugs = high risk)
- Average peak multiplier (consistently low = pump-and-dump pattern)
"""

from decimal import Decimal

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import CreatorProfile, Token, TokenOutcome


async def profile_creator(
    session: AsyncSession,
    creator_address: str,
) -> CreatorProfile | None:
    """Build or update a creator's profile from DB data.

    Queries all tokens by this creator and their outcomes.
    Returns None if creator has no past launches.
    """
    if not creator_address:
        return None

    # Find all tokens by this creator
    stmt = (
        select(Token.id, TokenOutcome.peak_multiplier, TokenOutcome.is_rug, TokenOutcome.time_to_peak_sec, Token.first_seen_at)
        .outerjoin(TokenOutcome, Token.id == TokenOutcome.token_id)
        .where(Token.creator_address == creator_address)
    )
    result = await session.execute(stmt)
    rows = result.all()

    if not rows:
        return None

    total = len(rows)
    rugged = sum(1 for r in rows if r.is_rug is True)
    success = sum(1 for r in rows if r.peak_multiplier is not None and r.peak_multiplier > Decimal("2.0"))

    multipliers = [float(r.peak_multiplier) for r in rows if r.peak_multiplier is not None]
    avg_mult = Decimal(str(sum(multipliers) / len(multipliers))) if multipliers else None

    peak_times = [r.time_to_peak_sec for r in rows if r.time_to_peak_sec is not None]
    avg_ttp = int(sum(peak_times) / len(peak_times)) if peak_times else None

    last_launch = max((r.first_seen_at for r in rows if r.first_seen_at), default=None)

    # Risk score: 0-100
    risk = _compute_risk(total, rugged, avg_mult)

    stmt = (
        pg_insert(CreatorProfile)
        .values(
            address=creator_address,
            total_launches=total,
            rugged_count=rugged,
            success_count=success,
            avg_peak_multiplier=avg_mult,
            avg_time_to_peak_sec=avg_ttp,
            last_launch_at=last_launch,
            risk_score=risk,
        )
        .on_conflict_do_update(
            index_elements=["address"],
            set_={
                "total_launches": total,
                "rugged_count": rugged,
                "success_count": success,
                "avg_peak_multiplier": avg_mult,
                "avg_time_to_peak_sec": avg_ttp,
                "last_launch_at": last_launch,
                "risk_score": risk,
                "updated_at": func.now(),
            },
        )
        .returning(CreatorProfile)
    )
    result = await session.execute(stmt)
    await session.flush()
    profile = result.scalar_one()

    if risk >= 70:
        logger.info(
            f"[CREATOR] High-risk creator {creator_address[:12]}... "
            f"launches={total} rugs={rugged} risk={risk}"
        )
    return profile


def _compute_risk(total_launches: int, rugged_count: int, avg_multiplier: Decimal | None) -> int:
    """Compute creator risk score 0-100.

    Factors:
    - Serial launcher penalty: 5+ launches → +20, 10+ → +40
    - Rug rate: >50% → +30, >75% → +50
    - Low avg multiplier: <1.5x → +10 (pump-and-dump pattern)
    """
    risk = 0

    # Serial launcher
    if total_launches >= 10:
        risk += 40
    elif total_launches >= 5:
        risk += 20
    elif total_launches >= 3:
        risk += 10

    # Rug rate
    if total_launches > 0:
        rug_rate = rugged_count / total_launches
        if rug_rate > 0.75:
            risk += 50
        elif rug_rate > 0.50:
            risk += 30
        elif rug_rate > 0.25:
            risk += 15

    # Low multiplier pattern
    if avg_multiplier is not None and avg_multiplier < Decimal("1.5"):
        risk += 10

    return min(risk, 100)

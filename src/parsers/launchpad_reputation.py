"""Dynamic launchpad reputation scoring from historical outcomes."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import Integer as SAInteger, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import Token, TokenOutcome

# In-memory cache to avoid repeated DB queries
_cache: dict[str, tuple[float, "LaunchpadRep"]] = {}  # key → (expire_time, rep)
CACHE_TTL_SEC = 3600.0  # 1 hour
MAX_CACHE_SIZE = 200  # max launchpads to cache (prevents unbounded growth)


@dataclass
class LaunchpadRep:
    """Reputation data for a Meteora DBC launchpad."""

    name: str
    total_launches: int
    rug_rate: float  # 0.0 - 1.0
    avg_multiplier: float
    reputation_score: int  # 0-100


def _cache_put(key: str, expire: float, rep: "LaunchpadRep") -> None:
    """Insert into cache with size eviction (remove oldest expired first)."""
    if len(_cache) >= MAX_CACHE_SIZE and key not in _cache:
        # Evict expired entries first
        import time
        now = time.monotonic()
        expired = [k for k, (exp, _) in _cache.items() if exp <= now]
        for k in expired:
            del _cache[k]
        # If still full, evict the entry expiring soonest
        if len(_cache) >= MAX_CACHE_SIZE:
            oldest_key = min(_cache, key=lambda k: _cache[k][0])
            del _cache[oldest_key]
    _cache[key] = (expire, rep)


async def compute_launchpad_reputation(
    session: AsyncSession,
    launchpad: str,
) -> LaunchpadRep:
    """Compute reputation for a launchpad from historical token outcomes.

    Uses cached results for performance (1h TTL).
    """
    import time
    now = time.monotonic()

    key = launchpad.lower()
    if key in _cache:
        expire, rep = _cache[key]
        if now < expire:
            return rep
        else:
            del _cache[key]  # expired — remove before re-computing

    try:
        # Count total launches
        total_stmt = (
            select(func.count())
            .select_from(Token)
            .where(func.lower(Token.dbc_launchpad) == key)
        )
        total_result = await session.execute(total_stmt)
        total = total_result.scalar_one()

        if total < 2:
            rep = LaunchpadRep(
                name=launchpad,
                total_launches=total,
                rug_rate=0.0,
                avg_multiplier=0.0,
                reputation_score=50,  # unknown, neutral
            )
            _cache_put(key, now + CACHE_TTL_SEC, rep)
            return rep

        # Count rugs and avg multiplier from outcomes
        stats_stmt = (
            select(
                func.count().label("outcomes"),
                func.sum(func.cast(TokenOutcome.is_rug, SAInteger)).label("rugs"),
                func.avg(TokenOutcome.peak_multiplier).label("avg_mult"),
            )
            .select_from(TokenOutcome)
            .join(Token, Token.id == TokenOutcome.token_id)
            .where(func.lower(Token.dbc_launchpad) == key)
        )
        stats_result = await session.execute(stats_stmt)
        row = stats_result.one_or_none()

        outcomes = row.outcomes if row and row.outcomes else 0
        rugs = row.rugs if row and row.rugs else 0
        avg_mult = float(row.avg_mult) if row and row.avg_mult else 0.0

        rug_rate = rugs / outcomes if outcomes > 0 else 0.0

        # Compute reputation score (0-100)
        if rug_rate > 0.5:
            score = max(int(20 - rug_rate * 20), 0)
        elif rug_rate > 0.3:
            score = int(50 - rug_rate * 60)
        elif avg_mult > 3.0 and rug_rate < 0.2:
            score = min(int(80 + avg_mult * 2), 100)
        else:
            score = int(60 - rug_rate * 40 + min(avg_mult, 5) * 4)

        score = max(min(score, 100), 0)

        rep = LaunchpadRep(
            name=launchpad,
            total_launches=total,
            rug_rate=round(rug_rate, 3),
            avg_multiplier=round(avg_mult, 2),
            reputation_score=score,
        )

        _cache[key] = (now + CACHE_TTL_SEC, rep)

        logger.debug(
            f"[LAUNCHPAD] {launchpad}: {total} launches, "
            f"rug_rate={rug_rate:.0%}, avg_mult={avg_mult:.1f}x, rep={score}"
        )

        return rep

    except Exception as e:
        logger.debug(f"[LAUNCHPAD] Error for {launchpad}: {e}")
        return LaunchpadRep(
            name=launchpad,
            total_launches=0,
            rug_rate=0.0,
            avg_multiplier=0.0,
            reputation_score=50,
        )


def get_launchpad_score_impact(reputation: LaunchpadRep) -> int:
    """Convert reputation to scoring impact.

    Replaces hardcoded trusted launchpad list.
    """
    if reputation.total_launches < 5:
        return -1  # not enough data, slight caution

    if reputation.reputation_score >= 70:
        return 3
    elif reputation.reputation_score >= 40:
        return 1
    else:
        return -2

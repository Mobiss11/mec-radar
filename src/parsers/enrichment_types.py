"""Types for the enrichment priority queue.

11 stages from +30s to +24h. Dense coverage in first hour for pump detection,
sparse tail for outcome tracking. Hybrid gmgn + DexScreener fetching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class EnrichmentStage(IntEnum):
    """Enrichment stages with delay offsets from discovery time."""

    PRE_SCAN = -1  # +5s — instant reject obvious scams (Phase 12)
    INITIAL = 0  # +8s — full baseline (Birdeye ready by T+5s)
    MIN_2 = 1  # +25s — quick DexScreener price check (was 2m→45s→25s for fast entry)
    MIN_5 = 2  # +5m — holder shift, prune low-score
    MIN_10 = 3  # +10m — price trajectory
    MIN_15 = 4  # +15m — deep gmgn check, prune
    MIN_30 = 5  # +30m — security re-check
    HOUR_1 = 6  # +1h — holder behavior
    HOUR_2 = 7  # +2h — cross-validation
    HOUR_4 = 8  # +4h — deep check
    HOUR_8 = 9  # +8h — trajectory
    HOUR_24 = 10  # +24h — final assessment + outcome


class EnrichmentPriority(IntEnum):
    """Lower number = higher priority in PriorityQueue."""

    MIGRATION = 0
    NORMAL = 1


@dataclass(frozen=True)
class StageConfig:
    """What to fetch and whether to prune at a given stage."""

    offset_sec: int
    fetch_gmgn_info: bool = True
    fetch_security: bool = False
    fetch_top_holders: bool = False
    fetch_dexscreener: bool = False
    check_smart_money: bool = False
    fetch_ohlcv: bool = False
    fetch_trades: bool = False
    fetch_metadata: bool = False
    prune_below_score: int | None = None
    run_prescan: bool = False  # Phase 12: PRE_SCAN instant checks


STAGE_SCHEDULE: dict[EnrichmentStage, StageConfig] = {
    EnrichmentStage.PRE_SCAN: StageConfig(
        offset_sec=5,  # +5s from discovery — instant checks
        fetch_gmgn_info=False,
        run_prescan=True,
    ),
    EnrichmentStage.INITIAL: StageConfig(
        offset_sec=8,   # +8s — Birdeye proven at T+5s (PRE_SCAN), data ready by T+6
        fetch_gmgn_info=True,
        fetch_security=True,
        fetch_top_holders=True,
        check_smart_money=True,
        fetch_metadata=True,
    ),
    EnrichmentStage.MIN_2: StageConfig(
        offset_sec=15,  # Phase 49: reduced from 25s to 15s — faster re-score for tokens near threshold
        fetch_gmgn_info=True,  # quick price check for early pump/dump detection
        fetch_dexscreener=True,
    ),
    EnrichmentStage.MIN_5: StageConfig(
        offset_sec=5 * 60,
        fetch_gmgn_info=False,
        fetch_top_holders=True,
        fetch_dexscreener=True,
        check_smart_money=True,
        fetch_trades=True,
        fetch_ohlcv=True,  # collect candles earlier for volatility
        prune_below_score=20,  # was 15 — less aggressive
    ),
    EnrichmentStage.MIN_10: StageConfig(
        offset_sec=10 * 60,
        fetch_gmgn_info=False,
        fetch_dexscreener=True,
    ),
    EnrichmentStage.MIN_15: StageConfig(
        offset_sec=15 * 60,
        fetch_gmgn_info=True,
        fetch_top_holders=True,
        check_smart_money=True,
        fetch_ohlcv=True,
        fetch_trades=True,
        prune_below_score=25,
    ),
    EnrichmentStage.MIN_30: StageConfig(
        offset_sec=30 * 60,
        fetch_gmgn_info=True,
        fetch_security=True,
    ),
    EnrichmentStage.HOUR_1: StageConfig(
        offset_sec=60 * 60,
        fetch_gmgn_info=False,
        fetch_top_holders=True,
        fetch_dexscreener=True,
        check_smart_money=True,
        fetch_ohlcv=True,
        fetch_trades=True,
    ),
    EnrichmentStage.HOUR_2: StageConfig(
        offset_sec=2 * 60 * 60,
        fetch_gmgn_info=False,
        fetch_dexscreener=True,
    ),
    EnrichmentStage.HOUR_4: StageConfig(
        offset_sec=4 * 60 * 60,
        fetch_gmgn_info=True,
        fetch_security=True,
        fetch_ohlcv=True,
    ),
    EnrichmentStage.HOUR_8: StageConfig(
        offset_sec=8 * 60 * 60,
        fetch_gmgn_info=False,
        fetch_dexscreener=True,
    ),
    EnrichmentStage.HOUR_24: StageConfig(
        offset_sec=24 * 60 * 60,
        fetch_gmgn_info=True,
        fetch_security=True,
    ),
}

# Stage progression: auto-derived from IntEnum order
_STAGE_ORDER = list(EnrichmentStage)
NEXT_STAGE: dict[EnrichmentStage, EnrichmentStage | None] = {
    stage: (_STAGE_ORDER[i + 1] if i + 1 < len(_STAGE_ORDER) else None)
    for i, stage in enumerate(_STAGE_ORDER)
}


@dataclass(order=True)
class EnrichmentTask:
    """A single enrichment job in the priority queue.

    Ordered by (priority, scheduled_at) — migrations always first,
    within same priority — earlier-scheduled tasks first.
    """

    priority: int
    scheduled_at: float  # asyncio loop.time() when this task should run

    # Fields below excluded from ordering
    address: str = field(compare=False)
    stage: EnrichmentStage = field(compare=False, default=EnrichmentStage.INITIAL)
    fetch_security: bool = field(compare=False, default=True)
    is_migration: bool = field(compare=False, default=False)
    discovery_time: float = field(compare=False, default=0.0)
    last_score: int | None = field(compare=False, default=None)
    # Phase 12: PRE_SCAN results carried forward to INITIAL
    instant_rejected: bool = field(compare=False, default=False)
    prescan_risk_boost: int = field(compare=False, default=0)
    # Phase 14A: carry mint_info and sell_sim from PRE_SCAN to INITIAL for signals R23-R24
    prescan_mint_info: Any = field(compare=False, default=None)
    prescan_sell_sim: Any = field(compare=False, default=None)
    # Phase 51: carry Birdeye overview from PRE_SCAN to INITIAL (saves 30 CU + micro-snipe data)
    prescan_birdeye_overview: Any = field(compare=False, default=None)

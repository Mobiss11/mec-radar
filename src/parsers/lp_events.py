"""On-chain LP event detection via Helius enhanced transactions.

Supplements snapshot-based LP monitoring with actual on-chain
LP add/remove events from Raydium, Orca, Meteora etc.
"""

from dataclasses import dataclass

from loguru import logger

from src.parsers.helius.client import HeliusClient

LP_EVENT_TYPES = {"ADD_LIQUIDITY", "REMOVE_LIQUIDITY"}
LP_SOURCES = {"RAYDIUM", "ORCA", "METEORA", "WHIRLPOOL"}


@dataclass
class LPEvent:
    """Single LP add/remove event detected on-chain."""

    type: str  # "add" or "remove"
    source: str  # "RAYDIUM", "ORCA", etc.
    wallet: str
    timestamp: int
    signature: str


@dataclass
class LPEventsResult:
    """Summary of LP events for a token."""

    events: list[LPEvent]
    total_adds: int
    total_removes: int
    score_impact: int


async def detect_lp_events_onchain(
    helius: HeliusClient,
    token_address: str,
    *,
    tx_limit: int = 30,
) -> LPEventsResult | None:
    """Detect LP add/remove events from on-chain transactions.

    Returns LPEventsResult or None if insufficient data.
    """
    try:
        sigs = await helius.get_signatures_for_address(
            token_address, limit=tx_limit
        )
        success_sigs = [s for s in sigs if s.err is None]
        if len(success_sigs) < 3:
            return None

        txs = await helius.get_parsed_transactions(
            [s.signature for s in success_sigs[:30]]
        )

        events: list[LPEvent] = []
        for tx in txs:
            if tx.type in LP_EVENT_TYPES and tx.source in LP_SOURCES:
                event_type = "add" if tx.type == "ADD_LIQUIDITY" else "remove"
                events.append(LPEvent(
                    type=event_type,
                    source=tx.source,
                    wallet=tx.fee_payer,
                    timestamp=tx.timestamp,
                    signature=tx.signature,
                ))

        adds = sum(1 for e in events if e.type == "add")
        removes = sum(1 for e in events if e.type == "remove")

        # Score impact: many removes = bad
        impact = 0
        if removes >= 3 and adds == 0:
            impact = -10
        elif removes >= 2 and removes > adds:
            impact = -5

        if events:
            logger.debug(
                f"[LP-EVENTS] {token_address}: "
                f"{adds} adds, {removes} removes, impact={impact}"
            )

        return LPEventsResult(
            events=events,
            total_adds=adds,
            total_removes=removes,
            score_impact=impact,
        )

    except Exception as e:
        logger.debug(f"[LP-EVENTS] Error for {token_address}: {e}")
        return None

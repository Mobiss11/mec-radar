"""Creator funding trace via Helius — multi-hop analysis (up to 3 hops).

Traces who funded the token creator with SOL through a chain of wallets.
Phase 12: 1 hop. Phase 13: up to 3 hops to catch intermediary wallet laundering.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import CreatorProfile
from src.parsers.helius.client import HeliusClient

MAX_HOPS = 2  # Phase 29: reduced from 3 (configurable via settings.funding_trace_max_hops)
MIN_SOL_AMOUNT = 10_000_000  # 0.01 SOL in lamports


@dataclass
class FundingHop:
    """A single hop in the funding chain."""

    address: str
    age_hours: float | None = None
    tx_count: int = 0
    risk_score: int = 0


@dataclass
class FundingTrace:
    """Result of creator funding analysis."""

    creator: str
    funder: str | None  # direct funder (hop 1)
    funder_age_hours: float | None
    funder_tx_count: int
    funding_risk: int  # 0-90 risk score
    reason: str
    # Phase 13: multi-hop data
    hops: list[FundingHop] = field(default_factory=list)
    chain_depth: int = 0
    chain_age_hours: float | None = None  # max age across entire chain


async def trace_creator_funding(
    session: AsyncSession,
    helius: HeliusClient,
    creator_address: str,
    *,
    max_hops: int = MAX_HOPS,
) -> FundingTrace | None:
    """Trace who funded the token creator (up to N hops back).

    Follows the SOL funding chain: creator ← hop1 ← hop2 ← hop3.
    Stops early if a known rugger is found or chain is broken.
    """
    if not creator_address:
        return None

    try:
        hops: list[FundingHop] = []
        current_address = creator_address
        visited: set[str] = {creator_address}
        max_risk = 0
        worst_reason = "no_trace"

        for hop_idx in range(max_hops):
            funder_address = await _find_funder(helius, current_address)

            if not funder_address:
                if hop_idx == 0:
                    # No funder found at all
                    return FundingTrace(
                        creator=creator_address,
                        funder=None,
                        funder_age_hours=None,
                        funder_tx_count=0,
                        funding_risk=20 if hops else 30,
                        reason="no_sol_funder_found" if not hops else worst_reason,
                        hops=hops,
                        chain_depth=len(hops),
                    )
                break

            # Prevent circular funding chains
            if funder_address in visited:
                worst_reason = "circular_funding_chain"
                max_risk = max(max_risk, 60)
                break
            visited.add(funder_address)

            # Check if this funder is a known rugger
            rugger_trace = await _check_funder_risk(session, funder_address)
            if rugger_trace is not None:
                hops.append(FundingHop(address=funder_address, risk_score=90))
                return FundingTrace(
                    creator=creator_address,
                    funder=hops[0].address if hops else funder_address,
                    funder_age_hours=hops[0].age_hours if hops else None,
                    funder_tx_count=hops[0].tx_count if hops else 0,
                    funding_risk=90,
                    reason=f"rugger_found_at_hop_{hop_idx + 1}",
                    hops=hops,
                    chain_depth=len(hops),
                )

            # Assess this hop's wallet
            hop_info = await _assess_wallet(helius, funder_address)
            hops.append(hop_info)

            if hop_info.risk_score > max_risk:
                max_risk = hop_info.risk_score
                worst_reason = _risk_reason(hop_info)

            current_address = funder_address

        # Multi-hop chain analysis
        chain_depth = len(hops)
        chain_age_hours = _chain_max_age(hops)

        # All hops through fresh wallets = coordinated attack
        if chain_depth >= 2:
            fresh_hops = sum(
                1 for h in hops
                if h.age_hours is not None and h.age_hours < 24
            )
            if fresh_hops == chain_depth:
                max_risk = max(max_risk, 70)
                worst_reason = "all_hops_fresh_wallets"
            elif fresh_hops >= 2:
                max_risk = max(max_risk, 60)
                worst_reason = f"multi_hop_fresh_chain_{fresh_hops}_hops"

        # Build final result
        direct_funder = hops[0] if hops else None
        result = FundingTrace(
            creator=creator_address,
            funder=direct_funder.address if direct_funder else None,
            funder_age_hours=direct_funder.age_hours if direct_funder else None,
            funder_tx_count=direct_funder.tx_count if direct_funder else 0,
            funding_risk=max_risk,
            reason=worst_reason,
            hops=hops,
            chain_depth=chain_depth,
            chain_age_hours=chain_age_hours,
        )

        if chain_depth > 1:
            logger.info(
                f"[FUNDING] {creator_address[:12]}: {chain_depth}-hop chain, "
                f"risk={max_risk}, reason={worst_reason}"
            )
        elif direct_funder:
            logger.debug(
                f"[FUNDING] {creator_address[:12]} funded by {direct_funder.address[:12]} "
                f"(risk={max_risk})"
            )

        return result

    except Exception as e:
        logger.debug(f"[FUNDING] Error tracing {creator_address}: {e}")
        return None


async def _find_funder(
    helius: HeliusClient, address: str
) -> str | None:
    """Find who sent SOL to this address (largest inflow)."""
    sigs = await helius.get_signatures_for_address(address, limit=20)
    if not sigs:
        return None

    sig_strs = [s.signature for s in sigs[:20]]
    txs = await helius.get_parsed_transactions(sig_strs)

    for tx in txs:
        for nt in tx.native_transfers:
            if (
                nt.to_user_account == address
                and nt.amount > MIN_SOL_AMOUNT
                and nt.from_user_account != address
            ):
                return nt.from_user_account

    return None


async def _assess_wallet(
    helius: HeliusClient, address: str
) -> FundingHop:
    """Assess a wallet's age and activity for risk scoring."""
    sigs = await helius.get_signatures_for_address(address, limit=50)
    tx_count = len(sigs)

    age_hours = None
    if sigs:
        oldest = sigs[-1]
        if oldest.timestamp > 0:
            age_sec = datetime.now(UTC).timestamp() - oldest.timestamp
            age_hours = age_sec / 3600

    # Risk assessment
    if age_hours is not None and age_hours < 1:
        risk = 50
    elif age_hours is not None and age_hours < 24:
        risk = 30
    elif tx_count < 5:
        risk = 25
    else:
        risk = 10

    return FundingHop(
        address=address,
        age_hours=age_hours,
        tx_count=tx_count,
        risk_score=risk,
    )


def _risk_reason(hop: FundingHop) -> str:
    """Generate reason string from hop assessment."""
    if hop.age_hours is not None and hop.age_hours < 1:
        return "funder_wallet_very_new"
    if hop.age_hours is not None and hop.age_hours < 24:
        return "funder_wallet_new"
    if hop.tx_count < 5:
        return "funder_low_activity"
    return "funder_established"


def _chain_max_age(hops: list[FundingHop]) -> float | None:
    """Get max age across the funding chain."""
    ages = [h.age_hours for h in hops if h.age_hours is not None]
    return max(ages) if ages else None


async def _check_funder_risk(
    session: AsyncSession, funder_address: str
) -> FundingTrace | None:
    """Check if funder is a known rugger in our DB."""
    stmt = select(CreatorProfile).where(CreatorProfile.address == funder_address)
    result = await session.execute(stmt)
    profile = result.scalar_one_or_none()

    if profile and profile.risk_score is not None and profile.risk_score >= 60:
        return FundingTrace(
            creator="",  # will be replaced by caller
            funder=funder_address,
            funder_age_hours=None,
            funder_tx_count=0,
            funding_risk=80,
            reason="funder_is_known_rugger",
        )

    return None

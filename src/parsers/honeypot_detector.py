"""On-chain honeypot detection via Helius enhanced transactions.

Analyses recent transactions for a token to detect sell failures
(indicator of honeypot / hidden tax / frozen token).
"""

from dataclasses import dataclass

from loguru import logger

from src.parsers.helius.client import HeliusClient


@dataclass
class HoneypotResult:
    """Result of on-chain honeypot analysis."""

    total_sells: int
    failed_sells: int
    failed_ratio: float  # 0.0 - 1.0
    is_honeypot: bool  # confirmed if ratio > 30%
    is_suspected: bool  # suspected if ratio > 10%
    score_impact: int  # 0 to -25


async def detect_honeypot_onchain(
    helius: HeliusClient,
    token_address: str,
    *,
    tx_limit: int = 50,
) -> HoneypotResult | None:
    """Check for honeypot patterns by analysing recent token transactions.

    Fetches recent signatures for the token, then analyses parsed
    transactions for failed sell attempts.

    Returns None if insufficient data.
    """
    try:
        sigs = await helius.get_signatures_for_address(
            token_address, limit=tx_limit
        )
        if len(sigs) < 5:
            return None

        # Separate failed vs successful
        failed_sigs = [s for s in sigs if s.err is not None]
        success_sigs = [s for s in sigs if s.err is None]

        if not success_sigs:
            return None

        # Parse successful transactions to identify sells
        success_txs = await helius.get_parsed_transactions(
            [s.signature for s in success_sigs[:30]]
        )

        total_sells = 0
        for tx in success_txs:
            # Sell = token transferred FROM user TO pool
            if tx.type in ("SWAP", "TRANSFER") and tx.token_transfers:
                for tt in tx.token_transfers:
                    if tt.mint == token_address and tt.token_amount > 0:
                        total_sells += 1
                        break

        # Estimate failed sells from failed tx count
        # (failed txs with the token as subject are likely failed sells)
        failed_sells = len(failed_sigs)

        total = total_sells + failed_sells
        if total < 3:
            return None

        ratio = failed_sells / total

        is_honeypot = ratio > 0.30
        is_suspected = ratio > 0.10

        if is_honeypot:
            impact = -25
        elif is_suspected:
            impact = -10
        else:
            impact = 0

        if is_suspected:
            logger.info(
                f"[HONEYPOT] {token_address}: {failed_sells}/{total} "
                f"failed sells ({ratio:.0%}) — "
                f"{'CONFIRMED' if is_honeypot else 'SUSPECTED'}"
            )

        return HoneypotResult(
            total_sells=total_sells,
            failed_sells=failed_sells,
            failed_ratio=ratio,
            is_honeypot=is_honeypot,
            is_suspected=is_suspected,
            score_impact=impact,
        )

    except Exception as e:
        logger.debug(f"[HONEYPOT] Error for {token_address}: {e}")
        return None

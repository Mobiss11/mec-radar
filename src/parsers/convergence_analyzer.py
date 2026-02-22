"""Convergence analysis — detect token destination consolidation.

If first-block buyers all send their tokens to ONE wallet,
it's a guaranteed dev consolidation / rug pull.
"""

from collections import Counter
from dataclasses import dataclass, field

from loguru import logger

from src.parsers.helius.client import HeliusClient


@dataclass
class ConvergenceResult:
    """Result of convergence analysis."""

    converging: bool = False
    convergence_pct: float = 0.0  # 0.0-1.0
    main_destination: str | None = None
    destinations: dict[str, int] = field(default_factory=dict)  # address → count
    total_tracked: int = 0
    error: str | None = None

    @property
    def risk_boost(self) -> int:
        """Risk score boost based on convergence."""
        if self.converging:
            return 35
        if self.convergence_pct > 0.3:
            return 15
        return 0


async def analyze_convergence(
    helius: HeliusClient,
    token_mint: str,
    buyers: list[str],
    creator_address: str = "",
    max_buyers: int = 10,
) -> ConvergenceResult:
    """Check if first-block buyers send tokens to a common destination.

    Analyzes outgoing token transfers from each buyer for the given mint.
    If >50% of buyers → 1 destination, convergence is detected.
    """
    if len(buyers) < 2:
        return ConvergenceResult(total_tracked=len(buyers))

    try:
        destination_counts: Counter[str] = Counter()
        tracked = 0

        # Check each buyer's outgoing token transfers
        for buyer in buyers[:max_buyers]:  # Phase 29: configurable cap (was 15)
            sigs = await helius.get_signatures_for_address(buyer, limit=10)
            if not sigs:
                continue

            sig_strs = [s.signature for s in sigs[:10]]
            txs = await helius.get_parsed_transactions(sig_strs)

            for tx in txs:
                for tt in tx.token_transfers:
                    if (
                        tt.mint == token_mint
                        and tt.from_user_account == buyer
                        and tt.to_user_account
                        and tt.to_user_account != buyer
                        and tt.to_user_account != creator_address
                        and tt.token_amount > 0
                    ):
                        destination_counts[tt.to_user_account] += 1
                        tracked += 1
                        break  # One destination per buyer is enough

        if tracked < 2:
            return ConvergenceResult(total_tracked=tracked)

        # Find the most common destination
        most_common_dest, most_common_count = destination_counts.most_common(1)[0]
        convergence_pct = most_common_count / tracked

        converging = convergence_pct > 0.5

        result = ConvergenceResult(
            converging=converging,
            convergence_pct=convergence_pct,
            main_destination=most_common_dest if converging else None,
            destinations=dict(destination_counts),
            total_tracked=tracked,
        )

        if converging:
            logger.warning(
                f"[CONVERGENCE] {token_mint[:12]}: {most_common_count}/{tracked} buyers "
                f"→ {most_common_dest[:12]} ({convergence_pct:.0%})"
            )

        return result

    except Exception as e:
        logger.warning(
            f"[CONVERGENCE] Error analyzing {token_mint[:12]}: {e}"
        )
        return ConvergenceResult(error=str(e))

"""Fee payer clustering — detect sybil attacks via shared fee payers.

If 10 "different" buyers share the SAME fee payer, it's 1 person.
Uses first-block transaction data from Helius.
"""

from collections import Counter
from dataclasses import dataclass, field

from loguru import logger

from src.parsers.helius.client import HeliusClient
from src.parsers.helius.models import HeliusSignature, HeliusTransaction


@dataclass
class FeePayerCluster:
    """A group of buyers sharing the same fee payer."""

    fee_payer: str
    buyers: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.buyers)


@dataclass
class FeePayerClusterResult:
    """Result of fee payer clustering analysis."""

    total_buyers: int = 0
    unique_payers: int = 0
    clusters: list[FeePayerCluster] = field(default_factory=list)
    sybil_score: float = 0.0
    largest_cluster_size: int = 0
    error: str | None = None

    @property
    def risk_boost(self) -> int:
        """Risk score boost based on sybil score."""
        if self.sybil_score > 0.5:
            return 25
        if self.sybil_score > 0.3:
            return 15
        return 0


async def cluster_by_fee_payer(
    helius: HeliusClient,
    token_address: str,
    creator_address: str,
) -> FeePayerClusterResult:
    """Cluster first-block buyers by their fee payer address.

    Sybil score = 1 - (unique_payers / total_buyers).
    If all buyers share 1 fee payer, sybil_score = 1.0.
    """
    try:
        sigs = await helius.get_signatures_for_address(token_address, limit=30)
        if not sigs:
            return FeePayerClusterResult(error="No signatures found")

        sigs.sort(key=lambda s: s.slot)
        creation_slot = sigs[0].slot
        if creation_slot == 0:
            return FeePayerClusterResult(error="Invalid slot data")

        # First-block: same slot or slot+1, successful only
        first_block_sigs = [
            s for s in sigs
            if s.slot <= creation_slot + 1 and s.err is None
        ]

        if len(first_block_sigs) < 3:
            # Need at least 3 txs for meaningful clustering
            return FeePayerClusterResult(total_buyers=len(first_block_sigs))

        sig_ids = [s.signature for s in first_block_sigs[:20]]
        txs = await helius.get_parsed_transactions(sig_ids)
        if not txs:
            return FeePayerClusterResult(error="No parsed transactions")

        # Map fee_payer → set of unique buyer/receiver addresses
        payer_to_buyers: dict[str, set[str]] = {}
        all_receivers: set[str] = set()

        for tx in txs:
            if not tx.fee_payer or tx.fee_payer == creator_address:
                continue

            fee_payer = tx.fee_payer
            payer_to_buyers.setdefault(fee_payer, set())

            # Check token_transfers to find actual token receivers
            found_receiver = False
            for tt in tx.token_transfers:
                if (
                    tt.to_user_account
                    and tt.to_user_account != creator_address
                    and tt.to_user_account not in all_receivers
                ):
                    payer_to_buyers[fee_payer].add(tt.to_user_account)
                    all_receivers.add(tt.to_user_account)
                    found_receiver = True

            # If no token transfer receivers, the fee_payer itself is the buyer
            if not found_receiver and fee_payer not in all_receivers:
                payer_to_buyers[fee_payer].add(fee_payer)
                all_receivers.add(fee_payer)

        total_buyers = sum(len(b) for b in payer_to_buyers.values())
        if total_buyers == 0:
            return FeePayerClusterResult(total_buyers=0)

        unique_payers = len(payer_to_buyers)
        sybil_score = 1.0 - (unique_payers / total_buyers) if total_buyers > 1 else 0.0
        sybil_score = max(0.0, min(1.0, sybil_score))

        clusters = [
            FeePayerCluster(fee_payer=payer, buyers=sorted(buyers_set))
            for payer, buyers_set in payer_to_buyers.items()
        ]
        clusters.sort(key=lambda c: c.size, reverse=True)
        largest = clusters[0].size if clusters else 0

        result = FeePayerClusterResult(
            total_buyers=total_buyers,
            unique_payers=unique_payers,
            clusters=clusters,
            sybil_score=sybil_score,
            largest_cluster_size=largest,
        )

        if result.sybil_score > 0.3:
            logger.info(
                f"[FEE_PAYER] {token_address[:12]}: sybil_score={sybil_score:.2f} "
                f"({unique_payers} payers / {total_buyers} buyers, "
                f"largest_cluster={largest})"
            )

        return result

    except Exception as e:
        logger.warning(
            f"[FEE_PAYER] Error clustering for {token_address[:12]}: {e}"
        )
        return FeePayerClusterResult(error=str(e))

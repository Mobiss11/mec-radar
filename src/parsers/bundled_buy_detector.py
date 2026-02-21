"""Bundled buy detection — detect coordinated first-block sybil attacks.

Analyzes first-block transactions to detect if the creator funded
multiple wallets to buy in the same block (coordinated pump).
"""

from dataclasses import dataclass

from loguru import logger

from src.parsers.helius.client import HeliusClient


@dataclass
class BundledBuyResult:
    """Result of bundled buy analysis."""

    first_block_buyers: int = 0
    funded_by_creator: int = 0
    bundled_pct: float = 0.0
    is_bundled: bool = False
    error: str | None = None

    @property
    def risk_boost(self) -> int:
        """Risk score boost based on bundled buy percentage."""
        if self.bundled_pct > 50:
            return 30
        if self.bundled_pct > 25:
            return 15
        return 0


async def detect_bundled_buys(
    helius: HeliusClient,
    token_address: str,
    creator_address: str,
) -> BundledBuyResult:
    """Detect if first-block buys were funded by the creator.

    1. Fetch first ~30 signatures for the token
    2. Find creation slot
    3. Filter buys in same slot (or slot+1)
    4. For each buyer, check if they received SOL from creator
    """
    try:
        sigs = await helius.get_signatures_for_address(
            token_address, limit=30
        )
        if not sigs:
            return BundledBuyResult(error="No signatures found")

        # Sort by slot ascending (earliest first)
        sigs.sort(key=lambda s: s.slot)

        # Creation slot = first signature's slot
        creation_slot = sigs[0].slot
        if creation_slot == 0:
            return BundledBuyResult(error="Invalid slot data")

        # First-block signatures (same slot or slot+1)
        first_block_sigs = [
            s for s in sigs
            if s.slot <= creation_slot + 1 and s.err is None
        ]

        if len(first_block_sigs) < 2:
            return BundledBuyResult(first_block_buyers=len(first_block_sigs))

        # Get parsed transactions for first block
        sig_ids = [s.signature for s in first_block_sigs[:20]]  # Max 20
        txs = await helius.get_parsed_transactions(sig_ids)

        if not txs:
            return BundledBuyResult(error="No parsed transactions")

        # Identify unique buyers (fee payers, excluding creator)
        buyer_addresses: set[str] = set()
        for tx in txs:
            if tx.fee_payer and tx.fee_payer != creator_address:
                buyer_addresses.add(tx.fee_payer)

        if not buyer_addresses:
            return BundledBuyResult(first_block_buyers=0)

        # Check if buyers received SOL from creator in these transactions
        funded_by_creator = 0
        for tx in txs:
            for transfer in tx.native_transfers:
                if (
                    transfer.from_user_account == creator_address
                    and transfer.to_user_account in buyer_addresses
                    and transfer.amount > 0
                ):
                    funded_by_creator += 1
                    buyer_addresses.discard(transfer.to_user_account)

        total_buyers = len(buyer_addresses) + funded_by_creator
        bundled_pct = (funded_by_creator / total_buyers * 100) if total_buyers > 0 else 0

        result = BundledBuyResult(
            first_block_buyers=total_buyers,
            funded_by_creator=funded_by_creator,
            bundled_pct=bundled_pct,
            is_bundled=bundled_pct > 50,
        )

        if result.is_bundled:
            logger.info(
                f"[BUNDLED] {token_address[:12]}: {funded_by_creator}/{total_buyers} "
                f"first-block buyers funded by creator ({bundled_pct:.0f}%)"
            )

        return result

    except Exception as e:
        logger.warning(
            f"[BUNDLED] Error detecting bundled buys for {token_address[:12]}: {e}"
        )
        return BundledBuyResult(error=str(e))

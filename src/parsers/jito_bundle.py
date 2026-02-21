"""Jito bundle snipe detection — detect self-snipe in first block.

Identifies tokens where the creator (or an associate) used a Jito MEV bundle
to atomically create the token AND buy it in the same slot. This is a strong
rug-pull indicator because the creator secures a position before anyone else
can react.

Detection method:
1. Fetch first-block transactions for the token via Helius
2. Check if any transaction in the creation slot includes a transfer to
   one of the 8 static Jito tip accounts
3. If found, identify the sniper wallet(s) and compute risk

Cost: 0 API calls beyond existing Helius (uses parsed transaction data).
"""

from dataclasses import dataclass, field

from loguru import logger

from src.parsers.helius.client import HeliusClient
from src.parsers.helius.models import HeliusTransaction


# 8 static Jito tip accounts — these never change
JITO_TIP_ACCOUNTS: frozenset[str] = frozenset({
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
})


@dataclass
class JitoBundleResult:
    """Result of Jito bundle snipe detection."""

    jito_bundle_detected: bool = False
    sniper_wallets: list[str] = field(default_factory=list)
    tip_account_used: str | None = None
    tip_amount_sol: float | None = None
    sniper_count: int = 0

    @property
    def score_impact(self) -> int:
        """Negative score impact for bundled snipes."""
        if not self.jito_bundle_detected:
            return 0
        if self.sniper_count >= 3:
            return -15  # Multiple snipers = coordinated
        if self.sniper_count >= 1:
            return -10  # Single sniper = self-snipe
        return 0

    @property
    def risk_boost(self) -> int:
        """Risk boost value for scoring."""
        return abs(self.score_impact)


async def detect_jito_bundle(
    helius: HeliusClient,
    token_address: str,
    creator_address: str | None = None,
    *,
    tx_limit: int = 20,
) -> JitoBundleResult:
    """Detect Jito bundle snipes in the token's first transactions.

    Fetches early transaction signatures for the token and checks
    if any involve transfers to Jito tip accounts in the same slot
    as the token creation.

    Args:
        helius: HeliusClient for fetching transaction data.
        token_address: The token mint address to check.
        creator_address: Optional creator wallet for correlation.
        tx_limit: Max transactions to analyze (first N).

    Returns:
        JitoBundleResult with detection details.
    """
    result = JitoBundleResult()

    try:
        # Get earliest signatures for the token
        sigs = await helius.get_signatures_for_address(
            token_address, limit=tx_limit
        )
        if not sigs:
            return result

        # Find the creation slot (earliest transaction)
        creation_slot: int | None = None
        for sig_info in reversed(sigs):
            if sig_info.slot:
                creation_slot = sig_info.slot
                break

        if creation_slot is None:
            return result

        # Collect signatures from creation slot (and slot+1 for near-misses)
        creation_sigs: list[str] = []
        for sig_info in reversed(sigs):
            if sig_info.slot > creation_slot + 1:
                break  # Only check first 2 slots
            if sig_info.signature:
                creation_sigs.append(sig_info.signature)

        if not creation_sigs:
            return result

        # Batch-fetch parsed transactions via Helius Enhanced API
        txs: list[HeliusTransaction] = await helius.get_parsed_transactions(
            creation_sigs
        )

        sniper_wallets: list[str] = []
        tip_account: str | None = None
        tip_amount: float | None = None

        for tx in txs:
            # Check native transfers for Jito tip accounts
            jito_tip = _find_jito_tip(tx)
            if jito_tip is None:
                continue

            tip_acct, amount = jito_tip

            # Fee payer is the sniper (first signer)
            if tx.fee_payer and tx.fee_payer not in sniper_wallets:
                sniper_wallets.append(tx.fee_payer)

            if tip_amount is None:
                tip_amount = amount

            if tip_account is None:
                tip_account = tip_acct

        if sniper_wallets:
            result.jito_bundle_detected = True
            result.sniper_wallets = sniper_wallets
            result.tip_account_used = tip_account
            result.tip_amount_sol = tip_amount
            result.sniper_count = len(sniper_wallets)

            # If creator is one of the snipers — extra suspicious
            if creator_address and creator_address in sniper_wallets:
                logger.info(
                    f"[JITO] Creator self-snipe detected for {token_address[:12]}"
                )

    except Exception as e:
        logger.debug(f"[JITO] Detection failed for {token_address[:12]}: {e}")

    return result


def _find_jito_tip(tx: HeliusTransaction) -> tuple[str, float] | None:
    """Find Jito tip transfer in a parsed transaction.

    Returns (tip_account, tip_amount_sol) or None.
    """
    for transfer in tx.native_transfers:
        if transfer.to_user_account in JITO_TIP_ACCOUNTS and transfer.amount > 0:
            return transfer.to_user_account, transfer.amount / 1e9
    return None

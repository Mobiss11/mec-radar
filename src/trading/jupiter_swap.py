"""Jupiter swap execution — quote, build transaction, sign, send, confirm.

Full pipeline:
  1. GET /swap/v1/quote — get optimal route
  2. POST /swap/v1/swap — build serialized transaction
  3. Deserialize + sign with solders
  4. Send via RPC sendTransaction
  5. Poll getSignatureStatuses until confirmed

Reuses retry patterns from src/parsers/jupiter/client.py.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from decimal import Decimal

import httpx
from loguru import logger
from solders.keypair import Keypair  # type: ignore[import-untyped]
from solders.transaction import VersionedTransaction  # type: ignore[import-untyped]

from src.parsers.rate_limiter import RateLimiter

QUOTE_URL = "https://api.jup.ag/swap/v1/quote"
SWAP_URL = "https://api.jup.ag/swap/v1/swap"
WSOL_MINT = "So11111111111111111111111111111111111111112"

MAX_RETRIES = 2
RETRY_DELAYS = [1.0, 3.0]

LAMPORTS_PER_SOL = 1_000_000_000

# Confirmation polling
CONFIRM_POLL_INTERVAL = 2.0  # seconds
CONFIRM_TIMEOUT = 60  # seconds


@dataclass
class SwapResult:
    """Result of a swap execution attempt."""

    success: bool
    tx_hash: str | None = None
    input_amount: Decimal | None = None
    output_amount: Decimal | None = None
    price_impact_pct: float | None = None
    fee_sol: Decimal | None = None
    error: str | None = None
    is_retryable: bool = False


class JupiterSwapClient:
    """Executes Jupiter swaps on Solana mainnet.

    Handles the full lifecycle: quote → transaction → sign → send → confirm.
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        rpc_url: str,
        keypair: Keypair,
        max_rps: float = 1.0,
        default_slippage_bps: int = 500,
        priority_fee_lamports: int | str = "auto",
    ) -> None:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key

        self._http = httpx.AsyncClient(timeout=15.0, headers=headers)
        self._rpc_http = httpx.AsyncClient(timeout=30.0)
        self._rpc_url = rpc_url
        self._keypair = keypair
        self._rate_limiter = RateLimiter(max_rps)
        self._slippage_bps = default_slippage_bps
        self._priority_fee = priority_fee_lamports

    async def buy_token(
        self,
        mint: str,
        sol_amount_lamports: int,
        *,
        slippage_bps: int | None = None,
    ) -> SwapResult:
        """Buy token with SOL. Full pipeline: quote → swap → sign → send → confirm.

        Args:
            mint: Token mint address to buy.
            sol_amount_lamports: Amount of SOL in lamports to spend.
            slippage_bps: Override default slippage tolerance (basis points).

        Returns:
            SwapResult with success status and transaction details.
        """
        slippage = slippage_bps or self._slippage_bps
        logger.info(
            f"[SWAP] Buying {mint[:12]} with {sol_amount_lamports / LAMPORTS_PER_SOL:.4f} SOL "
            f"(slippage: {slippage}bps)"
        )

        # Step 1: Get quote (SOL → token)
        quote = await self._get_quote(
            input_mint=WSOL_MINT,
            output_mint=mint,
            amount=sol_amount_lamports,
            slippage_bps=slippage,
        )
        if quote is None:
            return SwapResult(success=False, error="Quote failed", is_retryable=True)

        # Check price impact
        price_impact = float(quote.get("priceImpactPct", 0) or 0)
        if price_impact > 10.0:
            return SwapResult(
                success=False,
                error=f"Price impact too high: {price_impact:.1f}%",
                price_impact_pct=price_impact,
            )

        # Step 2: Get swap transaction
        swap_tx_b64 = await self._get_swap_transaction(quote)
        if swap_tx_b64 is None:
            return SwapResult(
                success=False, error="Swap transaction build failed", is_retryable=True
            )

        # Step 3: Sign + send + confirm
        result = await self._sign_send_confirm(swap_tx_b64, quote)

        if result.success:
            logger.info(
                f"[SWAP] BUY confirmed: {mint[:12]} tx={result.tx_hash} "
                f"impact={result.price_impact_pct:.2f}%"
            )
        else:
            logger.warning(f"[SWAP] BUY failed: {mint[:12]} error={result.error}")

        return result

    async def sell_token(
        self,
        mint: str,
        token_amount_raw: int,
        *,
        slippage_bps: int | None = None,
    ) -> SwapResult:
        """Sell token for SOL. Full pipeline: quote → swap → sign → send → confirm.

        Args:
            mint: Token mint address to sell.
            token_amount_raw: Amount of tokens in raw units (smallest denomination).
            slippage_bps: Override default slippage tolerance (basis points).

        Returns:
            SwapResult with success status and transaction details.
        """
        slippage = slippage_bps or self._slippage_bps
        logger.info(f"[SWAP] Selling {mint[:12]} amount={token_amount_raw} (slippage: {slippage}bps)")

        # Step 1: Get quote (token → SOL)
        quote = await self._get_quote(
            input_mint=mint,
            output_mint=WSOL_MINT,
            amount=token_amount_raw,
            slippage_bps=slippage,
        )
        if quote is None:
            return SwapResult(success=False, error="Sell quote failed", is_retryable=True)

        # Step 2: Get swap transaction
        swap_tx_b64 = await self._get_swap_transaction(quote)
        if swap_tx_b64 is None:
            return SwapResult(
                success=False, error="Sell tx build failed", is_retryable=True
            )

        # Step 3: Sign + send + confirm
        result = await self._sign_send_confirm(swap_tx_b64, quote)

        if result.success:
            logger.info(
                f"[SWAP] SELL confirmed: {mint[:12]} tx={result.tx_hash} "
                f"out={result.output_amount} SOL"
            )
        else:
            logger.warning(f"[SWAP] SELL failed: {mint[:12]} error={result.error}")

        return result

    # ─── Internal methods ────────────────────────────────────────────

    async def _get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int,
    ) -> dict | None:
        """GET /swap/v1/quote with retry."""
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": str(slippage_bps),
        }

        for attempt in range(MAX_RETRIES + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._http.get(QUOTE_URL, params=params)

                if resp.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[SWAP] Quote rate limited, waiting {delay}s")
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code == 400:
                    data = resp.json()
                    error_msg = data.get("error", data.get("message", "Bad request"))
                    logger.warning(f"[SWAP] Quote 400: {error_msg}")
                    return None

                if resp.status_code >= 500:
                    if attempt < MAX_RETRIES:
                        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                        logger.debug(f"[SWAP] Quote {resp.status_code}, retry in {delay}s")
                        await asyncio.sleep(delay)
                        continue
                    logger.warning(f"[SWAP] Quote failed: HTTP {resp.status_code}")
                    return None

                if resp.status_code != 200:
                    logger.warning(f"[SWAP] Quote unexpected HTTP {resp.status_code}")
                    return None

                return resp.json()

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[SWAP] Quote {type(e).__name__}, retry in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"[SWAP] Quote failed after retries: {e}")
                    return None

        return None

    async def _get_swap_transaction(self, quote: dict) -> str | None:
        """POST /swap/v1/swap to get serialized transaction."""
        payload = {
            "quoteResponse": quote,
            "userPublicKey": str(self._keypair.pubkey()),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": self._priority_fee,
        }

        for attempt in range(MAX_RETRIES + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._http.post(SWAP_URL, json=payload)

                if resp.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[SWAP] Swap API rate limited, waiting {delay}s")
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code == 400:
                    data = resp.json()
                    error_msg = data.get("error", data.get("message", "Bad request"))
                    logger.warning(f"[SWAP] Swap 400: {error_msg}")
                    return None

                if resp.status_code >= 500:
                    if attempt < MAX_RETRIES:
                        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                        await asyncio.sleep(delay)
                        continue
                    logger.warning(f"[SWAP] Swap failed: HTTP {resp.status_code}")
                    return None

                if resp.status_code != 200:
                    logger.warning(f"[SWAP] Swap unexpected HTTP {resp.status_code}")
                    return None

                data = resp.json()
                swap_tx = data.get("swapTransaction")
                if not swap_tx:
                    logger.warning("[SWAP] No swapTransaction in response")
                    return None
                return swap_tx

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"[SWAP] Swap TX build failed after retries: {e}")
                    return None

        return None

    async def _sign_send_confirm(
        self, swap_tx_b64: str, quote: dict
    ) -> SwapResult:
        """Deserialize tx, sign, send via RPC, poll confirmation."""
        price_impact = float(quote.get("priceImpactPct", 0) or 0)

        # 1. Deserialize transaction
        try:
            tx_bytes = base64.b64decode(swap_tx_b64)
            tx = VersionedTransaction.from_bytes(tx_bytes)
        except Exception as e:
            return SwapResult(
                success=False,
                error=f"TX deserialize failed: {e}",
                price_impact_pct=price_impact,
            )

        # 2. Sign transaction
        try:
            msg_bytes = bytes(tx.message)
            signature = self._keypair.sign_message(msg_bytes)
            signed_tx = VersionedTransaction.populate(tx.message, [signature])
        except Exception as e:
            return SwapResult(
                success=False,
                error=f"TX sign failed: {e}",
                price_impact_pct=price_impact,
            )

        # 3. Send via RPC sendTransaction
        tx_b64 = base64.b64encode(bytes(signed_tx)).decode("ascii")
        tx_hash = await self._send_raw_transaction(tx_b64)
        if tx_hash is None:
            return SwapResult(
                success=False,
                error="sendTransaction RPC failed",
                is_retryable=True,
                price_impact_pct=price_impact,
            )

        # 4. Poll confirmation
        confirmed = await self._wait_for_confirmation(tx_hash)
        if not confirmed:
            return SwapResult(
                success=False,
                tx_hash=tx_hash,
                error=f"Confirmation timeout ({CONFIRM_TIMEOUT}s)",
                is_retryable=False,
                price_impact_pct=price_impact,
            )

        # 5. Build success result
        in_amount_raw = int(quote.get("inAmount", 0))
        out_amount_raw = int(quote.get("outAmount", 0))

        # Determine which side is SOL for amount conversion
        input_mint = quote.get("inputMint", "")
        if input_mint == WSOL_MINT:
            # Buying: input is SOL, output is token
            input_sol = Decimal(str(in_amount_raw)) / Decimal(str(LAMPORTS_PER_SOL))
            output_amount = Decimal(str(out_amount_raw))  # raw token units
        else:
            # Selling: input is token, output is SOL
            input_sol = Decimal("0")
            output_amount = Decimal(str(out_amount_raw)) / Decimal(str(LAMPORTS_PER_SOL))

        return SwapResult(
            success=True,
            tx_hash=tx_hash,
            input_amount=Decimal(str(in_amount_raw)),
            output_amount=Decimal(str(out_amount_raw)),
            price_impact_pct=price_impact,
            fee_sol=input_sol * Decimal("0.0001"),  # Approximate: priority fee
        )

    async def _send_raw_transaction(self, tx_b64: str) -> str | None:
        """Send transaction via RPC sendTransaction method."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                tx_b64,
                {
                    "encoding": "base64",
                    "skipPreflight": False,
                    "preflightCommitment": "confirmed",
                    "maxRetries": 3,
                },
            ],
        }

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = await self._rpc_http.post(self._rpc_url, json=payload)
                if resp.status_code != 200:
                    logger.warning(f"[SWAP] sendTransaction HTTP {resp.status_code}")
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                        continue
                    return None

                data = resp.json()

                if "error" in data:
                    error = data["error"]
                    code = error.get("code", "?")
                    msg = error.get("message", str(error))
                    logger.warning(f"[SWAP] sendTransaction RPC error {code}: {msg}")
                    # Don't retry certain errors
                    if "Blockhash not found" in msg or "insufficient" in msg.lower():
                        return None
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                        continue
                    return None

                result = data.get("result")
                if result:
                    logger.debug(f"[SWAP] TX sent: {result}")
                    return str(result)

                return None

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[SWAP] sendTransaction {type(e).__name__}, retry in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"[SWAP] sendTransaction failed after retries: {e}")
                    return None

        return None

    async def _wait_for_confirmation(
        self,
        tx_hash: str,
        timeout: int = CONFIRM_TIMEOUT,
    ) -> bool:
        """Poll getSignatureStatuses until confirmed or timeout."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignatureStatuses",
            "params": [[tx_hash], {"searchTransactionHistory": False}],
        }

        elapsed = 0.0
        while elapsed < timeout:
            try:
                resp = await self._rpc_http.post(self._rpc_url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    statuses = data.get("result", {}).get("value", [])
                    if statuses and statuses[0] is not None:
                        status = statuses[0]
                        confirmation = status.get("confirmationStatus", "")
                        err = status.get("err")

                        if err:
                            logger.warning(f"[SWAP] TX {tx_hash[:16]} error on-chain: {err}")
                            return False

                        if confirmation in ("confirmed", "finalized"):
                            logger.debug(
                                f"[SWAP] TX {tx_hash[:16]} {confirmation} "
                                f"in {elapsed:.1f}s"
                            )
                            return True

            except (httpx.TimeoutException, httpx.ConnectError):
                pass  # Retry on next poll

            await asyncio.sleep(CONFIRM_POLL_INTERVAL)
            elapsed += CONFIRM_POLL_INTERVAL

        logger.warning(f"[SWAP] TX {tx_hash[:16]} confirmation timeout after {timeout}s")
        return False

    async def close(self) -> None:
        """Close HTTP clients."""
        await self._http.aclose()
        await self._rpc_http.aclose()

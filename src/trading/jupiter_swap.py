"""Jupiter swap execution — quote, build transaction, sign, send, confirm.

Full pipeline:
  1. GET /swap/v1/quote — get optimal route
  2. POST /swap/v1/swap-instructions — get swap instructions
  3. Build TX with fresh blockhash from our RPC
  4. Sign + send via RPC sendTransaction
  5. Poll getSignatureStatuses with resend until confirmed

Uses swap-instructions (not swap) to avoid stale blockhash issue:
Jupiter's /swap endpoint can return TX with expired blockhash,
especially under RPC latency. Building TX ourselves with fresh
blockhash from our RPC ensures reliable landing.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from decimal import Decimal

import httpx
from loguru import logger
from solders.hash import Hash  # type: ignore[import-untyped]
from solders.instruction import AccountMeta, Instruction  # type: ignore[import-untyped]
from solders.keypair import Keypair  # type: ignore[import-untyped]
from solders.message import MessageV0  # type: ignore[import-untyped]
from solders.pubkey import Pubkey  # type: ignore[import-untyped]
from solders.transaction import VersionedTransaction  # type: ignore[import-untyped]

from src.parsers.rate_limiter import RateLimiter

try:
    from solders.address_lookup_table_account import AddressLookupTableAccount  # type: ignore[import-untyped]
except ImportError:
    AddressLookupTableAccount = None  # type: ignore[misc,assignment]

QUOTE_URL = "https://api.jup.ag/swap/v1/quote"
SWAP_INSTRUCTIONS_URL = "https://api.jup.ag/swap/v1/swap-instructions"
WSOL_MINT = "So11111111111111111111111111111111111111112"

MAX_RETRIES = 2
RETRY_DELAYS = [1.0, 3.0]

LAMPORTS_PER_SOL = 1_000_000_000

# Confirmation polling
CONFIRM_POLL_INTERVAL = 2.0  # seconds
CONFIRM_TIMEOUT = 60  # seconds
RESEND_INTERVAL = 4.0  # seconds


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


def _parse_instruction(ix_data: dict) -> Instruction:
    """Parse a Jupiter instruction JSON into solders Instruction."""
    program_id = Pubkey.from_string(ix_data["programId"])
    accounts = [
        AccountMeta(
            pubkey=Pubkey.from_string(a["pubkey"]),
            is_signer=a["isSigner"],
            is_writable=a["isWritable"],
        )
        for a in ix_data["accounts"]
    ]
    data = base64.b64decode(ix_data["data"])
    return Instruction(program_id, data, accounts)


class JupiterSwapClient:
    """Executes Jupiter swaps on Solana mainnet.

    Uses swap-instructions endpoint + fresh blockhash for reliable landing.
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
        """Buy token with SOL via Jupiter.

        Pipeline: quote → swap-instructions → build TX → sign → send → confirm.
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

        # Step 2-5: Build TX, sign, send, confirm
        result = await self._execute_swap(quote)

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
        """Sell token for SOL via Jupiter.

        Pipeline: quote → swap-instructions → build TX → sign → send → confirm.
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

        # Step 2-5: Build TX, sign, send, confirm
        result = await self._execute_swap(quote)

        if result.success:
            logger.info(
                f"[SWAP] SELL confirmed: {mint[:12]} tx={result.tx_hash} "
                f"out={result.output_amount} SOL"
            )
        else:
            logger.warning(f"[SWAP] SELL failed: {mint[:12]} error={result.error}")

        return result

    # ─── Core swap pipeline ───────────────────────────────────────────

    async def _execute_swap(self, quote: dict) -> SwapResult:
        """Full swap pipeline: instructions → build TX → sign → send → confirm."""
        price_impact = float(quote.get("priceImpactPct", 0) or 0)

        # Step 2: Get swap instructions
        ix_data = await self._get_swap_instructions(quote)
        if ix_data is None:
            return SwapResult(
                success=False,
                error="Swap instructions fetch failed",
                is_retryable=True,
                price_impact_pct=price_impact,
            )

        # Step 3: Build TX with fresh blockhash
        try:
            tx_b64, tx_hash_str = await self._build_and_sign_tx(ix_data)
        except Exception as e:
            return SwapResult(
                success=False,
                error=f"TX build/sign failed: {e}",
                price_impact_pct=price_impact,
            )

        # Step 4: Send
        sent_hash = await self._send_raw_transaction(tx_b64)
        if sent_hash is None:
            return SwapResult(
                success=False,
                error="sendTransaction RPC failed",
                is_retryable=True,
                price_impact_pct=price_impact,
            )

        # Step 5: Poll confirmation with resend
        confirmed = await self._wait_for_confirmation_with_resend(sent_hash, tx_b64)
        if not confirmed:
            return SwapResult(
                success=False,
                tx_hash=sent_hash,
                error=f"Confirmation timeout ({CONFIRM_TIMEOUT}s)",
                is_retryable=False,
                price_impact_pct=price_impact,
            )

        # Build success result
        in_amount_raw = int(quote.get("inAmount", 0))
        out_amount_raw = int(quote.get("outAmount", 0))

        input_mint = quote.get("inputMint", "")
        if input_mint == WSOL_MINT:
            input_sol = Decimal(str(in_amount_raw)) / Decimal(str(LAMPORTS_PER_SOL))
        else:
            input_sol = Decimal("0")

        return SwapResult(
            success=True,
            tx_hash=sent_hash,
            input_amount=Decimal(str(in_amount_raw)),
            output_amount=Decimal(str(out_amount_raw)),
            price_impact_pct=price_impact,
            fee_sol=input_sol * Decimal("0.0001"),
        )

    # ─── Jupiter API methods ─────────────────────────────────────────

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

    async def _get_swap_instructions(self, quote: dict) -> dict | None:
        """POST /swap/v1/swap-instructions to get individual instructions."""
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
                resp = await self._http.post(SWAP_INSTRUCTIONS_URL, json=payload)

                if resp.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[SWAP] Instructions rate limited, waiting {delay}s")
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code == 400:
                    data = resp.json()
                    error_msg = data.get("error", data.get("message", "Bad request"))
                    logger.warning(f"[SWAP] Instructions 400: {error_msg}")
                    return None

                if resp.status_code >= 500:
                    if attempt < MAX_RETRIES:
                        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                        await asyncio.sleep(delay)
                        continue
                    logger.warning(f"[SWAP] Instructions failed: HTTP {resp.status_code}")
                    return None

                if resp.status_code != 200:
                    logger.warning(f"[SWAP] Instructions unexpected HTTP {resp.status_code}")
                    return None

                data = resp.json()
                if "swapInstruction" not in data:
                    logger.warning("[SWAP] No swapInstruction in response")
                    return None
                return data

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"[SWAP] Instructions failed after retries: {e}")
                    return None

        return None

    # ─── TX building ─────────────────────────────────────────────────

    async def _build_and_sign_tx(self, ix_data: dict) -> tuple[str, str]:
        """Build VersionedTransaction from instructions + fresh blockhash.

        Returns (tx_base64, tx_signature_string).
        """
        # Parse all instructions
        all_ixs: list[Instruction] = []

        for cix in ix_data.get("computeBudgetInstructions", []):
            all_ixs.append(_parse_instruction(cix))

        for six in ix_data.get("setupInstructions", []):
            all_ixs.append(_parse_instruction(six))

        all_ixs.append(_parse_instruction(ix_data["swapInstruction"]))

        cleanup = ix_data.get("cleanupInstruction")
        if cleanup:
            all_ixs.append(_parse_instruction(cleanup))

        # Fetch Address Lookup Tables
        alt_addresses = ix_data.get("addressLookupTableAddresses", [])
        alts = []
        if alt_addresses and AddressLookupTableAccount is not None:
            for alt_addr in alt_addresses:
                alt = await self._fetch_alt(alt_addr)
                if alt:
                    alts.append(alt)

        # Get fresh blockhash from our RPC
        bh_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getLatestBlockhash",
            "params": [{"commitment": "finalized"}],
        }
        resp = await self._rpc_http.post(self._rpc_url, json=bh_payload)
        bh_data = resp.json()["result"]["value"]
        blockhash = Hash.from_string(bh_data["blockhash"])

        # Build MessageV0 with our fresh blockhash
        msg = MessageV0.try_compile(
            payer=self._keypair.pubkey(),
            instructions=all_ixs,
            address_lookup_table_accounts=alts,
            recent_blockhash=blockhash,
        )

        # Sign
        tx = VersionedTransaction(msg, [self._keypair])
        tx_b64 = base64.b64encode(bytes(tx)).decode("ascii")
        tx_sig = str(tx.signatures[0])

        logger.debug(
            f"[SWAP] TX built: {len(all_ixs)} instructions, "
            f"{len(alts)} ALTs, blockhash={str(blockhash)[:16]}..."
        )

        return tx_b64, tx_sig

    async def _fetch_alt(self, alt_key: str) -> "AddressLookupTableAccount | None":
        """Fetch an Address Lookup Table account from RPC."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [alt_key, {"encoding": "base64", "commitment": "confirmed"}],
        }
        try:
            resp = await self._rpc_http.post(self._rpc_url, json=payload)
            result = resp.json().get("result", {}).get("value")
            if not result:
                return None

            raw = base64.b64decode(result["data"][0])
            if len(raw) < 56:
                return None

            # Parse ALT: 56-byte header, then 32-byte addresses
            addresses = []
            for i in range(56, len(raw), 32):
                if i + 32 <= len(raw):
                    addresses.append(Pubkey.from_bytes(raw[i : i + 32]))

            return AddressLookupTableAccount(
                key=Pubkey.from_string(alt_key),
                addresses=addresses,
            )
        except Exception as e:
            logger.warning(f"[SWAP] ALT fetch failed for {alt_key[:12]}: {e}")
            return None

    # ─── RPC methods ─────────────────────────────────────────────────

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
                    "skipPreflight": True,
                    "maxRetries": 5,
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

    async def _wait_for_confirmation_with_resend(
        self,
        tx_hash: str,
        tx_b64: str,
        timeout: int = CONFIRM_TIMEOUT,
    ) -> bool:
        """Poll getSignatureStatuses with periodic re-sends until confirmed.

        Solana best practice: re-send the same signed TX every few seconds
        to increase landing probability. The TX has the same signature so
        duplicate sends are idempotent.
        """
        status_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignatureStatuses",
            "params": [[tx_hash], {"searchTransactionHistory": True}],
        }
        send_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                tx_b64,
                {
                    "encoding": "base64",
                    "skipPreflight": True,
                    "maxRetries": 0,
                },
            ],
        }

        elapsed = 0.0
        last_resend = 0.0
        while elapsed < timeout:
            # Check status
            try:
                resp = await self._rpc_http.post(self._rpc_url, json=status_payload)
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
                pass

            # Periodic re-send (idempotent — same signature)
            if elapsed - last_resend >= RESEND_INTERVAL and elapsed < timeout - 5:
                try:
                    await self._rpc_http.post(self._rpc_url, json=send_payload)
                    last_resend = elapsed
                except Exception:
                    pass  # Best-effort resend

            await asyncio.sleep(CONFIRM_POLL_INTERVAL)
            elapsed += CONFIRM_POLL_INTERVAL

        logger.warning(f"[SWAP] TX {tx_hash[:16]} confirmation timeout after {timeout}s")
        return False

    async def close(self) -> None:
        """Close HTTP clients."""
        await self._http.aclose()
        await self._rpc_http.aclose()

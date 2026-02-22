"""Tests for JupiterSwapClient — quote, buy, sell, error handling, retries.

All HTTP calls are mocked. No real RPC or Jupiter API requests are made.
"""

from __future__ import annotations

import base64
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from solders.keypair import Keypair  # type: ignore[import-untyped]

from src.trading.jupiter_swap import (
    LAMPORTS_PER_SOL,
    QUOTE_URL,
    SWAP_INSTRUCTIONS_URL,
    WSOL_MINT,
    JupiterSwapClient,
    SwapResult,
)


# ── Fixtures ───────────────────────────────────────────────────────────


RPC_URL = "https://api.mainnet-beta.solana.com"


@pytest.fixture
def keypair() -> Keypair:
    return Keypair()


@pytest.fixture
def client(keypair: Keypair) -> JupiterSwapClient:
    """Create a JupiterSwapClient with mocked internals."""
    return JupiterSwapClient(
        api_key="test-key",
        rpc_url=RPC_URL,
        keypair=keypair,
        max_rps=100.0,  # high limit to avoid rate-limit waits in tests
        default_slippage_bps=500,
    )


def _make_quote_response(
    *,
    in_amount: int = 500_000_000,
    out_amount: int = 10_000_000,
    price_impact: float = 0.5,
    input_mint: str = WSOL_MINT,
    output_mint: str = "TokenMint111111111111111111111111111111111",
) -> dict:
    """Build a mock Jupiter quote response."""
    return {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "inAmount": str(in_amount),
        "outAmount": str(out_amount),
        "priceImpactPct": str(price_impact),
        "routePlan": [{"swapInfo": {"label": "Raydium"}}],
    }


def _make_swap_instructions_response() -> dict:
    """Build a mock Jupiter swap-instructions response."""
    return {
        "computeBudgetInstructions": [
            {
                "programId": "ComputeBudget111111111111111111111111111111",
                "accounts": [],
                "data": base64.b64encode(b"\x02\x00\x00\x00").decode(),
            }
        ],
        "setupInstructions": [],
        "swapInstruction": {
            "programId": "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
            "accounts": [
                {
                    "pubkey": "11111111111111111111111111111111",
                    "isSigner": False,
                    "isWritable": False,
                }
            ],
            "data": base64.b64encode(b"\x01\x02\x03").decode(),
        },
        "cleanupInstruction": None,
        "addressLookupTableAddresses": [],
    }


# ── SwapResult dataclass ──────────────────────────────────────────────


class TestSwapResult:
    """Test SwapResult dataclass creation and defaults."""

    def test_create_success_result(self):
        r = SwapResult(
            success=True,
            tx_hash="5abc...xyz",
            input_amount=Decimal("500000000"),
            output_amount=Decimal("10000000"),
            price_impact_pct=0.5,
            fee_sol=Decimal("0.00005"),
        )
        assert r.success is True
        assert r.tx_hash == "5abc...xyz"
        assert r.error is None
        assert r.is_retryable is False

    def test_create_failure_result(self):
        r = SwapResult(
            success=False,
            error="Quote failed",
            is_retryable=True,
        )
        assert r.success is False
        assert r.tx_hash is None
        assert r.input_amount is None
        assert r.output_amount is None
        assert r.error == "Quote failed"
        assert r.is_retryable is True

    def test_default_values(self):
        r = SwapResult(success=False)
        assert r.tx_hash is None
        assert r.input_amount is None
        assert r.output_amount is None
        assert r.price_impact_pct is None
        assert r.fee_sol is None
        assert r.error is None
        assert r.is_retryable is False


# ── _get_quote ─────────────────────────────────────────────────────────


class TestGetQuote:
    """Test internal quote request logic."""

    async def test_quote_success(self, client: JupiterSwapClient):
        """Successful quote returns parsed JSON dict."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = _make_quote_response()

        client._http = AsyncMock(spec=httpx.AsyncClient)
        client._http.get = AsyncMock(return_value=mock_response)

        result = await client._get_quote(
            input_mint=WSOL_MINT,
            output_mint="TokenMint111111111111111111111111111111111",
            amount=500_000_000,
            slippage_bps=500,
        )

        assert result is not None
        assert result["inputMint"] == WSOL_MINT
        client._http.get.assert_awaited_once()

    async def test_quote_400_returns_none(self, client: JupiterSwapClient):
        """Bad request (400) should return None without retry."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": "No route found"}

        client._http = AsyncMock(spec=httpx.AsyncClient)
        client._http.get = AsyncMock(return_value=mock_response)

        result = await client._get_quote(WSOL_MINT, "SomeMint", 100, 500)
        assert result is None

    async def test_quote_500_retries_then_fails(self, client: JupiterSwapClient):
        """Server error should retry MAX_RETRIES times, then return None."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 500

        client._http = AsyncMock(spec=httpx.AsyncClient)
        client._http.get = AsyncMock(return_value=mock_response)

        with patch("src.trading.jupiter_swap.asyncio.sleep", new_callable=AsyncMock):
            result = await client._get_quote(WSOL_MINT, "SomeMint", 100, 500)

        assert result is None
        # 1 initial + MAX_RETRIES(2) = 3 total calls
        assert client._http.get.await_count == 3

    async def test_quote_429_retries(self, client: JupiterSwapClient):
        """Rate limit (429) should trigger retry with backoff."""
        mock_429 = MagicMock(spec=httpx.Response)
        mock_429.status_code = 429

        mock_200 = MagicMock(spec=httpx.Response)
        mock_200.status_code = 200
        mock_200.json.return_value = _make_quote_response()

        client._http = AsyncMock(spec=httpx.AsyncClient)
        client._http.get = AsyncMock(side_effect=[mock_429, mock_200])

        with patch("src.trading.jupiter_swap.asyncio.sleep", new_callable=AsyncMock):
            result = await client._get_quote(WSOL_MINT, "SomeMint", 100, 500)

        assert result is not None
        assert client._http.get.await_count == 2

    async def test_quote_timeout_retries(self, client: JupiterSwapClient):
        """httpx.TimeoutException should retry then fail."""
        client._http = AsyncMock(spec=httpx.AsyncClient)
        client._http.get = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )

        with patch("src.trading.jupiter_swap.asyncio.sleep", new_callable=AsyncMock):
            result = await client._get_quote(WSOL_MINT, "SomeMint", 100, 500)

        assert result is None
        assert client._http.get.await_count == 3  # 1 + 2 retries


# ── buy_token ──────────────────────────────────────────────────────────


class TestBuyToken:
    """Test the full buy pipeline with mocked internal methods."""

    async def test_buy_success(self, client: JupiterSwapClient):
        """Successful buy returns SwapResult with success=True."""
        quote = _make_quote_response(
            in_amount=500_000_000,
            out_amount=10_000_000,
            price_impact=0.5,
        )
        swap_result = SwapResult(
            success=True,
            tx_hash="txhash123",
            input_amount=Decimal("500000000"),
            output_amount=Decimal("10000000"),
            price_impact_pct=0.5,
            fee_sol=Decimal("0.00005"),
        )

        client._get_quote = AsyncMock(return_value=quote)
        client._execute_swap = AsyncMock(return_value=swap_result)

        result = await client.buy_token("TokenMint123", 500_000_000)

        assert result.success is True
        assert result.tx_hash == "txhash123"
        client._get_quote.assert_awaited_once()
        client._execute_swap.assert_awaited_once_with(quote)

    async def test_buy_quote_fails(self, client: JupiterSwapClient):
        """Failed quote returns retryable error."""
        client._get_quote = AsyncMock(return_value=None)

        result = await client.buy_token("TokenMint123", 500_000_000)

        assert result.success is False
        assert "Quote failed" in result.error
        assert result.is_retryable is True

    async def test_buy_price_impact_too_high(self, client: JupiterSwapClient):
        """Price impact >10% should reject the buy."""
        quote = _make_quote_response(price_impact=15.0)
        client._get_quote = AsyncMock(return_value=quote)

        result = await client.buy_token("TokenMint123", 500_000_000)

        assert result.success is False
        assert "Price impact too high" in result.error
        assert result.price_impact_pct == 15.0

    async def test_buy_price_impact_exactly_10_allowed(self, client: JupiterSwapClient):
        """Price impact at exactly 10.0% should NOT trigger rejection (> not >=)."""
        quote = _make_quote_response(price_impact=10.0)
        swap_result = SwapResult(success=True, tx_hash="tx123", price_impact_pct=10.0)
        client._get_quote = AsyncMock(return_value=quote)
        client._execute_swap = AsyncMock(return_value=swap_result)

        result = await client.buy_token("TokenMint123", 500_000_000)
        assert result.success is True

    async def test_buy_execute_swap_fails(self, client: JupiterSwapClient):
        """Failed execute_swap returns error from pipeline."""
        quote = _make_quote_response()
        client._get_quote = AsyncMock(return_value=quote)
        client._execute_swap = AsyncMock(
            return_value=SwapResult(
                success=False,
                error="Swap instructions fetch failed",
                is_retryable=True,
            )
        )

        result = await client.buy_token("TokenMint123", 500_000_000)

        assert result.success is False
        assert "instructions" in result.error.lower()
        assert result.is_retryable is True

    async def test_buy_custom_slippage(self, client: JupiterSwapClient):
        """Custom slippage_bps overrides the default."""
        quote = _make_quote_response()
        swap_result = SwapResult(success=True, tx_hash="tx", price_impact_pct=0.5)
        client._get_quote = AsyncMock(return_value=quote)
        client._execute_swap = AsyncMock(return_value=swap_result)

        await client.buy_token("TokenMint123", 500_000_000, slippage_bps=200)

        # Verify the quote was called with custom slippage
        call_kwargs = client._get_quote.call_args
        assert call_kwargs.kwargs.get("slippage_bps") == 200 or call_kwargs[0][3] == 200


# ── sell_token ─────────────────────────────────────────────────────────


class TestSellToken:
    """Test the full sell pipeline with mocked internal methods."""

    async def test_sell_success(self, client: JupiterSwapClient):
        """Successful sell returns SwapResult with output in SOL."""
        quote = _make_quote_response(
            input_mint="TokenMint111111111111111111111111111111111",
            output_mint=WSOL_MINT,
            in_amount=10_000_000,
            out_amount=500_000_000,
        )
        swap_result = SwapResult(
            success=True,
            tx_hash="selltx123",
            input_amount=Decimal("10000000"),
            output_amount=Decimal("500000000"),
            price_impact_pct=0.3,
        )

        client._get_quote = AsyncMock(return_value=quote)
        client._execute_swap = AsyncMock(return_value=swap_result)

        result = await client.sell_token("TokenMint123", 10_000_000)

        assert result.success is True
        assert result.tx_hash == "selltx123"

    async def test_sell_quote_fails(self, client: JupiterSwapClient):
        """Failed sell quote returns retryable error."""
        client._get_quote = AsyncMock(return_value=None)

        result = await client.sell_token("TokenMint123", 10_000_000)

        assert result.success is False
        assert "Sell quote failed" in result.error
        assert result.is_retryable is True

    async def test_sell_execute_swap_fails(self, client: JupiterSwapClient):
        """Failed sell execute returns retryable error."""
        quote = _make_quote_response()
        client._get_quote = AsyncMock(return_value=quote)
        client._execute_swap = AsyncMock(
            return_value=SwapResult(
                success=False,
                error="Swap instructions fetch failed",
                is_retryable=True,
            )
        )

        result = await client.sell_token("TokenMint123", 10_000_000)

        assert result.success is False
        assert result.is_retryable is True

    async def test_sell_no_price_impact_check(self, client: JupiterSwapClient):
        """Sell does NOT check price impact (unlike buy)."""
        quote = _make_quote_response(price_impact=25.0)  # high but should pass
        swap_result = SwapResult(success=True, tx_hash="selltx")
        client._get_quote = AsyncMock(return_value=quote)
        client._execute_swap = AsyncMock(return_value=swap_result)

        result = await client.sell_token("TokenMint123", 10_000_000)
        assert result.success is True  # sell doesn't block on price impact


# ── _get_swap_instructions ───────────────────────────────────────────


class TestGetSwapInstructions:
    """Test swap instructions fetching."""

    async def test_instructions_success(self, client: JupiterSwapClient):
        """Successful fetch returns instructions dict."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = _make_swap_instructions_response()

        client._http = AsyncMock(spec=httpx.AsyncClient)
        client._http.post = AsyncMock(return_value=mock_response)

        quote = _make_quote_response()
        result = await client._get_swap_instructions(quote)

        assert result is not None
        assert "swapInstruction" in result

    async def test_instructions_missing_swap(self, client: JupiterSwapClient):
        """Response without swapInstruction returns None."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"computeBudgetInstructions": []}

        client._http = AsyncMock(spec=httpx.AsyncClient)
        client._http.post = AsyncMock(return_value=mock_response)

        result = await client._get_swap_instructions(_make_quote_response())
        assert result is None

    async def test_instructions_400_returns_none(self, client: JupiterSwapClient):
        """400 error returns None without retry."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": "Invalid quote"}

        client._http = AsyncMock(spec=httpx.AsyncClient)
        client._http.post = AsyncMock(return_value=mock_response)

        result = await client._get_swap_instructions(_make_quote_response())
        assert result is None

    async def test_instructions_500_retries(self, client: JupiterSwapClient):
        """Server error retries then fails."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 500

        client._http = AsyncMock(spec=httpx.AsyncClient)
        client._http.post = AsyncMock(return_value=mock_response)

        with patch("src.trading.jupiter_swap.asyncio.sleep", new_callable=AsyncMock):
            result = await client._get_swap_instructions(_make_quote_response())

        assert result is None
        assert client._http.post.await_count == 3  # 1 + 2 retries

    async def test_instructions_429_retries(self, client: JupiterSwapClient):
        """Rate limit (429) should trigger retry."""
        mock_429 = MagicMock(spec=httpx.Response)
        mock_429.status_code = 429

        mock_200 = MagicMock(spec=httpx.Response)
        mock_200.status_code = 200
        mock_200.json.return_value = _make_swap_instructions_response()

        client._http = AsyncMock(spec=httpx.AsyncClient)
        client._http.post = AsyncMock(side_effect=[mock_429, mock_200])

        with patch("src.trading.jupiter_swap.asyncio.sleep", new_callable=AsyncMock):
            result = await client._get_swap_instructions(_make_quote_response())

        assert result is not None
        assert client._http.post.await_count == 2


# ── _send_raw_transaction ──────────────────────────────────────────────


class TestSendRawTransaction:
    """Test RPC sendTransaction."""

    async def test_send_success(self, client: JupiterSwapClient):
        """Successful sendTransaction returns tx hash."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": "5abc123txhash",
        }

        client._rpc_http = AsyncMock(spec=httpx.AsyncClient)
        client._rpc_http.post = AsyncMock(return_value=mock_response)

        result = await client._send_raw_transaction("base64tx")
        assert result == "5abc123txhash"

    async def test_send_rpc_error_blockhash(self, client: JupiterSwapClient):
        """Blockhash not found should NOT retry (returns None)."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32002, "message": "Blockhash not found"},
        }

        client._rpc_http = AsyncMock(spec=httpx.AsyncClient)
        client._rpc_http.post = AsyncMock(return_value=mock_response)

        with patch("src.trading.jupiter_swap.asyncio.sleep", new_callable=AsyncMock):
            result = await client._send_raw_transaction("base64tx")

        assert result is None
        # Should NOT retry on blockhash error
        assert client._rpc_http.post.await_count == 1

    async def test_send_rpc_error_insufficient(self, client: JupiterSwapClient):
        """Insufficient funds error should NOT retry."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -1, "message": "Insufficient lamports"},
        }

        client._rpc_http = AsyncMock(spec=httpx.AsyncClient)
        client._rpc_http.post = AsyncMock(return_value=mock_response)

        with patch("src.trading.jupiter_swap.asyncio.sleep", new_callable=AsyncMock):
            result = await client._send_raw_transaction("base64tx")

        assert result is None
        assert client._rpc_http.post.await_count == 1

    async def test_send_retries_on_http_error(self, client: JupiterSwapClient):
        """HTTP errors should trigger retries."""
        mock_500 = MagicMock(spec=httpx.Response)
        mock_500.status_code = 500

        mock_200 = MagicMock(spec=httpx.Response)
        mock_200.status_code = 200
        mock_200.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": "tx123"}

        client._rpc_http = AsyncMock(spec=httpx.AsyncClient)
        client._rpc_http.post = AsyncMock(side_effect=[mock_500, mock_200])

        with patch("src.trading.jupiter_swap.asyncio.sleep", new_callable=AsyncMock):
            result = await client._send_raw_transaction("base64tx")

        assert result == "tx123"
        assert client._rpc_http.post.await_count == 2

    async def test_send_timeout_retries(self, client: JupiterSwapClient):
        """Timeout should retry then fail."""
        client._rpc_http = AsyncMock(spec=httpx.AsyncClient)
        client._rpc_http.post = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )

        with patch("src.trading.jupiter_swap.asyncio.sleep", new_callable=AsyncMock):
            result = await client._send_raw_transaction("base64tx")

        assert result is None
        assert client._rpc_http.post.await_count == 3


# ── _wait_for_confirmation_with_resend ───────────────────────────────


class TestWaitForConfirmationWithResend:
    """Test transaction confirmation polling with periodic re-sends."""

    async def test_confirmed_immediately(self, client: JupiterSwapClient):
        """Transaction confirmed on first poll returns True."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "value": [
                    {
                        "confirmationStatus": "confirmed",
                        "err": None,
                    }
                ]
            },
        }

        client._rpc_http = AsyncMock(spec=httpx.AsyncClient)
        client._rpc_http.post = AsyncMock(return_value=mock_response)

        result = await client._wait_for_confirmation_with_resend(
            "txhash123", "base64tx", timeout=5
        )
        assert result is True

    async def test_finalized_accepted(self, client: JupiterSwapClient):
        """'finalized' status counts as confirmed."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "value": [
                    {
                        "confirmationStatus": "finalized",
                        "err": None,
                    }
                ]
            },
        }

        client._rpc_http = AsyncMock(spec=httpx.AsyncClient)
        client._rpc_http.post = AsyncMock(return_value=mock_response)

        result = await client._wait_for_confirmation_with_resend(
            "txhash123", "base64tx", timeout=5
        )
        assert result is True

    async def test_on_chain_error_returns_false(self, client: JupiterSwapClient):
        """Transaction with on-chain error returns False."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "value": [
                    {
                        "confirmationStatus": "confirmed",
                        "err": {"InstructionError": [0, "Custom"]},
                    }
                ]
            },
        }

        client._rpc_http = AsyncMock(spec=httpx.AsyncClient)
        client._rpc_http.post = AsyncMock(return_value=mock_response)

        result = await client._wait_for_confirmation_with_resend(
            "txhash123", "base64tx", timeout=5
        )
        assert result is False

    async def test_timeout_returns_false(self, client: JupiterSwapClient):
        """Polling until timeout returns False."""
        # Return null status (not confirmed) forever
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"value": [None]},
        }

        client._rpc_http = AsyncMock(spec=httpx.AsyncClient)
        client._rpc_http.post = AsyncMock(return_value=mock_response)

        with patch("src.trading.jupiter_swap.asyncio.sleep", new_callable=AsyncMock):
            result = await client._wait_for_confirmation_with_resend(
                "txhash123", "base64tx", timeout=4
            )

        assert result is False

    async def test_confirms_after_pending_polls(self, client: JupiterSwapClient):
        """First poll returns pending, second returns confirmed."""
        mock_pending = MagicMock(spec=httpx.Response)
        mock_pending.status_code = 200
        mock_pending.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"value": [None]},
        }

        mock_confirmed = MagicMock(spec=httpx.Response)
        mock_confirmed.status_code = 200
        mock_confirmed.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "value": [{"confirmationStatus": "confirmed", "err": None}]
            },
        }

        client._rpc_http = AsyncMock(spec=httpx.AsyncClient)
        # pending → confirmed. May also include resend calls between them,
        # so we allow extra calls by returning confirmed for all subsequent.
        client._rpc_http.post = AsyncMock(
            side_effect=[mock_pending, mock_confirmed, mock_confirmed]
        )

        with patch("src.trading.jupiter_swap.asyncio.sleep", new_callable=AsyncMock):
            result = await client._wait_for_confirmation_with_resend(
                "txhash123", "base64tx", timeout=10
            )

        assert result is True


# ── _execute_swap ─────────────────────────────────────────────────────


class TestExecuteSwap:
    """Test the full swap execution pipeline."""

    async def test_execute_swap_success(self, client: JupiterSwapClient):
        """Full pipeline returns SwapResult with success."""
        quote = _make_quote_response()

        client._get_swap_instructions = AsyncMock(
            return_value=_make_swap_instructions_response()
        )
        client._build_and_sign_tx = AsyncMock(return_value=("base64tx", "sig123"))
        client._send_raw_transaction = AsyncMock(return_value="txhash456")
        client._wait_for_confirmation_with_resend = AsyncMock(return_value=True)

        result = await client._execute_swap(quote)

        assert result.success is True
        assert result.tx_hash == "txhash456"
        client._get_swap_instructions.assert_awaited_once_with(quote)
        client._build_and_sign_tx.assert_awaited_once()
        client._send_raw_transaction.assert_awaited_once_with("base64tx")
        client._wait_for_confirmation_with_resend.assert_awaited_once_with(
            "txhash456", "base64tx"
        )

    async def test_execute_swap_instructions_fail(self, client: JupiterSwapClient):
        """Instructions fetch failure returns retryable error."""
        quote = _make_quote_response()
        client._get_swap_instructions = AsyncMock(return_value=None)

        result = await client._execute_swap(quote)

        assert result.success is False
        assert "instructions" in result.error.lower()
        assert result.is_retryable is True

    async def test_execute_swap_build_tx_fail(self, client: JupiterSwapClient):
        """TX build failure returns error."""
        quote = _make_quote_response()
        client._get_swap_instructions = AsyncMock(
            return_value=_make_swap_instructions_response()
        )
        client._build_and_sign_tx = AsyncMock(
            side_effect=Exception("Failed to compile message")
        )

        result = await client._execute_swap(quote)

        assert result.success is False
        assert "build/sign failed" in result.error.lower()

    async def test_execute_swap_send_fail(self, client: JupiterSwapClient):
        """Send failure returns retryable error."""
        quote = _make_quote_response()
        client._get_swap_instructions = AsyncMock(
            return_value=_make_swap_instructions_response()
        )
        client._build_and_sign_tx = AsyncMock(return_value=("base64tx", "sig123"))
        client._send_raw_transaction = AsyncMock(return_value=None)

        result = await client._execute_swap(quote)

        assert result.success is False
        assert "rpc failed" in result.error.lower()
        assert result.is_retryable is True

    async def test_execute_swap_confirmation_timeout(self, client: JupiterSwapClient):
        """Confirmation timeout returns non-retryable failure with tx_hash."""
        quote = _make_quote_response()
        client._get_swap_instructions = AsyncMock(
            return_value=_make_swap_instructions_response()
        )
        client._build_and_sign_tx = AsyncMock(return_value=("base64tx", "sig123"))
        client._send_raw_transaction = AsyncMock(return_value="txhash789")
        client._wait_for_confirmation_with_resend = AsyncMock(return_value=False)

        result = await client._execute_swap(quote)

        assert result.success is False
        assert result.tx_hash == "txhash789"
        assert "timeout" in result.error.lower()
        assert result.is_retryable is False


# ── close ──────────────────────────────────────────────────────────────


class TestJupiterClientClose:
    """Test resource cleanup."""

    async def test_close_both_http_clients(self, client: JupiterSwapClient):
        """close() should close both _http and _rpc_http."""
        client._http = AsyncMock(spec=httpx.AsyncClient)
        client._http.aclose = AsyncMock()
        client._rpc_http = AsyncMock(spec=httpx.AsyncClient)
        client._rpc_http.aclose = AsyncMock()

        await client.close()

        client._http.aclose.assert_awaited_once()
        client._rpc_http.aclose.assert_awaited_once()

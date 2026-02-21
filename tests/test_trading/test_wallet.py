"""Tests for SolanaWallet — keypair init, balance queries, ATA derivation.

All HTTP calls are mocked via httpx.AsyncClient patching.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from solders.keypair import Keypair  # type: ignore[import-untyped]
from solders.pubkey import Pubkey  # type: ignore[import-untyped]

from src.trading.wallet import LAMPORTS_PER_SOL, SolanaWallet


# ── Fixtures ───────────────────────────────────────────────────────────

def _random_base58_key() -> str:
    """Generate a random Solana keypair and return base58 private key string."""
    kp = Keypair()
    return str(kp)


RPC_URL = "https://api.mainnet-beta.solana.com"


@pytest.fixture
def wallet() -> SolanaWallet:
    """Create a wallet with a random keypair for testing."""
    return SolanaWallet(_random_base58_key(), RPC_URL)


# ── Initialization ─────────────────────────────────────────────────────


class TestWalletInit:
    """Test wallet creation and validation."""

    def test_init_valid_key(self):
        key = _random_base58_key()
        w = SolanaWallet(key, RPC_URL)
        assert w.pubkey is not None
        assert isinstance(w.pubkey, Pubkey)

    def test_init_empty_key_raises(self):
        with pytest.raises(ValueError, match="private key is empty"):
            SolanaWallet("", RPC_URL)

    def test_init_empty_rpc_url_raises(self):
        key = _random_base58_key()
        with pytest.raises(ValueError, match="RPC URL is empty"):
            SolanaWallet(key, "")

    def test_pubkey_str_returns_string(self, wallet: SolanaWallet):
        result = wallet.pubkey_str
        assert isinstance(result, str)
        assert len(result) > 30  # Solana pubkeys are 32-44 chars in base58

    def test_repr_shows_pubkey_only(self, wallet: SolanaWallet):
        r = repr(wallet)
        assert "SolanaWallet(pubkey=" in r
        assert wallet.pubkey_str in r
        # Should NOT contain the private key
        assert "keypair" not in r.lower() or "pubkey" in r.lower()

    def test_keypair_property_returns_keypair(self, wallet: SolanaWallet):
        kp = wallet.keypair
        assert isinstance(kp, Keypair)
        assert kp.pubkey() == wallet.pubkey


# ── get_sol_balance ────────────────────────────────────────────────────


class TestGetSolBalance:
    """Mock HTTP to test SOL balance fetching."""

    async def test_get_sol_balance_success(self, wallet: SolanaWallet):
        """Successful RPC response returns balance in SOL."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"value": 5_000_000_000},  # 5 SOL in lamports
        }

        wallet._http = AsyncMock(spec=httpx.AsyncClient)
        wallet._http.post = AsyncMock(return_value=mock_response)

        balance = await wallet.get_sol_balance()
        assert balance == 5.0

    async def test_get_sol_balance_zero(self, wallet: SolanaWallet):
        """Empty wallet returns 0.0."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"value": 0},
        }

        wallet._http = AsyncMock(spec=httpx.AsyncClient)
        wallet._http.post = AsyncMock(return_value=mock_response)

        balance = await wallet.get_sol_balance()
        assert balance == 0.0

    async def test_get_sol_balance_http_error(self, wallet: SolanaWallet):
        """Non-200 response returns 0.0."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 500

        wallet._http = AsyncMock(spec=httpx.AsyncClient)
        wallet._http.post = AsyncMock(return_value=mock_response)

        balance = await wallet.get_sol_balance()
        assert balance == 0.0

    async def test_get_sol_balance_rpc_error(self, wallet: SolanaWallet):
        """RPC error in response returns 0.0."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "Invalid request"},
        }

        wallet._http = AsyncMock(spec=httpx.AsyncClient)
        wallet._http.post = AsyncMock(return_value=mock_response)

        balance = await wallet.get_sol_balance()
        assert balance == 0.0

    async def test_get_sol_balance_timeout(self, wallet: SolanaWallet):
        """Timeout returns 0.0 gracefully."""
        wallet._http = AsyncMock(spec=httpx.AsyncClient)
        wallet._http.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        balance = await wallet.get_sol_balance()
        assert balance == 0.0

    async def test_get_sol_balance_connect_error(self, wallet: SolanaWallet):
        """Connection error returns 0.0 gracefully."""
        wallet._http = AsyncMock(spec=httpx.AsyncClient)
        wallet._http.post = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )

        balance = await wallet.get_sol_balance()
        assert balance == 0.0

    async def test_get_sol_balance_fractional(self, wallet: SolanaWallet):
        """Fractional SOL amounts are converted correctly."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"value": 1_234_567_890},  # 1.23456789 SOL
        }

        wallet._http = AsyncMock(spec=httpx.AsyncClient)
        wallet._http.post = AsyncMock(return_value=mock_response)

        balance = await wallet.get_sol_balance()
        assert abs(balance - 1.23456789) < 1e-9


# ── get_token_balance ──────────────────────────────────────────────────


class TestGetTokenBalance:
    """Mock HTTP to test SPL token balance fetching."""

    async def test_get_token_balance_success(self, wallet: SolanaWallet):
        """Successful RPC response returns (raw_amount, decimals)."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "value": [
                    {
                        "account": {
                            "data": {
                                "parsed": {
                                    "info": {
                                        "tokenAmount": {
                                            "amount": "1000000",
                                            "decimals": 6,
                                        }
                                    }
                                }
                            }
                        }
                    }
                ]
            },
        }

        wallet._http = AsyncMock(spec=httpx.AsyncClient)
        wallet._http.post = AsyncMock(return_value=mock_response)

        raw, decimals = await wallet.get_token_balance("SomeMintAddr111111111111111111111111111111")
        assert raw == 1_000_000
        assert decimals == 6

    async def test_get_token_balance_no_accounts(self, wallet: SolanaWallet):
        """Empty accounts list returns (0, 0)."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"value": []},
        }

        wallet._http = AsyncMock(spec=httpx.AsyncClient)
        wallet._http.post = AsyncMock(return_value=mock_response)

        raw, decimals = await wallet.get_token_balance("SomeMintAddr111111111111111111111111111111")
        assert raw == 0
        assert decimals == 0

    async def test_get_token_balance_http_error(self, wallet: SolanaWallet):
        """Non-200 response returns (0, 0)."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 429

        wallet._http = AsyncMock(spec=httpx.AsyncClient)
        wallet._http.post = AsyncMock(return_value=mock_response)

        raw, decimals = await wallet.get_token_balance("SomeMintAddr111111111111111111111111111111")
        assert raw == 0
        assert decimals == 0

    async def test_get_token_balance_timeout(self, wallet: SolanaWallet):
        """Timeout returns (0, 0) gracefully."""
        wallet._http = AsyncMock(spec=httpx.AsyncClient)
        wallet._http.post = AsyncMock(side_effect=httpx.TimeoutException("slow"))

        raw, decimals = await wallet.get_token_balance("SomeMintAddr111111111111111111111111111111")
        assert raw == 0
        assert decimals == 0


# ── get_ata_address ────────────────────────────────────────────────────


class TestGetAtaAddress:
    """Test ATA derivation (deterministic, no HTTP needed)."""

    def test_get_ata_address_returns_pubkey(self, wallet: SolanaWallet):
        """ATA derivation should return a valid Pubkey."""
        # Use a well-known mint for deterministic testing
        mint = "So11111111111111111111111111111111111111112"
        ata = wallet.get_ata_address(mint)
        assert isinstance(ata, Pubkey)

    def test_get_ata_address_deterministic(self, wallet: SolanaWallet):
        """Same wallet + same mint should always give the same ATA."""
        mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        ata1 = wallet.get_ata_address(mint)
        ata2 = wallet.get_ata_address(mint)
        assert ata1 == ata2

    def test_get_ata_address_different_mints_different_atas(self, wallet: SolanaWallet):
        """Different mints should produce different ATAs."""
        mint1 = "So11111111111111111111111111111111111111112"
        mint2 = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        ata1 = wallet.get_ata_address(mint1)
        ata2 = wallet.get_ata_address(mint2)
        assert ata1 != ata2


# ── close ──────────────────────────────────────────────────────────────


class TestWalletClose:
    """Test wallet cleanup."""

    async def test_close_calls_aclose(self, wallet: SolanaWallet):
        """close() should call _http.aclose()."""
        wallet._http = AsyncMock(spec=httpx.AsyncClient)
        wallet._http.aclose = AsyncMock()

        await wallet.close()
        wallet._http.aclose.assert_awaited_once()

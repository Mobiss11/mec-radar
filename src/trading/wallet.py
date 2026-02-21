"""Solana wallet management — keypair loading, balance checks, ATA derivation.

Private key is loaded ONCE at startup and never logged or exposed.
Only the public key is shown in logs and __repr__.
"""

from __future__ import annotations

import httpx
from loguru import logger
from solders.keypair import Keypair  # type: ignore[import-untyped]
from solders.pubkey import Pubkey  # type: ignore[import-untyped]

# SPL Token constants
TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string(
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
)
WSOL_MINT = Pubkey.from_string("So11111111111111111111111111111111111111112")

LAMPORTS_PER_SOL = 1_000_000_000


class SolanaWallet:
    """Manages a Solana keypair and on-chain balance queries.

    Security: private key is only accessible via .keypair property.
    __repr__ and logging show only the public key.
    """

    def __init__(self, private_key_base58: str, rpc_url: str) -> None:
        if not private_key_base58:
            raise ValueError("Wallet private key is empty")
        if not rpc_url:
            raise ValueError("RPC URL is empty")

        self._keypair = Keypair.from_base58_string(private_key_base58)
        self._rpc_url = rpc_url
        self._http = httpx.AsyncClient(timeout=10.0)
        logger.info(f"[WALLET] Loaded wallet: {self.pubkey_str}")

    def __repr__(self) -> str:
        return f"SolanaWallet(pubkey={self.pubkey_str})"

    @property
    def pubkey(self) -> Pubkey:
        return self._keypair.pubkey()

    @property
    def pubkey_str(self) -> str:
        return str(self._keypair.pubkey())

    @property
    def keypair(self) -> Keypair:
        return self._keypair

    async def get_sol_balance(self) -> float:
        """Fetch SOL balance in SOL (not lamports). Returns 0.0 on error."""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBalance",
                "params": [self.pubkey_str],
            }
            resp = await self._http.post(self._rpc_url, json=payload)
            if resp.status_code != 200:
                logger.warning(f"[WALLET] getBalance HTTP {resp.status_code}")
                return 0.0

            data = resp.json()
            if "error" in data:
                logger.warning(f"[WALLET] getBalance error: {data['error']}")
                return 0.0

            lamports = data.get("result", {}).get("value", 0)
            return lamports / LAMPORTS_PER_SOL

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning(f"[WALLET] getBalance failed: {e}")
            return 0.0

    async def get_token_balance(self, mint: str) -> tuple[int, int]:
        """Get SPL token balance for a mint.

        Returns (raw_amount, decimals). Returns (0, 0) on error or if no account.
        """
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    self.pubkey_str,
                    {"mint": mint},
                    {"encoding": "jsonParsed"},
                ],
            }
            resp = await self._http.post(self._rpc_url, json=payload)
            if resp.status_code != 200:
                return 0, 0

            data = resp.json()
            accounts = data.get("result", {}).get("value", [])
            if not accounts:
                return 0, 0

            # Take first account (usually only one ATA per mint)
            parsed = accounts[0]["account"]["data"]["parsed"]["info"]["tokenAmount"]
            raw_amount = int(parsed.get("amount", "0"))
            decimals = int(parsed.get("decimals", 0))
            return raw_amount, decimals

        except (httpx.TimeoutException, httpx.ConnectError, KeyError, IndexError) as e:
            logger.warning(f"[WALLET] getTokenBalance failed for {mint[:12]}: {e}")
            return 0, 0

    def get_ata_address(self, mint_str: str) -> Pubkey:
        """Derive Associated Token Account address for a mint."""
        mint = Pubkey.from_string(mint_str)
        ata, _bump = Pubkey.find_program_address(
            [bytes(self.pubkey), bytes(TOKEN_PROGRAM_ID), bytes(mint)],
            ASSOCIATED_TOKEN_PROGRAM_ID,
        )
        return ata

    async def close(self) -> None:
        """Close HTTP client."""
        await self._http.aclose()

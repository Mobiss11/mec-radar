"""Pydantic models for Helius Enhanced Transaction API responses."""

from decimal import Decimal

from pydantic import BaseModel


class HeliusTokenTransfer(BaseModel):
    """Token transfer within a transaction."""

    from_user_account: str = ""
    to_user_account: str = ""
    from_token_account: str = ""
    to_token_account: str = ""
    token_amount: Decimal = Decimal("0")
    mint: str = ""
    token_standard: str = ""


class HeliusNativeTransfer(BaseModel):
    """SOL native transfer within a transaction."""

    from_user_account: str = ""
    to_user_account: str = ""
    amount: int = 0  # lamports


class HeliusTransaction(BaseModel):
    """Enhanced parsed transaction from Helius."""

    signature: str
    type: str = ""  # "TRANSFER", "SWAP", "ADD_LIQUIDITY", etc.
    source: str = ""  # "RAYDIUM", "ORCA", "JUPITER", etc.
    fee: int = 0  # lamports
    fee_payer: str = ""
    timestamp: int = 0  # unix
    description: str = ""
    token_transfers: list[HeliusTokenTransfer] = []
    native_transfers: list[HeliusNativeTransfer] = []
    transaction_error: str | dict | None = None  # non-None means failed (Helius returns dict or str)


class HeliusSignature(BaseModel):
    """Transaction signature metadata."""

    signature: str
    slot: int = 0
    timestamp: int = 0
    err: dict | None = None  # non-None means failed

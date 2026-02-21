"""Pump.fun instruction decoder for raw Solana transactions.

Decodes create/buy/sell instructions from Pump.fun program
to extract token info from gRPC-streamed transactions.
"""

import struct

import base58
from loguru import logger


# Pump.fun program ID
PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# 8-byte Anchor discriminators for pump.fun instructions
DISCRIMINATOR_CREATE = struct.pack("<Q", 8576854823835016728)
DISCRIMINATOR_BUY = struct.pack("<Q", 16927863322537952870)
DISCRIMINATOR_SELL = struct.pack("<Q", 12502976635542562355)

# Raydium migration program
RAYDIUM_MIGRATION_PROGRAM = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"


def decode_create_instruction(
    ix_data: bytes,
    account_keys: list[bytes],
    ix_accounts: list[int],
) -> dict | None:
    """Decode pump.fun create (token launch) instruction.

    Returns dict with: name, symbol, uri, mint, creator, bonding_curve_key
    or None on decode failure.
    """
    try:
        offset = 8  # skip discriminator

        def read_string() -> str:
            nonlocal offset
            length = struct.unpack_from("<I", ix_data, offset)[0]
            offset += 4
            value = ix_data[offset : offset + length].decode("utf-8", errors="replace")
            offset += length
            return value

        def get_account(index: int) -> str:
            if index >= len(ix_accounts):
                return ""
            account_index = ix_accounts[index]
            if account_index >= len(account_keys):
                return ""
            return base58.b58encode(bytes(account_keys[account_index])).decode()

        name = read_string()
        symbol = read_string()
        uri = read_string()

        return {
            "name": name,
            "symbol": symbol,
            "uri": uri,
            "mint": get_account(0),
            "creator": get_account(7),
            "bonding_curve_key": get_account(2),
        }
    except Exception as e:
        logger.debug(f"[GRPC] Failed to decode create instruction: {e}")
        return None


def decode_buy_instruction(
    ix_data: bytes,
    account_keys: list[bytes],
    ix_accounts: list[int],
) -> dict | None:
    """Decode pump.fun buy instruction.

    Returns dict with: mint, trader, token_amount, max_sol_cost
    """
    try:
        offset = 8  # skip discriminator
        token_amount = struct.unpack_from("<Q", ix_data, offset)[0]
        offset += 8
        max_sol_cost = struct.unpack_from("<Q", ix_data, offset)[0]

        def get_account(index: int) -> str:
            if index >= len(ix_accounts):
                return ""
            account_index = ix_accounts[index]
            if account_index >= len(account_keys):
                return ""
            return base58.b58encode(bytes(account_keys[account_index])).decode()

        return {
            "mint": get_account(2),
            "trader": get_account(6),
            "token_amount": token_amount,
            "max_sol_cost": max_sol_cost,
            "bonding_curve_key": get_account(3),
        }
    except Exception as e:
        logger.debug(f"[GRPC] Failed to decode buy instruction: {e}")
        return None


def decode_sell_instruction(
    ix_data: bytes,
    account_keys: list[bytes],
    ix_accounts: list[int],
) -> dict | None:
    """Decode pump.fun sell instruction.

    Returns dict with: mint, trader, token_amount, min_sol_output
    """
    try:
        offset = 8  # skip discriminator
        token_amount = struct.unpack_from("<Q", ix_data, offset)[0]
        offset += 8
        min_sol_output = struct.unpack_from("<Q", ix_data, offset)[0]

        def get_account(index: int) -> str:
            if index >= len(ix_accounts):
                return ""
            account_index = ix_accounts[index]
            if account_index >= len(account_keys):
                return ""
            return base58.b58encode(bytes(account_keys[account_index])).decode()

        return {
            "mint": get_account(2),
            "trader": get_account(6),
            "token_amount": token_amount,
            "min_sol_output": min_sol_output,
            "bonding_curve_key": get_account(3),
        }
    except Exception as e:
        logger.debug(f"[GRPC] Failed to decode sell instruction: {e}")
        return None


def classify_instruction(ix_data: bytes) -> str | None:
    """Classify pump.fun instruction by its 8-byte discriminator.

    Returns: "create", "buy", "sell", or None if unknown.
    """
    if len(ix_data) < 8:
        return None
    prefix = ix_data[:8]
    if prefix == DISCRIMINATOR_CREATE:
        return "create"
    if prefix == DISCRIMINATOR_BUY:
        return "buy"
    if prefix == DISCRIMINATOR_SELL:
        return "sell"
    return None


def is_migration_transaction(
    account_keys: list[bytes],
) -> str | None:
    """Check if transaction involves Raydium migration.

    Returns the base mint address if migration detected, None otherwise.
    """
    raydium_bytes = base58.b58decode(RAYDIUM_MIGRATION_PROGRAM)
    for key in account_keys:
        if bytes(key) == raydium_bytes:
            return "migration_detected"
    return None

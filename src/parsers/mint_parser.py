"""Direct on-chain mint account parser — Token2022 extension detection.

Parses raw SPL Token / Token2022 mint account data via Solana RPC
getAccountInfo call. Detects dangerous extensions in ~100ms.
"""

import base64
import struct
from dataclasses import dataclass, field
from enum import IntEnum

import httpx
from loguru import logger

# SPL Token mint layout: 82 bytes
# [0:36]   mintAuthorityOption (4) + mintAuthority (32)
# [36:44]  supply (u64)
# [44:45]  decimals (u8)
# [45:46]  isInitialized (bool)
# [46:82]  freezeAuthorityOption (4) + freezeAuthority (32)
SPL_MINT_SIZE = 82

# Well-known null address (system program)
NULL_ADDRESS = "11111111111111111111111111111111"

# Token2022 extension type IDs (from spl-token-2022 source)
# https://github.com/solana-labs/solana-program-library/blob/master/token/program-2022/src/extension/mod.rs


class Token2022ExtType(IntEnum):
    """Known Token2022 extension types."""

    TRANSFER_FEE_CONFIG = 1
    TRANSFER_FEE_AMOUNT = 2
    MINT_CLOSE_AUTHORITY = 3
    CONFIDENTIAL_TRANSFER_MINT = 4
    CONFIDENTIAL_TRANSFER_ACCOUNT = 5
    DEFAULT_ACCOUNT_STATE = 6
    IMMUTABLE_OWNER = 7
    MEMO_TRANSFER = 8
    NON_TRANSFERABLE = 9
    INTEREST_BEARING_CONFIG = 10
    CPI_GUARD = 11
    PERMANENT_DELEGATE = 12
    NON_TRANSFERABLE_ACCOUNT = 13
    TRANSFER_HOOK = 14
    TRANSFER_HOOK_ACCOUNT = 15
    METADATA_POINTER = 18
    TOKEN_METADATA = 19
    GROUP_POINTER = 20
    GROUP_MEMBER_POINTER = 22


# Extensions that represent security risks for token holders
DANGEROUS_EXTENSIONS = {
    Token2022ExtType.PERMANENT_DELEGATE,
    Token2022ExtType.NON_TRANSFERABLE,
    Token2022ExtType.TRANSFER_HOOK,
}

RISKY_EXTENSIONS = {
    Token2022ExtType.TRANSFER_FEE_CONFIG,
    Token2022ExtType.DEFAULT_ACCOUNT_STATE,
}


@dataclass
class MintInfo:
    """Parsed mint account information."""

    supply: int = 0
    decimals: int = 0
    mint_authority: str | None = None  # None = renounced
    freeze_authority: str | None = None  # None = safe
    is_token2022: bool = False
    extensions: list[int] = field(default_factory=list)
    dangerous_extensions: list[str] = field(default_factory=list)
    risky_extensions: list[str] = field(default_factory=list)
    parse_error: str | None = None

    @property
    def mint_authority_active(self) -> bool:
        return self.mint_authority is not None

    @property
    def freeze_authority_active(self) -> bool:
        return self.freeze_authority is not None

    @property
    def has_dangerous_extensions(self) -> bool:
        return len(self.dangerous_extensions) > 0

    @property
    def risk_score(self) -> int:
        """Compute risk score from mint properties (0-100)."""
        score = 0
        if self.mint_authority_active:
            score += 20
        if self.freeze_authority_active:
            score += 15
        for _ in self.dangerous_extensions:
            score += 20
        for _ in self.risky_extensions:
            score += 10
        return min(score, 100)


async def parse_mint_account(rpc_url: str, mint_address: str) -> MintInfo:
    """Fetch and parse mint account data from Solana RPC.

    Returns MintInfo with parsed fields. On error, returns MintInfo
    with parse_error set.
    """
    # Phase 54: Defensive — ensure rpc_url is str (bytes from Redis serialization)
    if isinstance(rpc_url, bytes):
        rpc_url = rpc_url.decode("utf-8")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [
            mint_address,
            {"encoding": "base64", "commitment": "confirmed"},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(rpc_url, json=payload)
            if resp.status_code != 200:
                return MintInfo(parse_error=f"RPC HTTP {resp.status_code}")

            data = resp.json()
            result = data.get("result")
            if not result or not result.get("value"):
                return MintInfo(parse_error="Account not found")

            account = result["value"]
            raw_data_list = account.get("data", [])
            if not raw_data_list or len(raw_data_list) < 1:
                return MintInfo(parse_error="No account data")

            raw_b64 = raw_data_list[0]
            raw_bytes = base64.b64decode(raw_b64)

            return _decode_mint(raw_bytes)

    except (httpx.TimeoutException, httpx.ConnectError) as e:
        logger.debug(f"[MINT] RPC error for {mint_address[:12]}: {e}")
        return MintInfo(parse_error=str(e))
    except Exception as e:
        logger.warning(f"[MINT] Unexpected error parsing {mint_address[:12]}: {e}")
        return MintInfo(parse_error=str(e))


def _decode_mint(raw: bytes) -> MintInfo:
    """Decode raw mint account bytes (SPL Token or Token2022)."""
    if len(raw) < SPL_MINT_SIZE:
        return MintInfo(parse_error=f"Data too short: {len(raw)} bytes")

    # Parse mint authority (COption<Pubkey>: 4 bytes option + 32 bytes key)
    mint_auth_option = struct.unpack_from("<I", raw, 0)[0]
    mint_authority: str | None = None
    if mint_auth_option == 1:
        mint_auth_bytes = raw[4:36]
        mint_authority = _bytes_to_base58(mint_auth_bytes)
        if mint_authority == NULL_ADDRESS:
            mint_authority = None

    # Parse supply and decimals
    supply = struct.unpack_from("<Q", raw, 36)[0]
    decimals = raw[44]

    # Parse freeze authority
    freeze_auth_option = struct.unpack_from("<I", raw, 46)[0]
    freeze_authority: str | None = None
    if freeze_auth_option == 1:
        freeze_auth_bytes = raw[50:82]
        freeze_authority = _bytes_to_base58(freeze_auth_bytes)
        if freeze_authority == NULL_ADDRESS:
            freeze_authority = None

    # Detect Token2022 (data > 82 bytes = has extensions)
    is_token2022 = len(raw) > SPL_MINT_SIZE
    extensions: list[int] = []
    dangerous: list[str] = []
    risky: list[str] = []

    if is_token2022:
        extensions = _parse_extensions(raw[SPL_MINT_SIZE:])
        for ext_type in extensions:
            try:
                ext = Token2022ExtType(ext_type)
                if ext in DANGEROUS_EXTENSIONS:
                    dangerous.append(ext.name)
                elif ext in RISKY_EXTENSIONS:
                    risky.append(ext.name)
            except ValueError:
                pass  # Unknown extension type

    return MintInfo(
        supply=supply,
        decimals=decimals,
        mint_authority=mint_authority,
        freeze_authority=freeze_authority,
        is_token2022=is_token2022,
        extensions=extensions,
        dangerous_extensions=dangerous,
        risky_extensions=risky,
    )


def _parse_extensions(ext_data: bytes) -> list[int]:
    """Parse Token2022 extension TLV (Type-Length-Value) entries.

    Extension data starts with account type byte, then padding to 82+1,
    followed by TLV entries: u16 type + u16 length + data.
    """
    extensions: list[int] = []
    # Skip account type byte (1 byte) if present
    offset = 1 if len(ext_data) > 0 else 0

    while offset + 4 <= len(ext_data):
        try:
            ext_type = struct.unpack_from("<H", ext_data, offset)[0]
            ext_len = struct.unpack_from("<H", ext_data, offset + 2)[0]
        except struct.error:
            break

        if ext_type == 0 and ext_len == 0:
            break  # End of extensions

        extensions.append(ext_type)
        offset += 4 + ext_len

        # Safety: don't read past buffer
        if offset > len(ext_data):
            break

    return extensions


# Base58 alphabet
_B58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _bytes_to_base58(data: bytes) -> str:
    """Convert bytes to base58 string (Solana address encoding)."""
    n = int.from_bytes(data, "big")
    result = []
    while n > 0:
        n, remainder = divmod(n, 58)
        result.append(_B58_ALPHABET[remainder:remainder + 1])
    # Handle leading zeros
    for byte in data:
        if byte == 0:
            result.append(b"1")
        else:
            break
    return b"".join(reversed(result)).decode("ascii")

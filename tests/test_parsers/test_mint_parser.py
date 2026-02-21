"""Tests for mint_parser — on-chain mint account parsing."""

import base64
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.parsers.mint_parser import (
    MintInfo,
    Token2022ExtType,
    _bytes_to_base58,
    _decode_mint,
    parse_mint_account,
)


def _build_standard_mint(
    *,
    mint_authority: bytes | None = None,
    freeze_authority: bytes | None = None,
    supply: int = 1_000_000_000,
    decimals: int = 6,
) -> bytes:
    """Build a standard SPL Token mint account (82 bytes)."""
    data = bytearray(82)
    # Mint authority (COption<Pubkey>)
    if mint_authority:
        struct.pack_into("<I", data, 0, 1)  # Some
        data[4:36] = mint_authority
    else:
        struct.pack_into("<I", data, 0, 0)  # None (renounced)

    # Supply
    struct.pack_into("<Q", data, 36, supply)
    # Decimals
    data[44] = decimals
    # isInitialized
    data[45] = 1
    # Freeze authority
    if freeze_authority:
        struct.pack_into("<I", data, 46, 1)  # Some
        data[50:82] = freeze_authority
    else:
        struct.pack_into("<I", data, 46, 0)  # None

    return bytes(data)


def _build_token2022_mint(
    *,
    mint_authority: bytes | None = None,
    freeze_authority: bytes | None = None,
    extensions: list[tuple[int, int]] | None = None,
) -> bytes:
    """Build a Token2022 mint with extensions.

    extensions: list of (type_id, data_length) tuples.
    """
    base = _build_standard_mint(
        mint_authority=mint_authority, freeze_authority=freeze_authority
    )
    if not extensions:
        return base

    # Account type byte + extension TLV entries
    ext_data = bytearray()
    ext_data.append(2)  # AccountType::Mint for Token2022

    for ext_type, ext_len in extensions:
        ext_data += struct.pack("<H", ext_type)
        ext_data += struct.pack("<H", ext_len)
        ext_data += b"\x00" * ext_len

    return base + bytes(ext_data)


FAKE_AUTHORITY = b"\x01" * 32  # Non-null authority


class TestDecodeMint:
    """Unit tests for _decode_mint."""

    def test_standard_token_renounced(self) -> None:
        """Standard SPL token with renounced authorities."""
        raw = _build_standard_mint()
        info = _decode_mint(raw)

        assert info.parse_error is None
        assert info.mint_authority is None
        assert info.freeze_authority is None
        assert info.is_token2022 is False
        assert info.supply == 1_000_000_000
        assert info.decimals == 6
        assert info.dangerous_extensions == []
        assert info.risk_score == 0

    def test_token2022_with_dangerous_extensions(self) -> None:
        """Token2022 with permanentDelegate and nonTransferable."""
        raw = _build_token2022_mint(
            mint_authority=FAKE_AUTHORITY,
            extensions=[
                (Token2022ExtType.PERMANENT_DELEGATE, 32),
                (Token2022ExtType.NON_TRANSFERABLE, 0),
            ],
        )
        info = _decode_mint(raw)

        assert info.is_token2022 is True
        assert "PERMANENT_DELEGATE" in info.dangerous_extensions
        assert "NON_TRANSFERABLE" in info.dangerous_extensions
        assert info.has_dangerous_extensions is True
        assert info.risk_score >= 40  # 20 (mint_auth) + 20 + 20 (two dangerous)

    def test_mint_authority_active_freeze_active(self) -> None:
        """Both authorities active — high risk."""
        raw = _build_standard_mint(
            mint_authority=FAKE_AUTHORITY, freeze_authority=FAKE_AUTHORITY
        )
        info = _decode_mint(raw)

        assert info.mint_authority is not None
        assert info.freeze_authority is not None
        assert info.mint_authority_active is True
        assert info.freeze_authority_active is True
        assert info.risk_score >= 35

    def test_data_too_short(self) -> None:
        """Data shorter than 82 bytes returns error."""
        raw = b"\x00" * 40
        info = _decode_mint(raw)
        assert info.parse_error is not None
        assert "too short" in info.parse_error.lower()


class TestParseMintAccount:
    """Integration tests for parse_mint_account (mocked RPC)."""

    @pytest.mark.asyncio
    async def test_successful_parse(self) -> None:
        """Successful RPC call + parse."""
        raw = _build_standard_mint()
        encoded = base64.b64encode(raw).decode()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "value": {
                    "data": [encoded, "base64"],
                    "owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                }
            }
        }

        with patch("src.parsers.mint_parser.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            info = await parse_mint_account("https://rpc.example.com", "TokenMint123")

        assert info.parse_error is None
        assert info.supply == 1_000_000_000

    @pytest.mark.asyncio
    async def test_account_not_found(self) -> None:
        """RPC returns null value → parse error."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"value": None}}

        with patch("src.parsers.mint_parser.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            info = await parse_mint_account("https://rpc.example.com", "NotExist123")

        assert info.parse_error is not None

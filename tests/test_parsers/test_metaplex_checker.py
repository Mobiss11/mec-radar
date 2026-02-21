"""Tests for Metaplex metadata deep check."""

import pytest
from unittest.mock import AsyncMock

from src.parsers.metaplex_checker import (
    MetaplexCheckResult,
    check_metaplex_metadata,
    _classify_uri,
    _detect_homoglyphs,
)


class TestClassifyUri:
    def test_ipfs_gateway(self) -> None:
        assert _classify_uri("https://ipfs.io/ipfs/QmABC123") == "ipfs"

    def test_ipfs_protocol(self) -> None:
        assert _classify_uri("ipfs://QmABC123") == "ipfs"

    def test_arweave(self) -> None:
        assert _classify_uri("https://arweave.net/abc123") == "arweave"

    def test_arweave_protocol(self) -> None:
        assert _classify_uri("ar://abc123") == "arweave"

    def test_nftstorage(self) -> None:
        assert _classify_uri("https://nftstorage.link/ipfs/abc") == "ipfs"

    def test_http_plain(self) -> None:
        assert _classify_uri("https://example.com/metadata.json") == "http"

    def test_unknown(self) -> None:
        assert _classify_uri("data:application/json;base64,abc") == "unknown"

    def test_shadow_drive(self) -> None:
        assert _classify_uri("https://shdw-drive.genesysgo.net/abc") == "shadow"


class TestDetectHomoglyphs:
    def test_clean_name(self) -> None:
        assert _detect_homoglyphs("SOLANA") == []

    def test_cyrillic_a(self) -> None:
        # Cyrillic А looks like Latin A
        result = _detect_homoglyphs("SOL\u0410NA")
        assert len(result) == 1
        assert "А→A" in result[0]

    def test_cyrillic_o(self) -> None:
        result = _detect_homoglyphs("S\u041EL")
        assert len(result) == 1

    def test_multiple_homoglyphs(self) -> None:
        # "ВОНК" using Cyrillic В, О, Н, К
        result = _detect_homoglyphs("\u0412\u041E\u041D\u041A")
        assert len(result) == 4

    def test_pure_ascii(self) -> None:
        assert _detect_homoglyphs("DOGE COIN 123") == []


class TestMetaplexCheckResult:
    def test_clean_result_no_impact(self) -> None:
        result = MetaplexCheckResult(is_mutable=False, uri_type="ipfs")
        assert result.score_impact == 0
        assert result.risk_flags == []

    def test_mutable_penalty(self) -> None:
        result = MetaplexCheckResult(is_mutable=True, uri_type="ipfs")
        assert result.score_impact == -5
        assert "mutable_metadata" in result.risk_flags

    def test_http_uri_penalty(self) -> None:
        result = MetaplexCheckResult(is_mutable=False, uri_type="http")
        assert result.score_impact == -3
        assert "http_uri" in result.risk_flags

    def test_homoglyph_penalty(self) -> None:
        result = MetaplexCheckResult(has_homoglyphs=True)
        assert result.score_impact == -8
        assert "name_spoofing" in result.risk_flags

    def test_all_flags_combined(self) -> None:
        result = MetaplexCheckResult(
            is_mutable=True, uri_type="http", has_homoglyphs=True
        )
        assert result.score_impact == -16
        assert len(result.risk_flags) == 3


class TestCheckMetaplexMetadata:
    @pytest.mark.asyncio
    async def test_asset_not_found(self) -> None:
        helius = AsyncMock()
        helius.get_asset.return_value = None
        result = await check_metaplex_metadata(helius, "mint123")
        assert result is None

    @pytest.mark.asyncio
    async def test_mutable_with_http_uri(self) -> None:
        helius = AsyncMock()
        helius.get_asset.return_value = {
            "mutable": True,
            "content": {
                "json_uri": "https://example.com/meta.json",
                "metadata": {"name": "Test Token", "symbol": "TEST"},
            },
        }
        result = await check_metaplex_metadata(helius, "mint123")
        assert result is not None
        assert result.is_mutable is True
        assert result.uri_type == "http"
        assert result.name == "Test Token"
        assert result.score_impact == -8  # -5 mutable + -3 http

    @pytest.mark.asyncio
    async def test_immutable_with_ipfs(self) -> None:
        helius = AsyncMock()
        helius.get_asset.return_value = {
            "mutable": False,
            "content": {
                "json_uri": "https://ipfs.io/ipfs/QmABC",
                "metadata": {"name": "Good Token", "symbol": "GOOD"},
            },
        }
        result = await check_metaplex_metadata(helius, "mint123")
        assert result is not None
        assert result.is_mutable is False
        assert result.uri_type == "ipfs"
        assert result.score_impact == 0

    @pytest.mark.asyncio
    async def test_homoglyph_detection(self) -> None:
        helius = AsyncMock()
        helius.get_asset.return_value = {
            "mutable": False,
            "content": {
                "json_uri": "https://arweave.net/abc",
                "metadata": {"name": "SOL\u0410NA", "symbol": "SOL"},
            },
        }
        result = await check_metaplex_metadata(helius, "mint123")
        assert result is not None
        assert result.has_homoglyphs is True
        assert result.score_impact == -8

    @pytest.mark.asyncio
    async def test_helius_error(self) -> None:
        helius = AsyncMock()
        helius.get_asset.side_effect = Exception("timeout")
        result = await check_metaplex_metadata(helius, "mint123")
        assert result is None

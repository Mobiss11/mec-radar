"""Metaplex metadata deep check — isMutable, URI type, name spoofing.

Uses Helius DAS (Digital Asset Standard) `getAsset` to fetch rich metadata
that goes beyond what Birdeye provides:
- isMutable: If true, creator can change name/image/URI at any time (scam risk)
- URI type: HTTP URIs are red flags (can be changed), IPFS/Arweave are immutable
- Name spoofing: Detect homoglyph attacks (Cyrillic/Greek lookalike chars)

Cost: 10 Helius credits per call (DAS API).
Latency: ~500ms.
"""

import re
from dataclasses import dataclass

from loguru import logger

from src.parsers.helius.client import HeliusClient


# Common homoglyph patterns (Cyrillic/Greek chars that look like Latin)
_HOMOGLYPH_MAP: dict[str, str] = {
    "\u0410": "A",  # Cyrillic А
    "\u0412": "B",  # Cyrillic В
    "\u0421": "C",  # Cyrillic С
    "\u0415": "E",  # Cyrillic Е
    "\u041D": "H",  # Cyrillic Н
    "\u041A": "K",  # Cyrillic К
    "\u041C": "M",  # Cyrillic М
    "\u041E": "O",  # Cyrillic О
    "\u0420": "P",  # Cyrillic Р
    "\u0422": "T",  # Cyrillic Т
    "\u0425": "X",  # Cyrillic Х
    "\u0430": "a",  # Cyrillic а
    "\u0435": "e",  # Cyrillic е
    "\u043E": "o",  # Cyrillic о
    "\u0440": "p",  # Cyrillic р
    "\u0441": "c",  # Cyrillic с
    "\u0443": "y",  # Cyrillic у
    "\u0445": "x",  # Cyrillic х
}

# Known legitimate token names that might trigger false positives
_WHITELIST_NAMES: set[str] = set()


@dataclass
class MetaplexCheckResult:
    """Result of Metaplex metadata deep check."""

    is_mutable: bool | None = None
    uri_type: str | None = None  # "ipfs", "arweave", "http", "unknown"
    uri: str | None = None
    has_homoglyphs: bool = False
    homoglyph_chars: list[str] | None = None
    name: str | None = None
    symbol: str | None = None

    @property
    def score_impact(self) -> int:
        """Negative score impact based on metadata red flags."""
        impact = 0
        if self.is_mutable is True:
            impact -= 5
        if self.uri_type == "http":
            impact -= 3
        if self.has_homoglyphs:
            impact -= 8
        return impact

    @property
    def risk_flags(self) -> list[str]:
        """Human-readable list of risk flags."""
        flags = []
        if self.is_mutable is True:
            flags.append("mutable_metadata")
        if self.uri_type == "http":
            flags.append("http_uri")
        if self.has_homoglyphs:
            flags.append("name_spoofing")
        return flags


async def check_metaplex_metadata(
    helius: HeliusClient,
    token_address: str,
) -> MetaplexCheckResult | None:
    """Fetch and analyze Metaplex metadata via Helius DAS API.

    Args:
        helius: HeliusClient instance.
        token_address: Token mint address.

    Returns:
        MetaplexCheckResult or None if metadata unavailable.
    """
    result = MetaplexCheckResult()

    try:
        asset = await helius.get_asset(token_address)
        if asset is None:
            return None

        # Extract mutability
        result.is_mutable = asset.get("mutable", None)

        # Extract content/URI
        content = asset.get("content", {})
        json_uri = content.get("json_uri", "")
        result.uri = json_uri

        if json_uri:
            result.uri_type = _classify_uri(json_uri)

        # Extract name/symbol from metadata
        metadata = content.get("metadata", {})
        result.name = metadata.get("name")
        result.symbol = metadata.get("symbol")

        # Check for homoglyphs in name
        if result.name:
            homoglyphs = _detect_homoglyphs(result.name)
            if homoglyphs:
                result.has_homoglyphs = True
                result.homoglyph_chars = homoglyphs

    except Exception as e:
        logger.debug(f"[METAPLEX] Check failed for {token_address[:12]}: {e}")
        return None

    return result


def _classify_uri(uri: str) -> str:
    """Classify a metadata URI by storage type."""
    uri_lower = uri.lower()
    if "ipfs" in uri_lower or uri_lower.startswith("ipfs://"):
        return "ipfs"
    if "arweave.net" in uri_lower or uri_lower.startswith("ar://"):
        return "arweave"
    if "nftstorage" in uri_lower:
        return "ipfs"  # NFT.Storage uses IPFS
    if "shadow-storage" in uri_lower or "shdw" in uri_lower:
        return "shadow"  # Solana Shadow Drive
    if uri_lower.startswith("http://") or uri_lower.startswith("https://"):
        return "http"
    return "unknown"


def _detect_homoglyphs(name: str) -> list[str]:
    """Detect homoglyph characters in a token name.

    Returns list of suspicious characters found.
    """
    found: list[str] = []
    for char in name:
        if char in _HOMOGLYPH_MAP:
            found.append(f"{char}→{_HOMOGLYPH_MAP[char]}")
    return found

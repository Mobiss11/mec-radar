"""Test Pydantic model parsing for all parsers."""

from decimal import Decimal

from src.parsers.gmgn.models import (
    GmgnNewPair,
    GmgnPumpToken,
    GmgnSecurityInfo,
    GmgnSmartWallet,
    GmgnTokenInfo,
    GmgnTopHolder,
)
from src.parsers.pumpportal.models import (
    PumpPortalMigration,
    PumpPortalNewToken,
    PumpPortalTrade,
)
from src.parsers.dexscreener.models import DexScreenerPair


# === gmgn.ai models ===


def test_gmgn_token_info_minimal():
    data = {"address": "abc123", "name": "TestToken", "symbol": "TT"}
    token = GmgnTokenInfo.model_validate(data)
    assert token.address == "abc123"
    assert token.name == "TestToken"
    assert token.price is None


def test_gmgn_token_info_flat():
    """Flat format (from /api/v1/token_info single endpoint)."""
    data = {
        "address": "abc123",
        "name": "TestToken",
        "symbol": "TT",
        "price": "0.0001",
        "market_cap": "50000",
        "liquidity": "25000",
        "volume_5min": "1200",
        "volume_1h": "8000",
        "volume_24h": "50000",
        "holder_count": 150,
    }
    token = GmgnTokenInfo.model_validate(data)
    assert token.effective_price == Decimal("0.0001")
    assert token.effective_volume_5m == Decimal("1200")
    assert token.holder_count == 150


def test_gmgn_token_info_nested_price():
    """Nested format (from POST /api/v1/mutil_window_token_info)."""
    data = {
        "address": "abc123",
        "name": "TestToken",
        "symbol": "TT",
        "price": {
            "price": "0.00025",
            "volume_5m": "3500",
            "volume_1h": "12000",
            "volume_24h": "80000",
        },
        "market_cap": "75000",
        "liquidity": "40000",
        "holder_count": 250,
    }
    token = GmgnTokenInfo.model_validate(data)
    assert token.effective_price == Decimal("0.00025")
    assert token.effective_volume_5m == Decimal("3500")
    assert token.effective_volume_1h == Decimal("12000")
    assert token.effective_volume_24h == Decimal("80000")


def test_gmgn_security_info():
    data = {
        "is_open_source": True,
        "is_honeypot": False,
        "buy_tax": "0",
        "sell_tax": "5",
        "lp_burned": True,
        "unknown_field": "ignored",
    }
    sec = GmgnSecurityInfo.model_validate(data)
    assert sec.is_honeypot is False
    assert sec.sell_tax == Decimal("5")
    assert sec.lp_burned is True


def test_gmgn_top_holder():
    data = {"address": "wallet123", "balance": "1000000", "percentage": "5.5"}
    holder = GmgnTopHolder.model_validate(data)
    assert holder.percentage == Decimal("5.5")


def test_gmgn_smart_wallet():
    data = {
        "address": "wallet_addr",
        "category": "smart_degen",
        "win_rate": "0.65",
        "total_trades": 200,
    }
    wallet = GmgnSmartWallet.model_validate(data)
    assert wallet.win_rate == Decimal("0.65")
    assert wallet.total_trades == 200


def test_gmgn_new_pair():
    data = {
        "address": "pool_abc",
        "base_address": "token_abc",
        "symbol": "ABC",
        "initial_liquidity": "30000",
        "base_token_info": {"name": "Alpha Token", "symbol": "ALPHA"},
    }
    pair = GmgnNewPair.model_validate(data)
    assert pair.address == "pool_abc"
    assert pair.token_address == "token_abc"
    assert pair.token_name == "Alpha Token"
    assert pair.token_symbol == "ALPHA"
    assert pair.liquidity == Decimal("30000")


def test_gmgn_new_pair_no_base():
    """Falls back to address when base_address is missing."""
    data = {"address": "fallback_addr", "symbol": "FB"}
    pair = GmgnNewPair.model_validate(data)
    assert pair.token_address == "fallback_addr"
    assert pair.token_symbol == "FB"


def test_gmgn_pump_token():
    data = {"address": "pump_token", "progress": "85.5", "holder_count": 300}
    token = GmgnPumpToken.model_validate(data)
    assert token.progress == Decimal("85.5")


# === PumpPortal models ===


def test_pumpportal_new_token():
    data = {
        "signature": "sig123",
        "mint": "mint_address_abc",
        "name": "PumpToken",
        "symbol": "PUMP",
        "txType": "create",
        "traderPublicKey": "deployer_wallet",
        "initialBuy": 0.5,
        "marketCapSol": 30.0,
        "bondingCurveKey": "bc_key",
        "vTokensInBondingCurve": 1000000,
        "vSolInBondingCurve": 85.0,
    }
    token = PumpPortalNewToken.model_validate(data)
    assert token.mint == "mint_address_abc"
    assert token.symbol == "PUMP"
    assert token.initialBuy == Decimal("0.5")


def test_pumpportal_trade():
    data = {
        "signature": "sig456",
        "mint": "token_mint",
        "txType": "buy",
        "traderPublicKey": "buyer_wallet",
        "tokenAmount": 50000,
        "solAmount": 0.1,
        "marketCapSol": 45.0,
    }
    trade = PumpPortalTrade.model_validate(data)
    assert trade.txType == "buy"
    assert trade.solAmount == Decimal("0.1")


def test_pumpportal_migration():
    data = {"mint": "migrated_token", "signature": "sig789", "extra_field": "allowed"}
    migration = PumpPortalMigration.model_validate(data)
    assert migration.mint == "migrated_token"


# === DexScreener models ===


def test_dexscreener_pair():
    data = {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "pair_abc",
        "baseToken": {"address": "token_abc", "name": "TestDex", "symbol": "TDX"},
        "quoteToken": {"address": "So111...", "name": "SOL", "symbol": "SOL"},
        "priceUsd": "0.005",
        "volume": {"m5": 500, "h1": 3000, "h24": 50000},
        "liquidity": {"usd": 25000},
        "fdv": 100000,
        "pairCreatedAt": 1700000000000,
    }
    pair = DexScreenerPair.model_validate(data)
    assert pair.chainId == "solana"
    assert pair.baseToken.symbol == "TDX"
    assert pair.volume.h24 == Decimal("50000")
    assert pair.liquidity.usd == Decimal("25000")


def test_dexscreener_pair_with_txns():
    data = {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "pair_txns",
        "priceUsd": "0.005",
        "txns": {
            "m5": {"buys": 20, "sells": 5},
            "h1": {"buys": 150, "sells": 30},
            "h24": {"buys": 2000, "sells": 500},
        },
    }
    pair = DexScreenerPair.model_validate(data)
    assert pair.txns is not None
    assert pair.txns.h1.buys == 150
    assert pair.txns.h1.sells == 30
    assert pair.txns.m5.buys == 20

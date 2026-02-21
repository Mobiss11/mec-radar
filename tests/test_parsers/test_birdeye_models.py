"""Test Birdeye Data Services API Pydantic models."""

from decimal import Decimal

from src.parsers.birdeye.models import (
    BirdeyePrice,
    BirdeyeTokenOverview,
    BirdeyeTokenSecurity,
)


def test_token_overview_minimal():
    overview = BirdeyeTokenOverview(address="test_addr")
    assert overview.address == "test_addr"
    assert overview.price is None
    assert overview.marketCap is None


def test_token_overview_full():
    overview = BirdeyeTokenOverview(
        address="So111...",
        name="Wrapped SOL",
        symbol="SOL",
        price=Decimal("150.5"),
        marketCap=Decimal("60000000000"),
        fdv=Decimal("70000000000"),
        liquidity=Decimal("500000"),
        holder=1000000,
        v5mUSD=Decimal("1000"),
        v1hUSD=Decimal("50000"),
        v24hUSD=Decimal("2000000"),
        buy5m=100,
        sell5m=80,
        buy1h=500,
        sell1h=400,
        buy24h=10000,
        sell24h=9000,
        uniqueWallet5m=50,
        uniqueWallet1h=200,
        priceChange1hPercent=Decimal("2.5"),
    )
    assert overview.price == Decimal("150.5")
    assert overview.holder == 1000000
    assert overview.buy5m == 100
    assert overview.sell1h == 400
    assert overview.uniqueWallet5m == 50


def test_token_overview_extra_fields_ignored():
    overview = BirdeyeTokenOverview(
        address="test",
        unknownField="should_be_ignored",
        anotherExtra=123,
    )
    assert overview.address == "test"


def test_birdeye_price():
    price = BirdeyePrice(
        value=Decimal("0.001234"),
        updateUnixTime=1700000000,
        liquidity=Decimal("50000"),
    )
    assert price.value == Decimal("0.001234")
    assert price.updateUnixTime == 1700000000


def test_birdeye_price_minimal():
    price = BirdeyePrice()
    assert price.value is None
    assert price.liquidity is None


def test_token_security_mintable():
    sec = BirdeyeTokenSecurity(
        mintAuthority="SomeMintAuthority123",
        freezeAuthority=None,
    )
    assert sec.is_mintable is True
    assert sec.is_freezable is False
    assert sec.has_transfer_fee is False


def test_token_security_not_mintable():
    sec = BirdeyeTokenSecurity(
        mintAuthority=None,
        freezeAuthority=None,
    )
    assert sec.is_mintable is False


def test_token_security_freezable():
    sec = BirdeyeTokenSecurity(freezeAuthority="FreezeAuth456")
    assert sec.is_freezable is True


def test_token_security_transfer_fee():
    sec = BirdeyeTokenSecurity(transferFeeEnable=True)
    assert sec.has_transfer_fee is True


def test_token_security_with_lock_info():
    sec = BirdeyeTokenSecurity(
        lockInfo={"lockTag": "Unknown", "lockAddress": "abc123"},
        top10HolderPercent=Decimal("35.5"),
    )
    assert sec.lockInfo is not None
    assert sec.top10HolderPercent == Decimal("35.5")


def test_token_security_extra_ignored():
    sec = BirdeyeTokenSecurity(
        jupStrictList=True,
        someNewApiField="ignored",
    )
    assert sec.jupStrictList is True

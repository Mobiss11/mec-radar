"""Test entry signal detection rules."""

from decimal import Decimal

from src.models.token import CreatorProfile, TokenSecurity, TokenSnapshot
from src.parsers.signals import evaluate_signals


def _make_snapshot(**kwargs) -> TokenSnapshot:
    defaults = {
        "token_id": 1,
        "liquidity_usd": Decimal("50000"),
        "holders_count": 100,
        "volume_1h": Decimal("30000"),
        "score": 55,
    }
    defaults.update(kwargs)
    return TokenSnapshot(**defaults)


def _make_security(**kwargs) -> TokenSecurity:
    defaults = {
        "token_id": 1,
        "is_honeypot": False,
        "is_mintable": False,
        "lp_burned": True,
        "contract_renounced": True,
        "sell_tax": Decimal("0"),
        "top10_holders_pct": Decimal("20"),
    }
    defaults.update(kwargs)
    return TokenSecurity(**defaults)


def test_strong_buy_signal():
    """Token with high score, smart money, buy pressure → strong_buy."""
    snapshot = _make_snapshot(
        score=65,
        smart_wallets_count=3,
        buys_1h=100,
        sells_1h=20,
        volume_1h=Decimal("120000"),
    )
    security = _make_security()
    result = evaluate_signals(snapshot, security, holder_velocity=80.0)
    assert result.action in ("strong_buy", "buy")
    assert result.net_score >= 5
    assert "high_score" in result.reasons
    assert "buy_pressure" in result.reasons
    assert "smart_money" in result.reasons


def test_watch_signal_moderate_token():
    """Token with decent score but no special signals → watch."""
    snapshot = _make_snapshot(score=35, smart_wallets_count=0)
    result = evaluate_signals(snapshot, None)
    assert result.action in ("watch", "avoid")


def test_avoid_honeypot():
    """Honeypot triggers massive bearish weight → avoid."""
    snapshot = _make_snapshot(score=60)
    security = _make_security(is_honeypot=True)
    result = evaluate_signals(snapshot, security)
    assert result.action == "avoid"
    assert "honeypot" in result.reasons


def test_bearish_risky_creator():
    """Risky creator adds bearish weight."""
    snapshot = _make_snapshot(score=40)
    creator = CreatorProfile(address="risky", risk_score=70)
    result = evaluate_signals(snapshot, None, creator_profile=creator)
    assert "risky_creator" in result.reasons
    assert result.bearish_score >= 3


def test_bearish_high_concentration():
    """Top 10 holders > 50% is bearish."""
    snapshot = _make_snapshot(score=40, top10_holders_pct=Decimal("65"))
    result = evaluate_signals(snapshot, None)
    assert "high_concentration" in result.reasons


def test_bearish_tiny_liquidity():
    """Very low liquidity is bearish — hard gate fires before tiny_liquidity rule.

    Since Phase 30 added LIQ < $30K hard gate, tokens with $3K liq are now
    blocked by the gate (action=avoid, low_liquidity_gate) instead of the
    softer tiny_liquidity rule.
    """
    snapshot = _make_snapshot(
        score=30,
        liquidity_usd=Decimal("3000"),
        volume_1h=Decimal("1000"),
    )
    result = evaluate_signals(snapshot, None)
    assert result.action == "avoid"
    assert "low_liquidity_gate" in result.reasons


def test_volume_spike_bullish():
    """Vol/liq ratio >= 2 fires volume_spike rule.

    Liquidity raised to $40K to pass the LIQ < $30K hard gate (Phase 30).
    """
    snapshot = _make_snapshot(
        liquidity_usd=Decimal("40000"),
        volume_1h=Decimal("100000"),
        score=45,
    )
    result = evaluate_signals(snapshot, None)
    assert "volume_spike" in result.reasons


def test_price_momentum():
    """Price increase >= 20% fires momentum rule."""
    prev = _make_snapshot(price=Decimal("0.001"))
    current = _make_snapshot(price=Decimal("0.0015"), score=50)
    result = evaluate_signals(current, None, prev_snapshot=prev)
    assert "price_momentum" in result.reasons


def test_safe_creator_bullish():
    """Low risk creator is bullish."""
    snapshot = _make_snapshot(score=50)
    creator = CreatorProfile(address="safe", risk_score=10)
    result = evaluate_signals(snapshot, None, creator_profile=creator)
    assert "safe_creator" in result.reasons


def test_security_cleared():
    """Multiple security green flags fire security_cleared."""
    snapshot = _make_snapshot(score=40)
    security = _make_security(lp_burned=True, contract_renounced=True, sell_tax=Decimal("2"))
    result = evaluate_signals(snapshot, security)
    assert "security_cleared" in result.reasons


def test_high_sell_tax_bearish():
    """Sell tax > 10% is bearish."""
    snapshot = _make_snapshot(score=40)
    security = _make_security(sell_tax=Decimal("15"))
    result = evaluate_signals(snapshot, security)
    assert "high_sell_tax" in result.reasons

"""Tests for TradingCircuitBreaker and RiskManager.

All tests are synchronous (pure logic), no DB or HTTP calls.
"""

from unittest.mock import patch

import pytest

from src.trading.risk_manager import RiskManager, TradingCircuitBreaker


# ═══════════════════════════════════════════════════════════════════════
# TradingCircuitBreaker
# ═══════════════════════════════════════════════════════════════════════


class TestCircuitBreakerInitial:
    """Initial state assertions."""

    def test_not_tripped_initially(self):
        cb = TradingCircuitBreaker(threshold=3, cooldown_sec=1800)
        assert cb.is_tripped is False

    def test_seconds_until_reset_zero_initially(self):
        cb = TradingCircuitBreaker()
        assert cb.seconds_until_reset == 0.0

    def test_total_failures_zero_initially(self):
        cb = TradingCircuitBreaker()
        assert cb.total_failures == 0


class TestCircuitBreakerTripping:
    """Tripping behavior after consecutive failures."""

    def test_trips_after_threshold_failures(self):
        cb = TradingCircuitBreaker(threshold=3, cooldown_sec=60)
        cb.record_failure("err1")
        cb.record_failure("err2")
        assert cb.is_tripped is False  # 2 < 3
        cb.record_failure("err3")
        assert cb.is_tripped is True

    def test_trips_after_default_threshold(self):
        """Default threshold is 3."""
        cb = TradingCircuitBreaker()
        for i in range(3):
            cb.record_failure(f"error {i}")
        assert cb.is_tripped is True

    def test_total_failures_increments(self):
        cb = TradingCircuitBreaker(threshold=5)
        for i in range(4):
            cb.record_failure()
        assert cb.total_failures == 4

    def test_trips_only_on_consecutive_failures(self):
        """A success in between resets the consecutive counter."""
        cb = TradingCircuitBreaker(threshold=3, cooldown_sec=60)
        cb.record_failure("e1")
        cb.record_failure("e2")
        cb.record_success()  # reset consecutive count
        cb.record_failure("e3")
        assert cb.is_tripped is False  # only 1 consecutive failure


class TestCircuitBreakerReset:
    """Success and auto-reset behavior."""

    def test_success_resets_consecutive_failures(self):
        cb = TradingCircuitBreaker(threshold=3)
        cb.record_failure("e1")
        cb.record_failure("e2")
        cb.record_success()
        # After success, need 3 more failures to trip
        cb.record_failure("e3")
        cb.record_failure("e4")
        assert cb.is_tripped is False
        cb.record_failure("e5")
        assert cb.is_tripped is True

    def test_auto_reset_after_cooldown(self):
        """Circuit breaker should auto-reset when cooldown expires."""
        cb = TradingCircuitBreaker(threshold=1, cooldown_sec=10)
        cb.record_failure("trip")
        assert cb.is_tripped is True

        # Simulate time passing beyond cooldown by patching time.monotonic
        tripped_time = cb._tripped_at
        with patch("src.trading.risk_manager.time") as mock_time:
            mock_time.monotonic.return_value = tripped_time + 11  # 11s > 10s cooldown
            assert cb.is_tripped is False

    def test_seconds_until_reset_when_tripped(self):
        """Should return positive remaining seconds while tripped."""
        cb = TradingCircuitBreaker(threshold=1, cooldown_sec=100)
        cb.record_failure("trip")
        assert cb.is_tripped is True

        remaining = cb.seconds_until_reset
        # Should be close to 100 (minus tiny elapsed time)
        assert 99.0 <= remaining <= 100.0

    def test_seconds_until_reset_after_cooldown(self):
        """Should return 0.0 after cooldown expires."""
        cb = TradingCircuitBreaker(threshold=1, cooldown_sec=5)
        cb.record_failure("trip")

        tripped_time = cb._tripped_at
        with patch("src.trading.risk_manager.time") as mock_time:
            mock_time.monotonic.return_value = tripped_time + 10  # well past cooldown
            assert cb.seconds_until_reset == 0.0

    def test_total_failures_persists_after_success(self):
        """Total failures should not reset on success, only consecutive."""
        cb = TradingCircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.total_failures == 2  # persists


# ═══════════════════════════════════════════════════════════════════════
# RiskManager.pre_buy_check
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def risk_mgr() -> RiskManager:
    """Standard risk manager with known limits."""
    return RiskManager(
        max_sol_per_trade=0.5,
        max_positions=5,
        max_total_exposure_sol=3.0,
        min_liquidity_usd=1000.0,
        min_wallet_balance_sol=0.05,
    )


class TestRiskManagerAllowsBuy:
    """Scenarios where buy should be allowed."""

    def test_allows_when_all_checks_pass(self, risk_mgr: RiskManager):
        allowed, reason = risk_mgr.pre_buy_check(
            wallet_balance_sol=2.0,
            open_position_count=2,
            total_open_exposure_sol=1.0,
            invest_sol=0.5,
            liquidity_usd=5000.0,
        )
        assert allowed is True
        assert reason == ""

    def test_allows_with_none_liquidity(self, risk_mgr: RiskManager):
        """None liquidity should skip the liquidity check."""
        allowed, reason = risk_mgr.pre_buy_check(
            wallet_balance_sol=2.0,
            open_position_count=0,
            total_open_exposure_sol=0.0,
            invest_sol=0.5,
            liquidity_usd=None,
        )
        assert allowed is True
        assert reason == ""

    def test_allows_exactly_at_position_limit_minus_one(self, risk_mgr: RiskManager):
        """4 positions (limit=5) should still allow."""
        allowed, _ = risk_mgr.pre_buy_check(
            wallet_balance_sol=2.0,
            open_position_count=4,
            total_open_exposure_sol=2.0,
            invest_sol=0.5,
            liquidity_usd=5000.0,
        )
        assert allowed is True

    def test_allows_strong_buy_1_5x_multiplier(self, risk_mgr: RiskManager):
        """Strong_buy 1.5x of 0.5 SOL = 0.75 SOL, within 1.6x buffer (0.8)."""
        allowed, _ = risk_mgr.pre_buy_check(
            wallet_balance_sol=2.0,
            open_position_count=0,
            total_open_exposure_sol=0.0,
            invest_sol=0.75,
            liquidity_usd=5000.0,
        )
        assert allowed is True


class TestRiskManagerBlocksBuy:
    """Scenarios where buy should be blocked."""

    def test_blocks_insufficient_balance(self, risk_mgr: RiskManager):
        """Balance too low to cover invest + reserve."""
        allowed, reason = risk_mgr.pre_buy_check(
            wallet_balance_sol=0.5,  # 0.5 < 0.5 + 0.05 reserve
            open_position_count=0,
            total_open_exposure_sol=0.0,
            invest_sol=0.5,
            liquidity_usd=5000.0,
        )
        assert allowed is False
        assert "Insufficient balance" in reason

    def test_blocks_zero_balance(self, risk_mgr: RiskManager):
        allowed, reason = risk_mgr.pre_buy_check(
            wallet_balance_sol=0.0,
            open_position_count=0,
            total_open_exposure_sol=0.0,
            invest_sol=0.5,
            liquidity_usd=5000.0,
        )
        assert allowed is False
        assert "Insufficient balance" in reason

    def test_blocks_max_positions_reached(self, risk_mgr: RiskManager):
        """At or above max_positions should block."""
        allowed, reason = risk_mgr.pre_buy_check(
            wallet_balance_sol=10.0,
            open_position_count=5,  # == max
            total_open_exposure_sol=2.0,
            invest_sol=0.5,
            liquidity_usd=5000.0,
        )
        assert allowed is False
        assert "Max positions" in reason

    def test_blocks_over_max_positions(self, risk_mgr: RiskManager):
        allowed, reason = risk_mgr.pre_buy_check(
            wallet_balance_sol=10.0,
            open_position_count=7,  # > max
            total_open_exposure_sol=2.0,
            invest_sol=0.5,
            liquidity_usd=5000.0,
        )
        assert allowed is False
        assert "Max positions" in reason

    def test_blocks_total_exposure_exceeded(self, risk_mgr: RiskManager):
        """Existing exposure + new invest exceeds max_total_exposure_sol."""
        allowed, reason = risk_mgr.pre_buy_check(
            wallet_balance_sol=10.0,
            open_position_count=1,
            total_open_exposure_sol=2.8,  # + 0.5 = 3.3 > 3.0 max
            invest_sol=0.5,
            liquidity_usd=5000.0,
        )
        assert allowed is False
        assert "exposure" in reason.lower()

    def test_blocks_low_liquidity(self, risk_mgr: RiskManager):
        """Liquidity below min_liquidity_usd should block."""
        allowed, reason = risk_mgr.pre_buy_check(
            wallet_balance_sol=10.0,
            open_position_count=0,
            total_open_exposure_sol=0.0,
            invest_sol=0.5,
            liquidity_usd=500.0,  # < 1000 min
        )
        assert allowed is False
        assert "Liquidity" in reason

    def test_blocks_trade_size_exceeds_max(self, risk_mgr: RiskManager):
        """Trade size beyond max_sol_per_trade * 1.6 buffer should block."""
        allowed, reason = risk_mgr.pre_buy_check(
            wallet_balance_sol=10.0,
            open_position_count=0,
            total_open_exposure_sol=0.0,
            invest_sol=0.85,  # 0.5 * 1.6 = 0.80, 0.85 > 0.80
            liquidity_usd=5000.0,
        )
        assert allowed is False
        assert "exceeds max" in reason.lower()


class TestRiskManagerPreSellCheck:
    """Tests for pre_sell_check."""

    def test_allows_sufficient_token_balance(self, risk_mgr: RiskManager):
        allowed, reason = risk_mgr.pre_sell_check(
            token_balance_raw=1_000_000,
            required_amount_raw=500_000,
        )
        assert allowed is True
        assert reason == ""

    def test_blocks_insufficient_token_balance(self, risk_mgr: RiskManager):
        allowed, reason = risk_mgr.pre_sell_check(
            token_balance_raw=100,
            required_amount_raw=500_000,
        )
        assert allowed is False
        assert "Insufficient token balance" in reason

    def test_allows_exact_balance(self, risk_mgr: RiskManager):
        allowed, reason = risk_mgr.pre_sell_check(
            token_balance_raw=1000,
            required_amount_raw=1000,
        )
        assert allowed is True

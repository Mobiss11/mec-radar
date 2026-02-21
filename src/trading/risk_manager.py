"""Risk management — circuit breaker, balance enforcement, exposure limits.

Provides pre-trade safety checks and automatic trading pause on failures.
"""

from __future__ import annotations

import time
from decimal import Decimal

from loguru import logger


class TradingCircuitBreaker:
    """Pauses trading after consecutive failures. Auto-resets after cooldown."""

    def __init__(
        self,
        *,
        threshold: int = 3,
        cooldown_sec: int = 1800,
    ) -> None:
        self._threshold = threshold
        self._cooldown_sec = cooldown_sec
        self._consecutive_failures = 0
        self._tripped_at: float | None = None
        self._total_failures = 0

    @property
    def is_tripped(self) -> bool:
        """Check if circuit breaker is currently active."""
        if self._tripped_at is None:
            return False
        elapsed = time.monotonic() - self._tripped_at
        if elapsed >= self._cooldown_sec:
            # Auto-reset after cooldown
            self._tripped_at = None
            self._consecutive_failures = 0
            logger.info("[CIRCUIT] Circuit breaker reset after cooldown")
            return False
        return True

    @property
    def seconds_until_reset(self) -> float:
        """Seconds remaining until auto-reset. 0 if not tripped."""
        if self._tripped_at is None:
            return 0.0
        remaining = self._cooldown_sec - (time.monotonic() - self._tripped_at)
        return max(remaining, 0.0)

    @property
    def total_failures(self) -> int:
        return self._total_failures

    def record_success(self) -> None:
        """Record a successful trade. Resets consecutive failure counter."""
        self._consecutive_failures = 0

    def record_failure(self, error: str = "") -> None:
        """Record a failed trade. Trips breaker if threshold reached."""
        self._consecutive_failures += 1
        self._total_failures += 1
        logger.warning(
            f"[CIRCUIT] Trade failure #{self._consecutive_failures}/{self._threshold}: {error}"
        )
        if self._consecutive_failures >= self._threshold:
            self._tripped_at = time.monotonic()
            logger.warning(
                f"[CIRCUIT] TRIPPED — pausing trades for {self._cooldown_sec}s. "
                f"Total failures: {self._total_failures}"
            )


class RiskManager:
    """Pre-trade risk checks. All checks are synchronous (pure logic)."""

    def __init__(
        self,
        *,
        max_sol_per_trade: float,
        max_positions: int,
        max_total_exposure_sol: float,
        min_liquidity_usd: float,
        min_wallet_balance_sol: float = 0.05,
    ) -> None:
        self._max_sol_per_trade = Decimal(str(max_sol_per_trade))
        self._max_positions = max_positions
        self._max_exposure = Decimal(str(max_total_exposure_sol))
        self._min_liquidity = min_liquidity_usd
        self._min_balance = Decimal(str(min_wallet_balance_sol))

    def pre_buy_check(
        self,
        *,
        wallet_balance_sol: float,
        open_position_count: int,
        total_open_exposure_sol: float,
        invest_sol: float,
        liquidity_usd: float | None,
    ) -> tuple[bool, str]:
        """Check if a buy trade is allowed.

        Returns (allowed, reason). Reason is empty string if allowed.
        """
        bal = Decimal(str(wallet_balance_sol))
        invest = Decimal(str(invest_sol))
        exposure = Decimal(str(total_open_exposure_sol))

        # Wallet balance must cover trade + reserve for fees
        if bal < invest + self._min_balance:
            return (
                False,
                f"Insufficient balance: {bal:.4f} SOL < {invest:.4f} + {self._min_balance} reserve",
            )

        # Trade size limit
        if invest > self._max_sol_per_trade * Decimal("1.6"):
            # 1.6x allows strong_buy 1.5x multiplier + small buffer
            return False, f"Trade size {invest:.4f} exceeds max {self._max_sol_per_trade * Decimal('1.6')}"

        # Position count limit
        if open_position_count >= self._max_positions:
            return (
                False,
                f"Max positions reached: {open_position_count}/{self._max_positions}",
            )

        # Total exposure cap
        if exposure + invest > self._max_exposure:
            return (
                False,
                f"Total exposure {exposure + invest:.4f} exceeds max {self._max_exposure}",
            )

        # Minimum liquidity (protect against extreme slippage)
        if liquidity_usd is not None and liquidity_usd < self._min_liquidity:
            return (
                False,
                f"Liquidity ${liquidity_usd:.0f} < min ${self._min_liquidity:.0f}",
            )

        return True, ""

    def pre_sell_check(
        self,
        *,
        token_balance_raw: int,
        required_amount_raw: int,
    ) -> tuple[bool, str]:
        """Check if a sell trade is allowed (have enough tokens)."""
        if token_balance_raw < required_amount_raw:
            return (
                False,
                f"Insufficient token balance: {token_balance_raw} < {required_amount_raw}",
            )
        return True, ""

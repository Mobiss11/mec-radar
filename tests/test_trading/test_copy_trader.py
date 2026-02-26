"""Tests for CopyTrader — copy trading engine (Phase 57).

Covers:
- Helius SWAP parsing (buy/sell detection)
- Paper position creation (source=copy_trade, copied_from_wallet)
- Wallet multiplier + max_sol_per_trade cap
- Sell mirroring (closes only copy_trade positions from same wallet)
- Redis dedup (same sig processed once)
- Max positions limit
- Token auto-creation for FK constraint
- Close conditions (reuse shared logic)
- Stats tracking
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.trading.copy_trader import CopySwap, CopyTrader

# ── Fixtures ──────────────────────────────────────────────────────────────

def _make_helius_tx(
    *,
    signature: str = "sig123abc",
    tx_type: str = "SWAP",
    source: str = "JUPITER",
    fee_payer: str = "WalletABC",
    fee: int = 5000,
    native_transfers: list | None = None,
    token_transfers: list | None = None,
    transaction_error: str = "",
    timestamp: int = 1709000000,
) -> SimpleNamespace:
    """Build a mock Helius transaction for testing."""
    if native_transfers is None:
        native_transfers = []
    if token_transfers is None:
        token_transfers = []
    return SimpleNamespace(
        signature=signature,
        type=tx_type,
        source=source,
        fee_payer=fee_payer,
        fee=fee,
        native_transfers=native_transfers,
        token_transfers=token_transfers,
        transaction_error=transaction_error,
        timestamp=timestamp,
    )


def _native_xfer(from_addr: str, to_addr: str, amount: int) -> SimpleNamespace:
    """Native SOL transfer."""
    return SimpleNamespace(from_user_account=from_addr, to_user_account=to_addr, amount=amount)


def _token_xfer(
    from_addr: str, to_addr: str, mint: str, token_amount: float,
) -> SimpleNamespace:
    """SPL token transfer."""
    return SimpleNamespace(
        from_user_account=from_addr,
        to_user_account=to_addr,
        mint=mint,
        token_amount=Decimal(str(token_amount)),
    )


_WALLET = "A3WySdFfsNLNyRQABzfV5wAo1Y9fo2Kgrmuug7fTfBxL"
_WALLET_CONFIG = {
    "label": "GMGN#4 WR93.7%",
    "multiplier": 1.0,
    "max_sol_per_trade": 0.05,
    "enabled": True,
}
_TOKEN_MINT = "TokenMint123456789012345678901234"
_SOL_MINT = "So11111111111111111111111111111111111111112"


def _make_trader(
    helius_mock: AsyncMock | None = None,
    redis_mock: AsyncMock | None = None,
    alert_mock: AsyncMock | None = None,
    **kwargs: Any,
) -> CopyTrader:
    """Create CopyTrader with mocked dependencies."""
    return CopyTrader(
        helius=helius_mock or AsyncMock(),
        alert_dispatcher=alert_mock,
        redis=redis_mock,
        max_positions=kwargs.get("max_positions", 20),
        default_sol_per_trade=kwargs.get("default_sol_per_trade", 0.05),
        min_sol_amount=kwargs.get("min_sol_amount", 0.01),
        sell_mirror=kwargs.get("sell_mirror", True),
        dedup_ttl_sec=kwargs.get("dedup_ttl_sec", 300),
    )


# ── Swap parsing tests ───────────────────────────────────────────────────


class TestParseSwap:
    """Test CopyTrader._parse_swap() — Helius SWAP → CopySwap."""

    def test_buy_detection(self) -> None:
        """SOL out + token received = BUY."""
        trader = _make_trader()
        tx = _make_helius_tx(
            fee_payer=_WALLET,
            fee=5000,
            native_transfers=[
                _native_xfer(_WALLET, "PoolABC", 100_000_000),  # 0.1 SOL
            ],
            token_transfers=[
                _token_xfer("PoolABC", _WALLET, _TOKEN_MINT, 1_000_000),
            ],
        )
        swap = trader._parse_swap(_WALLET, _WALLET_CONFIG, tx)
        assert swap is not None
        assert swap.side == "buy"
        assert swap.token_mint == _TOKEN_MINT
        assert swap.sol_amount > Decimal("0")
        assert swap.wallet_address == _WALLET

    def test_sell_detection(self) -> None:
        """Token sent + net SOL in = SELL."""
        trader = _make_trader()
        tx = _make_helius_tx(
            fee_payer=_WALLET,
            fee=5000,
            native_transfers=[
                _native_xfer("PoolABC", _WALLET, 200_000_000),  # 0.2 SOL received
            ],
            token_transfers=[
                _token_xfer(_WALLET, "PoolABC", _TOKEN_MINT, 500_000),
            ],
        )
        swap = trader._parse_swap(_WALLET, _WALLET_CONFIG, tx)
        assert swap is not None
        assert swap.side == "sell"
        assert swap.sol_amount > Decimal("0")

    def test_skip_below_min_sol(self) -> None:
        """Ignore swaps smaller than min_sol_amount."""
        trader = _make_trader(min_sol_amount=0.1)
        tx = _make_helius_tx(
            fee_payer=_WALLET,
            fee=5000,
            native_transfers=[
                _native_xfer(_WALLET, "PoolABC", 10_000_000),  # 0.01 SOL
            ],
            token_transfers=[
                _token_xfer("PoolABC", _WALLET, _TOKEN_MINT, 100),
            ],
        )
        swap = trader._parse_swap(_WALLET, _WALLET_CONFIG, tx)
        assert swap is None

    def test_sol_mint_excluded(self) -> None:
        """Wrapped SOL transfers should not count as token buys."""
        trader = _make_trader()
        tx = _make_helius_tx(
            fee_payer=_WALLET,
            fee=5000,
            native_transfers=[
                _native_xfer(_WALLET, "PoolABC", 50_000_000),
            ],
            token_transfers=[
                # Only wrapped SOL, no real token
                _token_xfer("PoolABC", _WALLET, _SOL_MINT, 0.05),
            ],
        )
        swap = trader._parse_swap(_WALLET, _WALLET_CONFIG, tx)
        assert swap is None

    def test_label_from_config(self) -> None:
        """Wallet label should come from config."""
        trader = _make_trader()
        tx = _make_helius_tx(
            fee_payer=_WALLET,
            fee=5000,
            native_transfers=[
                _native_xfer(_WALLET, "PoolABC", 100_000_000),
            ],
            token_transfers=[
                _token_xfer("PoolABC", _WALLET, _TOKEN_MINT, 1_000_000),
            ],
        )
        swap = trader._parse_swap(_WALLET, _WALLET_CONFIG, tx)
        assert swap is not None
        assert swap.wallet_label == "GMGN#4 WR93.7%"

    def test_source_dex_from_tx(self) -> None:
        """Source DEX should come from Helius transaction."""
        trader = _make_trader()
        tx = _make_helius_tx(
            fee_payer=_WALLET,
            fee=5000,
            source="RAYDIUM",
            native_transfers=[
                _native_xfer(_WALLET, "PoolABC", 100_000_000),
            ],
            token_transfers=[
                _token_xfer("PoolABC", _WALLET, _TOKEN_MINT, 1_000_000),
            ],
        )
        swap = trader._parse_swap(_WALLET, _WALLET_CONFIG, tx)
        assert swap is not None
        assert swap.source_dex == "RAYDIUM"


# ── Callback tests ────────────────────────────────────────────────────────


class TestOnCopySwapDetected:
    """Test CopyTrader.on_copy_swap_detected() — full pipeline."""

    @pytest.mark.asyncio
    async def test_skip_non_swap(self) -> None:
        """Non-SWAP transactions should be skipped."""
        helius = AsyncMock()
        helius.get_parsed_transactions = AsyncMock(return_value=[
            _make_helius_tx(tx_type="TRANSFER", fee_payer=_WALLET),
        ])
        trader = _make_trader(helius_mock=helius)
        session = AsyncMock()

        with patch.object(trader, "_get_tracked_wallets", return_value={
            _WALLET: _WALLET_CONFIG,
        }):
            await trader.on_copy_swap_detected(_WALLET, "sig1", session)

        assert trader._skipped_non_swap == 1

    @pytest.mark.asyncio
    async def test_skip_transaction_error(self) -> None:
        """Transactions with errors should be skipped."""
        helius = AsyncMock()
        helius.get_parsed_transactions = AsyncMock(return_value=[
            _make_helius_tx(
                fee_payer=_WALLET,
                transaction_error="InstructionError",
            ),
        ])
        trader = _make_trader(helius_mock=helius)
        session = AsyncMock()

        with patch.object(trader, "_get_tracked_wallets", return_value={
            _WALLET: _WALLET_CONFIG,
        }):
            await trader.on_copy_swap_detected(_WALLET, "sig2", session)

        # Should not count as parsed swap
        assert trader._swaps_parsed == 0

    @pytest.mark.asyncio
    async def test_skip_wrong_fee_payer(self) -> None:
        """Transactions where fee_payer != wallet should be skipped."""
        helius = AsyncMock()
        helius.get_parsed_transactions = AsyncMock(return_value=[
            _make_helius_tx(fee_payer="SomeOtherWallet"),
        ])
        trader = _make_trader(helius_mock=helius)
        session = AsyncMock()

        with patch.object(trader, "_get_tracked_wallets", return_value={
            _WALLET: _WALLET_CONFIG,
        }):
            await trader.on_copy_swap_detected(_WALLET, "sig3", session)

        assert trader._swaps_parsed == 0

    @pytest.mark.asyncio
    async def test_redis_dedup_blocks_duplicate(self) -> None:
        """Second call with same signature should be skipped via Redis."""
        redis = AsyncMock()
        # First call: NX succeeds
        redis.set = AsyncMock(side_effect=[True, None])

        helius = AsyncMock()
        helius.get_parsed_transactions = AsyncMock(return_value=[
            _make_helius_tx(fee_payer=_WALLET),
        ])
        trader = _make_trader(helius_mock=helius, redis_mock=redis)

        session = AsyncMock()
        with patch.object(trader, "_get_tracked_wallets", return_value={
            _WALLET: _WALLET_CONFIG,
        }):
            await trader.on_copy_swap_detected(_WALLET, "sig_dup", session)
            await trader.on_copy_swap_detected(_WALLET, "sig_dup", session)

        assert trader._skipped_dedup == 1

    @pytest.mark.asyncio
    async def test_disabled_wallet_skipped(self) -> None:
        """Disabled wallets should be skipped."""
        helius = AsyncMock()
        trader = _make_trader(helius_mock=helius)
        session = AsyncMock()

        with patch.object(trader, "_get_tracked_wallets", return_value={
            _WALLET: {**_WALLET_CONFIG, "enabled": False},
        }):
            await trader.on_copy_swap_detected(_WALLET, "sig4", session)

        # Helius should not be called for disabled wallet
        helius.get_parsed_transactions.assert_not_called()


# ── Buy handler tests ─────────────────────────────────────────────────────


class TestHandleBuy:
    """Test CopyTrader._handle_buy() — paper position creation."""

    @pytest.mark.asyncio
    async def test_create_paper_position(self, db_session) -> None:
        """Should create Trade + Position with source=copy_trade."""
        from src.models.token import Token

        # Create token first
        token = Token(address=_TOKEN_MINT, chain="sol", source="test")
        db_session.add(token)
        await db_session.flush()

        trader = _make_trader()

        swap = CopySwap(
            wallet_address=_WALLET,
            wallet_label="GMGN#4",
            signature="sig_buy1",
            side="buy",
            token_mint=_TOKEN_MINT,
            token_symbol="TEST",
            sol_amount=Decimal("0.1"),
            token_amount=Decimal("1000000"),
            source_dex="JUPITER",
            timestamp=1709000000,
        )
        config = {**_WALLET_CONFIG, "multiplier": 1.0, "max_sol_per_trade": 0.05}

        pos = await trader._handle_buy(db_session, swap, config, is_paper=True)
        assert pos is not None
        assert pos.source == "copy_trade"
        assert pos.copied_from_wallet == _WALLET
        assert pos.is_paper == 1
        assert pos.status == "open"
        assert pos.amount_sol_invested == Decimal("0.05")  # Capped by max_sol_per_trade

    @pytest.mark.asyncio
    async def test_multiplier_applied(self, db_session) -> None:
        """Multiplier should scale the investment."""
        from src.models.token import Token

        token = Token(address=_TOKEN_MINT, chain="sol", source="test")
        db_session.add(token)
        await db_session.flush()

        trader = _make_trader(default_sol_per_trade=1.0)

        swap = CopySwap(
            wallet_address=_WALLET,
            wallet_label="Test",
            signature="sig_mult",
            side="buy",
            token_mint=_TOKEN_MINT,
            token_symbol="TEST",
            sol_amount=Decimal("0.1"),
            token_amount=Decimal("1000000"),
            source_dex="JUPITER",
            timestamp=1709000000,
        )
        config = {"multiplier": 2.0, "max_sol_per_trade": 1.0, "enabled": True}

        pos = await trader._handle_buy(db_session, swap, config, is_paper=True)
        assert pos is not None
        # swap.sol * multiplier = 0.1 * 2.0 = 0.2, capped by max_sol_per_trade=1.0
        assert pos.amount_sol_invested == Decimal("0.2")

    @pytest.mark.asyncio
    async def test_max_positions_reached(self, db_session) -> None:
        """Should skip when max_positions is reached."""
        from src.models.token import Token
        from src.models.trade import Position

        token = Token(address=_TOKEN_MINT, chain="sol", source="test")
        db_session.add(token)
        await db_session.flush()

        # Create max positions
        trader = _make_trader(max_positions=1)

        existing = Position(
            token_id=token.id,
            token_address=_TOKEN_MINT,
            symbol="EXISTING",
            entry_price=Decimal("0.001"),
            current_price=Decimal("0.001"),
            amount_token=Decimal("1000"),
            amount_sol_invested=Decimal("0.05"),
            status="open",
            is_paper=1,
            source="copy_trade",
            copied_from_wallet=_WALLET,
        )
        db_session.add(existing)
        await db_session.flush()

        swap = CopySwap(
            wallet_address=_WALLET,
            wallet_label="Test",
            signature="sig_max",
            side="buy",
            token_mint="NewToken123456789012345678901234",
            token_symbol="NEW",
            sol_amount=Decimal("0.1"),
            token_amount=Decimal("1000000"),
            source_dex="JUPITER",
            timestamp=1709000000,
        )
        config = _WALLET_CONFIG

        pos = await trader._handle_buy(db_session, swap, config, is_paper=True)
        assert pos is None

    @pytest.mark.asyncio
    async def test_auto_create_token(self, db_session) -> None:
        """Should auto-create Token record if mint not in DB."""
        from sqlalchemy import select as sa_select

        from src.models.token import Token

        trader = _make_trader()
        new_mint = "BrandNewToken12345678901234567890"

        swap = CopySwap(
            wallet_address=_WALLET,
            wallet_label="Test",
            signature="sig_auto",
            side="buy",
            token_mint=new_mint,
            token_symbol="NEW",
            sol_amount=Decimal("0.1"),
            token_amount=Decimal("1000000"),
            source_dex="JUPITER",
            timestamp=1709000000,
        )
        config = _WALLET_CONFIG

        pos = await trader._handle_buy(db_session, swap, config, is_paper=True)
        assert pos is not None

        # Verify token was created
        result = await db_session.execute(
            sa_select(Token).where(Token.address == new_mint)
        )
        token = result.scalar_one_or_none()
        assert token is not None
        assert token.source == "copy_trade"


# ── Sell handler tests ─────────────────────────────────────────────────────


class TestHandleSell:
    """Test CopyTrader._handle_sell() — mirror sell."""

    @pytest.mark.asyncio
    async def test_mirror_sell_closes_position(self, db_session) -> None:
        """Sell should close open copy-trade positions for that token + wallet."""
        from src.models.token import Token
        from src.models.trade import Position

        token = Token(address=_TOKEN_MINT, chain="sol", source="test")
        db_session.add(token)
        await db_session.flush()

        pos = Position(
            token_id=token.id,
            token_address=_TOKEN_MINT,
            symbol="TEST",
            entry_price=Decimal("0.001"),
            current_price=Decimal("0.0012"),
            amount_token=Decimal("1000"),
            amount_sol_invested=Decimal("0.05"),
            pnl_pct=Decimal("20"),
            status="open",
            is_paper=1,
            source="copy_trade",
            copied_from_wallet=_WALLET,
        )
        db_session.add(pos)
        await db_session.flush()

        trader = _make_trader()
        swap = CopySwap(
            wallet_address=_WALLET,
            wallet_label="Test",
            signature="sig_sell",
            side="sell",
            token_mint=_TOKEN_MINT,
            token_symbol="TEST",
            sol_amount=Decimal("0.06"),
            token_amount=Decimal("1000"),
            source_dex="JUPITER",
            timestamp=1709000000,
        )

        await trader._handle_sell(db_session, swap)
        await db_session.flush()

        await db_session.expire_all()
        await db_session.refresh(pos)
        assert pos.status == "closed"
        assert pos.close_reason == "mirror_sell"
        assert trader._sells_mirrored == 1

    @pytest.mark.asyncio
    async def test_mirror_sell_ignores_signal_positions(self, db_session) -> None:
        """Sell should NOT close signal positions, only copy_trade."""
        from src.models.token import Token
        from src.models.trade import Position

        token = Token(address=_TOKEN_MINT, chain="sol", source="test")
        db_session.add(token)
        await db_session.flush()

        # Signal position (source="signal")
        signal_pos = Position(
            token_id=token.id,
            token_address=_TOKEN_MINT,
            symbol="TEST",
            entry_price=Decimal("0.001"),
            current_price=Decimal("0.001"),
            amount_token=Decimal("1000"),
            amount_sol_invested=Decimal("0.5"),
            status="open",
            is_paper=1,
            source="signal",
        )
        db_session.add(signal_pos)
        await db_session.flush()

        trader = _make_trader()
        swap = CopySwap(
            wallet_address=_WALLET,
            wallet_label="Test",
            signature="sig_sell2",
            side="sell",
            token_mint=_TOKEN_MINT,
            token_symbol="TEST",
            sol_amount=Decimal("0.06"),
            token_amount=Decimal("1000"),
            source_dex="JUPITER",
            timestamp=1709000000,
        )

        await trader._handle_sell(db_session, swap)
        await db_session.flush()

        await db_session.refresh(signal_pos)
        assert signal_pos.status == "open"  # Untouched

    @pytest.mark.asyncio
    async def test_mirror_sell_only_same_wallet(self, db_session) -> None:
        """Sell should NOT close positions copied from a DIFFERENT wallet."""
        from src.models.token import Token
        from src.models.trade import Position

        token = Token(address=_TOKEN_MINT, chain="sol", source="test")
        db_session.add(token)
        await db_session.flush()

        other_wallet = "OtherWallet123456789012345678901234"
        pos = Position(
            token_id=token.id,
            token_address=_TOKEN_MINT,
            symbol="TEST",
            entry_price=Decimal("0.001"),
            current_price=Decimal("0.001"),
            amount_token=Decimal("1000"),
            amount_sol_invested=Decimal("0.05"),
            status="open",
            is_paper=1,
            source="copy_trade",
            copied_from_wallet=other_wallet,
        )
        db_session.add(pos)
        await db_session.flush()

        trader = _make_trader()
        swap = CopySwap(
            wallet_address=_WALLET,  # Different wallet
            wallet_label="Test",
            signature="sig_sell3",
            side="sell",
            token_mint=_TOKEN_MINT,
            token_symbol="TEST",
            sol_amount=Decimal("0.06"),
            token_amount=Decimal("1000"),
            source_dex="JUPITER",
            timestamp=1709000000,
        )

        await trader._handle_sell(db_session, swap)
        await db_session.flush()

        await db_session.refresh(pos)
        assert pos.status == "open"  # Not closed — different wallet


# ── Position update tests ─────────────────────────────────────────────────


class TestUpdatePositions:
    """Test CopyTrader.update_positions() — price loop handler."""

    @pytest.mark.asyncio
    async def test_update_pnl_calculation(self, db_session) -> None:
        """Should update P&L when price changes."""
        from src.models.token import Token
        from src.models.trade import Position

        token = Token(address=_TOKEN_MINT, chain="sol", source="test")
        db_session.add(token)
        await db_session.flush()

        pos = Position(
            token_id=token.id,
            token_address=_TOKEN_MINT,
            symbol="TEST",
            entry_price=Decimal("0.001"),
            current_price=Decimal("0.001"),
            max_price=Decimal("0.001"),
            amount_token=Decimal("1000"),
            amount_sol_invested=Decimal("0.05"),
            pnl_pct=Decimal("0"),
            status="open",
            is_paper=1,
            source="copy_trade",
            copied_from_wallet=_WALLET,
        )
        db_session.add(pos)
        await db_session.flush()

        trader = _make_trader()
        await trader.update_positions(
            db_session, token.id, Decimal("0.0015"),
            sol_price_usd=150.0,
        )
        await db_session.flush()

        await db_session.refresh(pos)
        assert pos.current_price == Decimal("0.0015")
        assert pos.pnl_pct == Decimal("50")  # +50%

    @pytest.mark.asyncio
    async def test_take_profit_close(self, db_session) -> None:
        """Position should close when take profit is hit."""
        from src.models.token import Token
        from src.models.trade import Position

        token = Token(address=_TOKEN_MINT, chain="sol", source="test")
        db_session.add(token)
        await db_session.flush()

        pos = Position(
            token_id=token.id,
            token_address=_TOKEN_MINT,
            symbol="TEST",
            entry_price=Decimal("0.001"),
            current_price=Decimal("0.001"),
            max_price=Decimal("0.001"),
            amount_token=Decimal("1000"),
            amount_sol_invested=Decimal("0.05"),
            pnl_pct=Decimal("0"),
            status="open",
            is_paper=1,
            source="copy_trade",
            copied_from_wallet=_WALLET,
            opened_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10),
        )
        db_session.add(pos)
        await db_session.flush()

        trader = _make_trader(take_profit_x=1.5)
        # Price at 2x entry = above 1.5x TP
        await trader.update_positions(
            db_session, token.id, Decimal("0.002"),
            sol_price_usd=150.0,
        )
        await db_session.flush()

        await db_session.refresh(pos)
        assert pos.status == "closed"
        assert "take_profit" in (pos.close_reason or "")


# ── Sweep tests ───────────────────────────────────────────────────────────


class TestSweepStalePositions:
    """Test CopyTrader.sweep_stale_positions()."""

    @pytest.mark.asyncio
    async def test_sweep_old_positions(self, db_session) -> None:
        """Positions older than timeout should be swept."""
        from src.models.token import Token
        from src.models.trade import Position

        token = Token(address=_TOKEN_MINT, chain="sol", source="test")
        db_session.add(token)
        await db_session.flush()

        old_pos = Position(
            token_id=token.id,
            token_address=_TOKEN_MINT,
            symbol="OLD",
            entry_price=Decimal("0.001"),
            current_price=Decimal("0.001"),
            amount_token=Decimal("1000"),
            amount_sol_invested=Decimal("0.05"),
            status="open",
            is_paper=1,
            source="copy_trade",
            copied_from_wallet=_WALLET,
            opened_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=10),
        )
        db_session.add(old_pos)
        await db_session.flush()

        trader = _make_trader(timeout_hours=8)
        closed = await trader.sweep_stale_positions(db_session)
        await db_session.flush()

        assert closed == 1
        await db_session.refresh(old_pos)
        assert old_pos.status == "closed"
        assert old_pos.close_reason == "timeout"

    @pytest.mark.asyncio
    async def test_sweep_skips_fresh_positions(self, db_session) -> None:
        """Fresh positions should not be swept."""
        from src.models.token import Token
        from src.models.trade import Position

        token = Token(address=_TOKEN_MINT, chain="sol", source="test")
        db_session.add(token)
        await db_session.flush()

        fresh_pos = Position(
            token_id=token.id,
            token_address=_TOKEN_MINT,
            symbol="FRESH",
            entry_price=Decimal("0.001"),
            current_price=Decimal("0.001"),
            amount_token=Decimal("1000"),
            amount_sol_invested=Decimal("0.05"),
            status="open",
            is_paper=1,
            source="copy_trade",
            copied_from_wallet=_WALLET,
            opened_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1),
        )
        db_session.add(fresh_pos)
        await db_session.flush()

        trader = _make_trader(timeout_hours=8)
        closed = await trader.sweep_stale_positions(db_session)

        assert closed == 0
        await db_session.refresh(fresh_pos)
        assert fresh_pos.status == "open"


# ── Stats tests ───────────────────────────────────────────────────────────


class TestStats:
    """Test CopyTrader.stats property."""

    def test_initial_stats(self) -> None:
        """Stats should start at zero."""
        trader = _make_trader()
        s = trader.stats
        assert s["events_received"] == 0
        assert s["swaps_parsed"] == 0
        assert s["buys_opened"] == 0
        assert s["sells_mirrored"] == 0


# ── CopySwap dataclass tests ──────────────────────────────────────────────


class TestCopySwap:
    """Test CopySwap dataclass."""

    def test_create_buy(self) -> None:
        swap = CopySwap(
            wallet_address=_WALLET,
            wallet_label="Test",
            signature="sig1",
            side="buy",
            token_mint=_TOKEN_MINT,
            token_symbol="TEST",
            sol_amount=Decimal("0.1"),
            token_amount=Decimal("1000"),
            source_dex="JUPITER",
            timestamp=1709000000,
        )
        assert swap.side == "buy"
        assert swap.sol_amount == Decimal("0.1")

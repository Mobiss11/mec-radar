"""Tests for RugGuard — real-time LP removal detection.

Covers:
- Watched position tracking (refresh)
- LP removal event matching
- Emergency close flow (paper + real)
- Debounce (no duplicate closes)
- Unmatched mints (no false triggers)
- Stats tracking
- gRPC client Raydium filter
- Raydium transaction decoding
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.parsers.rug_guard import RugGuard


# ── Helpers ───────────────────────────────────────────────────────────


def _make_position(
    *,
    pos_id: int = 1,
    token_address: str = "RUGPULL111111111111111111111111111111111111",
    symbol: str = "TRUG",
    entry_price: Decimal = Decimal("0.001"),
    amount_sol_invested: Decimal = Decimal("0.1"),
    is_paper: int = 1,
    status: str = "open",
) -> SimpleNamespace:
    """Build a lightweight Position-like object for testing."""
    return SimpleNamespace(
        id=pos_id,
        token_id=pos_id * 10,
        token_address=token_address,
        symbol=symbol,
        entry_price=entry_price,
        current_price=entry_price,
        amount_token=Decimal("1000000"),
        amount_sol_invested=amount_sol_invested,
        pnl_pct=Decimal("0"),
        pnl_usd=Decimal("0"),
        max_price=entry_price,
        status=status,
        is_paper=is_paper,
        close_reason=None,
        closed_at=None,
        opened_at=datetime.now(UTC).replace(tzinfo=None),
    )


def _make_rug_guard(
    paper_trader: "PaperTrader | None" = None,
    real_trader: "RealTrader | None" = None,
    alert_dispatcher: "AlertDispatcher | None" = None,
) -> RugGuard:
    """Create RugGuard instance with optional mocked traders."""
    return RugGuard(
        paper_trader=paper_trader,
        real_trader=real_trader,
        alert_dispatcher=alert_dispatcher,
    )


def _mock_session_with_positions(positions: list) -> MagicMock:
    """Create a mock async_session_factory that returns given positions.

    Mocks both select(Position.id, Position.token_address) for refresh
    and select(Position).where() for emergency close.
    """
    mock_factory = MagicMock()
    mock_session = AsyncMock()

    # For refresh_watched_positions: select(Position.id, Position.token_address)
    mock_result = MagicMock()
    mock_result.all.return_value = [
        (p.id, p.token_address) for p in positions if p.status == "open"
    ]
    mock_session.execute = AsyncMock(return_value=mock_result)

    # Context manager
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    return mock_factory, mock_session


def _mock_session_for_close(position: SimpleNamespace) -> MagicMock:
    """Create a mock session that returns a specific position on query."""
    mock_factory = MagicMock()
    mock_session = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = position
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    return mock_factory, mock_session


# ── Test: Watched Position Refresh ────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_picks_up_open_positions() -> None:
    """refresh_watched_positions should find open positions in DB."""
    rg = _make_rug_guard()
    assert rg.watched_count == 0

    pos = _make_position()
    mock_factory, _ = _mock_session_with_positions([pos])

    with patch("src.parsers.rug_guard.async_session_factory", mock_factory):
        await rg.refresh_watched_positions()

    assert rg.watched_count == 1
    assert pos.token_address in rg._watched_mints
    assert pos.id in rg._watched_mints[pos.token_address]


@pytest.mark.asyncio
async def test_refresh_removes_closed_positions() -> None:
    """Closed positions should be removed from watch list."""
    rg = _make_rug_guard()

    pos = _make_position()
    mock_factory1, _ = _mock_session_with_positions([pos])

    with patch("src.parsers.rug_guard.async_session_factory", mock_factory1):
        await rg.refresh_watched_positions()
    assert rg.watched_count == 1

    # Now position is closed
    pos.status = "closed"
    mock_factory2, _ = _mock_session_with_positions([pos])

    with patch("src.parsers.rug_guard.async_session_factory", mock_factory2):
        await rg.refresh_watched_positions()
    assert rg.watched_count == 0


@pytest.mark.asyncio
async def test_refresh_multiple_positions_same_mint() -> None:
    """Multiple positions for same mint should be tracked together."""
    rg = _make_rug_guard()

    mint = "SAME_MINT_1111111111111111111111111111111111"
    pos1 = _make_position(pos_id=1, token_address=mint, is_paper=1)
    pos2 = _make_position(pos_id=2, token_address=mint, is_paper=0)
    mock_factory, _ = _mock_session_with_positions([pos1, pos2])

    with patch("src.parsers.rug_guard.async_session_factory", mock_factory):
        await rg.refresh_watched_positions()

    assert rg.watched_count == 1  # 1 unique mint
    assert len(rg._watched_mints[mint]) == 2  # 2 positions


# ── Test: LP Removal Event Handling ───────────────────────────────────


@pytest.mark.asyncio
async def test_lp_removal_unmatched_mint_ignored() -> None:
    """LP removal for a mint without open positions should be ignored."""
    rg = _make_rug_guard()
    await rg.on_lp_removal("UNKNOWN_MINT_ADDRESS", "sig123", 1_000_000_000, 0)

    assert rg._lp_events_received == 1
    assert rg._lp_events_matched == 0
    assert rg._positions_closed == 0


@pytest.mark.asyncio
async def test_lp_removal_matched_triggers_close() -> None:
    """LP removal for a watched mint should trigger emergency close."""
    pos = _make_position()
    rg = _make_rug_guard()
    rg._watched_mints = {pos.token_address: {pos.id}}

    mock_factory, mock_session = _mock_session_for_close(pos)

    with patch("src.parsers.rug_guard.async_session_factory", mock_factory):
        with patch("src.parsers.rug_guard.get_sol_price_safe", return_value=140.0):
            await rg.on_lp_removal(pos.token_address, "txsig123", 5_000_000_000, 0)
            await asyncio.sleep(0.3)

    assert rg._lp_events_matched == 1
    assert rg._positions_closed == 1
    assert pos.status == "closed"
    assert pos.close_reason == "rug_lp_removed"
    assert pos.pnl_pct == Decimal("-100")


@pytest.mark.asyncio
async def test_lp_removal_debounce_no_double_close() -> None:
    """Same position should not be closed twice."""
    pos = _make_position()
    rg = _make_rug_guard()
    rg._watched_mints = {pos.token_address: {pos.id}}

    # First close succeeds
    mock_factory1, _ = _mock_session_for_close(pos)

    with patch("src.parsers.rug_guard.async_session_factory", mock_factory1):
        with patch("src.parsers.rug_guard.get_sol_price_safe", return_value=140.0):
            await rg.on_lp_removal(pos.token_address, "txsig1", 5_000_000_000, 0)
            await asyncio.sleep(0.2)

    # Second call — should be debounced (pos.id already in recently_closed)
    mock_factory2, _ = _mock_session_for_close(pos)
    with patch("src.parsers.rug_guard.async_session_factory", mock_factory2):
        await rg.on_lp_removal(pos.token_address, "txsig2", 5_000_000_000, 0)
        await asyncio.sleep(0.2)

    assert rg._lp_events_matched == 2
    assert rg._positions_closed == 1  # Only 1 close, second debounced
    assert pos.id in rg._recently_closed


@pytest.mark.asyncio
async def test_lp_removal_multiple_positions() -> None:
    """LP removal for a mint with multiple positions should close all."""
    mint = "MULTI_111111111111111111111111111111111111111"
    pos1 = _make_position(pos_id=1, token_address=mint, is_paper=1)
    pos2 = _make_position(pos_id=2, token_address=mint, is_paper=0)

    rg = _make_rug_guard()
    rg._watched_mints = {mint: {pos1.id, pos2.id}}

    # We need _mock_session_for_close to return pos1 first, then pos2
    call_count = 0
    positions_by_call = [pos1, pos2]

    def make_mock():
        mock_factory = MagicMock()
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        async def mock_execute(*args, **kwargs):
            nonlocal call_count
            idx = min(call_count, len(positions_by_call) - 1)
            call_count += 1
            result = MagicMock()
            result.scalar_one_or_none.return_value = positions_by_call[idx]
            return result

        mock_session.execute = mock_execute
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
        return mock_factory

    mock_factory = make_mock()
    with patch("src.parsers.rug_guard.async_session_factory", mock_factory):
        with patch("src.parsers.rug_guard.get_sol_price_safe", return_value=140.0):
            await rg.on_lp_removal(mint, "txsig_multi", 10_000_000_000, 0)
            await asyncio.sleep(0.5)

    assert rg._lp_events_matched == 1
    assert rg._positions_closed == 2

    for pos in [pos1, pos2]:
        assert pos.status == "closed"
        assert pos.close_reason == "rug_lp_removed"


# ── Test: Paper Position Emergency Close ──────────────────────────────


@pytest.mark.asyncio
async def test_paper_close_sets_minus_100_pnl() -> None:
    """Paper position close should set -100% PnL and correct USD loss."""
    pos = _make_position(amount_sol_invested=Decimal("0.1"))
    rg = _make_rug_guard()
    sol_price = 150.0
    mock_session = AsyncMock()

    with patch("src.parsers.rug_guard.get_sol_price_safe", return_value=sol_price):
        await rg._close_paper_position(mock_session, pos, "TRUG")

    assert pos.status == "closed"
    assert pos.close_reason == "rug_lp_removed"
    assert pos.pnl_pct == Decimal("-100")
    assert pos.current_price == Decimal("0")
    # PnL USD: -(0.1 SOL) * 150 = -15.0
    expected_loss = -(Decimal("0.1") * Decimal("150.0"))
    assert pos.pnl_usd == expected_loss
    assert pos.closed_at is not None


@pytest.mark.asyncio
async def test_paper_close_zero_investment() -> None:
    """Paper close with zero SOL invested should still work."""
    pos = _make_position(amount_sol_invested=Decimal("0"))
    rg = _make_rug_guard()
    mock_session = AsyncMock()

    with patch("src.parsers.rug_guard.get_sol_price_safe", return_value=140.0):
        await rg._close_paper_position(mock_session, pos, "TEST")

    assert pos.status == "closed"
    assert pos.pnl_usd == Decimal("0")


# ── Test: Real Position Emergency Close ───────────────────────────────


@pytest.mark.asyncio
async def test_real_close_delegates_to_real_trader() -> None:
    """Real position close should first try RealTrader._execute_close."""
    pos = _make_position(is_paper=0)
    mock_real_trader = MagicMock()
    mock_real_trader._execute_close = AsyncMock(return_value=True)
    mock_session = AsyncMock()

    rg = _make_rug_guard(real_trader=mock_real_trader)
    await rg._close_real_position(mock_session, pos, "RRUG")

    mock_real_trader._execute_close.assert_awaited_once()
    call_kwargs = mock_real_trader._execute_close.call_args[1]
    assert call_kwargs["reason"] == "rug_lp_removed"
    assert call_kwargs["price"] == Decimal("0")
    assert call_kwargs["liquidity_usd"] == 0.0


@pytest.mark.asyncio
async def test_real_close_force_close_on_trader_failure() -> None:
    """If RealTrader._execute_close returns False, should force-close."""
    pos = _make_position(is_paper=0, amount_sol_invested=Decimal("0.5"))
    mock_real_trader = MagicMock()
    mock_real_trader._execute_close = AsyncMock(return_value=False)
    mock_session = AsyncMock()

    rg = _make_rug_guard(real_trader=mock_real_trader)

    with patch("src.parsers.rug_guard.get_sol_price_safe", return_value=140.0):
        await rg._close_real_position(mock_session, pos, "RRUG")

    assert pos.status == "closed"
    assert pos.close_reason == "rug_lp_removed"
    assert pos.pnl_pct == Decimal("-100")
    assert pos.pnl_usd == -(Decimal("0.5") * Decimal("140.0"))


@pytest.mark.asyncio
async def test_real_close_force_close_on_timeout() -> None:
    """If RealTrader takes too long, should force-close."""
    pos = _make_position(is_paper=0)
    mock_real_trader = MagicMock()

    async def slow_close(*args, **kwargs):
        await asyncio.sleep(60)
        return True

    mock_real_trader._execute_close = slow_close
    mock_session = AsyncMock()

    rg = _make_rug_guard(real_trader=mock_real_trader)

    with patch("src.parsers.rug_guard.get_sol_price_safe", return_value=140.0):
        await rg._close_real_position(mock_session, pos, "RRUG")

    assert pos.status == "closed"
    assert pos.close_reason == "rug_lp_removed"


@pytest.mark.asyncio
async def test_real_close_force_close_on_exception() -> None:
    """If RealTrader raises exception, should force-close."""
    pos = _make_position(is_paper=0)
    mock_real_trader = MagicMock()
    mock_real_trader._execute_close = AsyncMock(side_effect=RuntimeError("swap failed"))
    mock_session = AsyncMock()

    rg = _make_rug_guard(real_trader=mock_real_trader)

    with patch("src.parsers.rug_guard.get_sol_price_safe", return_value=140.0):
        await rg._close_real_position(mock_session, pos, "RRUG")

    assert pos.status == "closed"
    assert pos.pnl_pct == Decimal("-100")


@pytest.mark.asyncio
async def test_real_close_no_trader_force_close() -> None:
    """Without RealTrader, should force-close directly."""
    pos = _make_position(is_paper=0)
    rg = _make_rug_guard()  # No real_trader
    mock_session = AsyncMock()

    with patch("src.parsers.rug_guard.get_sol_price_safe", return_value=140.0):
        await rg._close_real_position(mock_session, pos, "RRUG")

    assert pos.status == "closed"
    assert pos.pnl_pct == Decimal("-100")


# ── Test: Stats ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stats_reflect_activity() -> None:
    """Stats should track LP events."""
    rg = _make_rug_guard()

    for i in range(3):
        await rg.on_lp_removal(f"UNKNWN_MINT_{i}", f"sig{i}", 1_000_000_000, 0)

    stats = rg.stats
    assert stats["lp_events_received"] == 3
    assert stats["lp_events_matched"] == 0
    assert stats["positions_closed"] == 0
    assert stats["watched_mints"] == 0


@pytest.mark.asyncio
async def test_stats_after_matched_close() -> None:
    """Stats should update after matched LP removal."""
    pos = _make_position()
    rg = _make_rug_guard()
    rg._watched_mints = {pos.token_address: {pos.id}}

    mock_factory, _ = _mock_session_for_close(pos)

    with patch("src.parsers.rug_guard.async_session_factory", mock_factory):
        with patch("src.parsers.rug_guard.get_sol_price_safe", return_value=140.0):
            await rg.on_lp_removal(pos.token_address, "sig1", 5e9, 0)
            await asyncio.sleep(0.3)

    stats = rg.stats
    assert stats["lp_events_received"] == 1
    assert stats["lp_events_matched"] == 1
    assert stats["positions_closed"] == 1


# ── Test: Alert Dispatch ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_alert_sent_on_close() -> None:
    """Alert should be sent when position is closed by RugGuard."""
    pos = _make_position(symbol="SCAM")
    mock_alerts = MagicMock()
    mock_alerts.send_real_error = AsyncMock()

    rg = RugGuard(
        paper_trader=None,
        real_trader=None,
        alert_dispatcher=mock_alerts,
    )
    rg._watched_mints = {pos.token_address: {pos.id}}

    mock_factory, _ = _mock_session_for_close(pos)

    with patch("src.parsers.rug_guard.async_session_factory", mock_factory):
        with patch("src.parsers.rug_guard.get_sol_price_safe", return_value=140.0):
            await rg.on_lp_removal(pos.token_address, "alert_txsig", 5_000_000_000, 0)
            await asyncio.sleep(0.3)

    mock_alerts.send_real_error.assert_awaited_once()
    alert_msg = mock_alerts.send_real_error.call_args[0][0]
    assert "RugGuard" in alert_msg
    assert "SCAM" in alert_msg
    assert "LP removal detected" in alert_msg


@pytest.mark.asyncio
async def test_alert_failure_doesnt_crash() -> None:
    """Alert send failure should not prevent position close."""
    pos = _make_position()
    mock_alerts = MagicMock()
    mock_alerts.send_real_error = AsyncMock(side_effect=RuntimeError("TG error"))

    rg = RugGuard(
        paper_trader=None, real_trader=None, alert_dispatcher=mock_alerts,
    )
    rg._watched_mints = {pos.token_address: {pos.id}}

    mock_factory, _ = _mock_session_for_close(pos)

    with patch("src.parsers.rug_guard.async_session_factory", mock_factory):
        with patch("src.parsers.rug_guard.get_sol_price_safe", return_value=140.0):
            await rg.on_lp_removal(pos.token_address, "sig1", 5e9, 0)
            await asyncio.sleep(0.3)

    # Position should still be closed despite alert failure
    assert pos.status == "closed"
    assert rg._positions_closed == 1


# ── Test: gRPC Client Integration ─────────────────────────────────────


def test_grpc_client_raydium_filter_added_when_callback_set() -> None:
    """gRPC subscribe request should include Raydium filter when on_lp_removal is set."""
    from src.parsers.chainstack.grpc_client import ChainstackGrpcClient

    client = ChainstackGrpcClient(endpoint="test.endpoint:443", token="test")

    # Without callback — no Raydium filter
    req = client._build_subscribe_request()
    assert "pump_txs" in req.transactions
    assert "raydium_amm_txs" not in req.transactions

    # With callback — Raydium filter added
    client.on_lp_removal = AsyncMock()
    req = client._build_subscribe_request()
    assert "pump_txs" in req.transactions
    assert "raydium_amm_txs" in req.transactions


def test_grpc_client_raydium_filter_includes_correct_program() -> None:
    """Raydium AMM filter should use the correct program ID."""
    from src.parsers.chainstack.grpc_client import (
        ChainstackGrpcClient,
        RAYDIUM_AMM_PROGRAM_ID,
    )

    client = ChainstackGrpcClient(endpoint="test:443", token="test")
    client.on_lp_removal = AsyncMock()
    req = client._build_subscribe_request()

    raydium_filter = req.transactions["raydium_amm_txs"]
    assert RAYDIUM_AMM_PROGRAM_ID in raydium_filter.account_include


# ── Test: Raydium Transaction Decoding ────────────────────────────────


@pytest.mark.asyncio
async def test_handle_raydium_tx_remove_liquidity() -> None:
    """_handle_raydium_tx should detect removeLiquidity and call callback."""
    import struct

    import base58

    from src.parsers.chainstack.grpc_client import (
        ChainstackGrpcClient,
        RAYDIUM_AMM_PROGRAM_ID,
        RAYDIUM_REMOVE_LIQUIDITY_IX,
    )

    client = ChainstackGrpcClient(endpoint="test:443", token="test")
    callback = AsyncMock()
    client.on_lp_removal = callback

    sol_mint = "So11111111111111111111111111111111111111112"
    token_mint = "RUGPULL111111111111111111111111111111111111"
    raydium_program = RAYDIUM_AMM_PROGRAM_ID

    # Account keys: [0:system, 1:raydium, 2:amm, ..., 7:coinMint(token), 8:pcMint(SOL)]
    account_keys = [
        base58.b58decode("11111111111111111111111111111111"),  # 0: System
        base58.b58decode(raydium_program),  # 1: Raydium AMM
        b"\x02" * 32,  # 2: amm
        b"\x03" * 32,  # 3: authority
        b"\x04" * 32,  # 4: openOrders
        b"\x05" * 32,  # 5: targetOrders
        b"\x06" * 32,  # 6: lpMint
        base58.b58decode(token_mint),  # 7: coinMint (the token)
        base58.b58decode(sol_mint),  # 8: pcMint (SOL)
        b"\x09" * 32,  # 9+: other accounts
        b"\x0a" * 32,
        b"\x0b" * 32,
        b"\x0c" * 32,
        b"\x0d" * 32,
        b"\x0e" * 32,
        b"\x0f" * 32,
    ]

    # removeLiquidity: ix_index=4, lp_amount=1B
    lp_amount = 1_000_000_000
    ix_data = bytes([RAYDIUM_REMOVE_LIQUIDITY_IX]) + struct.pack("<Q", lp_amount)

    # Mock instruction: program_id_index=1 (Raydium), accounts=[2..15]
    mock_ix = MagicMock()
    mock_ix.program_id_index = 1
    mock_ix.data = ix_data
    mock_ix.accounts = list(range(2, 16))

    mock_msg = MagicMock()
    mock_msg.instructions = [mock_ix]

    await client._handle_raydium_tx("test_signature", mock_msg, account_keys)

    callback.assert_awaited_once()
    call_args = callback.call_args[0]
    assert call_args[0] == token_mint  # mint
    assert call_args[1] == "test_signature"  # signature
    assert call_args[2] == lp_amount  # LP amount


@pytest.mark.asyncio
async def test_handle_raydium_tx_non_remove_ignored() -> None:
    """Non-removeLiquidity Raydium instructions should be ignored."""
    import base58

    from src.parsers.chainstack.grpc_client import (
        ChainstackGrpcClient,
        RAYDIUM_AMM_PROGRAM_ID,
    )

    client = ChainstackGrpcClient(endpoint="test:443", token="test")
    callback = AsyncMock()
    client.on_lp_removal = callback

    # Build instruction with ix index 9 (swap, not removeLiquidity)
    ix_data = bytes([9]) + b"\x00" * 16

    account_keys = [
        base58.b58decode(RAYDIUM_AMM_PROGRAM_ID),
    ]

    mock_ix = MagicMock()
    mock_ix.program_id_index = 0
    mock_ix.data = ix_data
    mock_ix.accounts = list(range(10))

    mock_msg = MagicMock()
    mock_msg.instructions = [mock_ix]

    await client._handle_raydium_tx("test_sig", mock_msg, account_keys)

    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_raydium_tx_sol_as_coin_mint() -> None:
    """When SOL is coinMint, should pick pcMint as the token."""
    import struct

    import base58

    from src.parsers.chainstack.grpc_client import (
        ChainstackGrpcClient,
        RAYDIUM_AMM_PROGRAM_ID,
        RAYDIUM_REMOVE_LIQUIDITY_IX,
    )

    client = ChainstackGrpcClient(endpoint="test:443", token="test")
    callback = AsyncMock()
    client.on_lp_removal = callback

    sol_mint = "So11111111111111111111111111111111111111112"
    token_mint = "TKNMint1234567891234567891234567891234567891"

    account_keys = [
        base58.b58decode(RAYDIUM_AMM_PROGRAM_ID),  # 0: program
        b"\x01" * 32,  # 1: amm
        b"\x02" * 32,  # 2: authority
        b"\x03" * 32,  # 3: openOrders
        b"\x04" * 32,  # 4: targetOrders
        b"\x05" * 32,  # 5: lpMint
        base58.b58decode(sol_mint),  # 6: coinMint = SOL
        base58.b58decode(token_mint),  # 7: pcMint = token
        b"\x08" * 32,
        b"\x09" * 32,
        b"\x0a" * 32,
        b"\x0b" * 32,
        b"\x0c" * 32,
        b"\x0d" * 32,
        b"\x0e" * 32,
        b"\x0f" * 32,
    ]

    lp_amount = 500_000_000
    ix_data = bytes([RAYDIUM_REMOVE_LIQUIDITY_IX]) + struct.pack("<Q", lp_amount)

    mock_ix = MagicMock()
    mock_ix.program_id_index = 0
    mock_ix.data = ix_data
    mock_ix.accounts = list(range(1, 16))

    mock_msg = MagicMock()
    mock_msg.instructions = [mock_ix]

    await client._handle_raydium_tx("test_sig2", mock_msg, account_keys)

    callback.assert_awaited_once()
    call_args = callback.call_args[0]
    assert call_args[0] == token_mint  # Should pick pcMint since coinMint is SOL


@pytest.mark.asyncio
async def test_handle_raydium_tx_short_data_ignored() -> None:
    """Instructions with too short data should be ignored."""
    import base58

    from src.parsers.chainstack.grpc_client import ChainstackGrpcClient, RAYDIUM_AMM_PROGRAM_ID

    client = ChainstackGrpcClient(endpoint="test:443", token="test")
    callback = AsyncMock()
    client.on_lp_removal = callback

    account_keys = [base58.b58decode(RAYDIUM_AMM_PROGRAM_ID)]

    mock_ix = MagicMock()
    mock_ix.program_id_index = 0
    mock_ix.data = b""  # Empty data
    mock_ix.accounts = list(range(10))

    mock_msg = MagicMock()
    mock_msg.instructions = [mock_ix]

    await client._handle_raydium_tx("sig", mock_msg, account_keys)

    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_raydium_tx_too_few_accounts_ignored() -> None:
    """Instructions with too few accounts should be ignored."""
    import base58

    from src.parsers.chainstack.grpc_client import (
        ChainstackGrpcClient,
        RAYDIUM_AMM_PROGRAM_ID,
        RAYDIUM_REMOVE_LIQUIDITY_IX,
    )

    client = ChainstackGrpcClient(endpoint="test:443", token="test")
    callback = AsyncMock()
    client.on_lp_removal = callback

    account_keys = [base58.b58decode(RAYDIUM_AMM_PROGRAM_ID)]

    ix_data = bytes([RAYDIUM_REMOVE_LIQUIDITY_IX]) + b"\x00" * 8

    mock_ix = MagicMock()
    mock_ix.program_id_index = 0
    mock_ix.data = ix_data
    mock_ix.accounts = [0, 0, 0]  # Only 3 accounts, need 7+

    mock_msg = MagicMock()
    mock_msg.instructions = [mock_ix]

    await client._handle_raydium_tx("sig", mock_msg, account_keys)

    callback.assert_not_awaited()


# ── Test: Emergency Close Error Handling ──────────────────────────────


@pytest.mark.asyncio
async def test_emergency_close_position_not_found() -> None:
    """If position was already closed, should handle gracefully."""
    rg = _make_rug_guard()
    mint = "GONE_MINT_11111111111111111111111111111111111"
    rg._watched_mints = {mint: {999}}  # Non-existent position ID

    mock_factory = MagicMock()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None  # Position not found
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("src.parsers.rug_guard.async_session_factory", mock_factory):
        await rg._emergency_close(999, mint, "sig", 5.0)

    # Should not crash, no position closed
    assert rg._positions_closed == 0
    # Should be removed from recently_closed since position doesn't exist
    assert 999 not in rg._recently_closed

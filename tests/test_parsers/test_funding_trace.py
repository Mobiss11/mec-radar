"""Tests for multi-hop funding trace — creator funding chain analysis."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.parsers.funding_trace import (
    FundingTrace,
    trace_creator_funding,
)
from src.parsers.helius.models import (
    HeliusNativeTransfer,
    HeliusSignature,
    HeliusTransaction,
)


CREATOR = "Creator111"
HOP1 = "Hop1_Funder"
HOP2 = "Hop2_Intermediary"
HOP3 = "Hop3_Origin"


def _make_sigs(count: int, oldest_timestamp: int = 1000000) -> list[HeliusSignature]:
    """Create signature list with age simulation."""
    return [
        HeliusSignature(
            signature=f"sig{i}",
            slot=100 + i,
            timestamp=oldest_timestamp + (count - i) * 100,
        )
        for i in range(count)
    ]


def _make_funding_tx(from_addr: str, to_addr: str, amount: int = 100_000_000) -> HeliusTransaction:
    """Create a transaction with SOL transfer."""
    return HeliusTransaction(
        signature=f"tx_{from_addr[:6]}_{to_addr[:6]}",
        type="TRANSFER",
        fee=5000,
        fee_payer=from_addr,
        timestamp=1000000,
        native_transfers=[
            HeliusNativeTransfer(
                from_user_account=from_addr,
                to_user_account=to_addr,
                amount=amount,
            )
        ],
    )


def _mock_session_no_rugger() -> AsyncMock:
    """Mock DB session that returns no known ruggers."""
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result
    return session


class TestMultiHopFunding:
    @pytest.mark.asyncio
    async def test_three_hop_rugger_chain(self) -> None:
        """Rugger found at hop 3 — should return risk=90."""
        helius = AsyncMock()
        session = AsyncMock()

        # Mock: Creator ← Hop1 ← Hop2 ← Hop3 (rugger)
        call_count = 0

        async def mock_get_sigs(address: str, *, limit: int = 50, before: str = "") -> list:
            return _make_sigs(10)

        async def mock_get_txs(signatures: list) -> list:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [_make_funding_tx(HOP1, CREATOR)]
            if call_count == 2:
                return [_make_funding_tx(HOP2, HOP1)]
            return [_make_funding_tx(HOP3, HOP2)]

        helius.get_signatures_for_address = AsyncMock(side_effect=mock_get_sigs)
        helius.get_parsed_transactions = AsyncMock(side_effect=mock_get_txs)

        # Hop1, Hop2 not ruggers; Hop3 IS a rugger
        db_call_count = 0

        async def mock_execute(stmt):
            nonlocal db_call_count
            db_call_count += 1
            mock_result = MagicMock()
            if db_call_count == 3:  # 3rd check = Hop3
                mock_profile = MagicMock()
                mock_profile.risk_score = 80
                mock_result.scalar_one_or_none.return_value = mock_profile
            else:
                mock_result.scalar_one_or_none.return_value = None
            return mock_result

        session.execute = AsyncMock(side_effect=mock_execute)

        result = await trace_creator_funding(session, helius, CREATOR, max_hops=3)

        assert result is not None
        assert result.funding_risk == 90
        assert "rugger_found_at_hop_3" in result.reason
        assert len(result.hops) == 3

    @pytest.mark.asyncio
    async def test_single_hop_still_works(self) -> None:
        """Backward compat: 1-hop analysis works as before."""
        helius = AsyncMock()
        session = _mock_session_no_rugger()

        helius.get_signatures_for_address = AsyncMock(
            return_value=_make_sigs(10, oldest_timestamp=1000000)
        )
        helius.get_parsed_transactions = AsyncMock(
            return_value=[_make_funding_tx(HOP1, CREATOR)]
        )

        result = await trace_creator_funding(session, helius, CREATOR, max_hops=1)

        assert result is not None
        assert result.chain_depth == 1
        assert result.funder == HOP1
        assert len(result.hops) == 1

    @pytest.mark.asyncio
    async def test_deep_fresh_chain(self) -> None:
        """All hops through <24h wallets = coordinated attack."""
        helius = AsyncMock()
        session = _mock_session_no_rugger()

        import time
        now = int(time.time())

        # All wallets are < 1 hour old
        async def mock_get_sigs(address: str, *, limit: int = 50, before: str = "") -> list:
            return [
                HeliusSignature(
                    signature=f"sig_{address[:6]}",
                    slot=100,
                    timestamp=now - 1800,  # 30 minutes ago
                )
            ]

        call_count = 0

        async def mock_get_txs(signatures: list) -> list:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [_make_funding_tx(HOP1, CREATOR)]
            if call_count == 2:
                return [_make_funding_tx(HOP2, HOP1)]
            if call_count == 3:
                return [_make_funding_tx(HOP3, HOP2)]
            return []

        helius.get_signatures_for_address = AsyncMock(side_effect=mock_get_sigs)
        helius.get_parsed_transactions = AsyncMock(side_effect=mock_get_txs)

        result = await trace_creator_funding(session, helius, CREATOR, max_hops=3)

        assert result is not None
        assert result.funding_risk >= 60
        assert "fresh" in result.reason or "new" in result.reason
        assert result.chain_depth >= 2

    @pytest.mark.asyncio
    async def test_no_trace(self) -> None:
        """No funder found — moderate risk."""
        helius = AsyncMock()
        session = _mock_session_no_rugger()

        helius.get_signatures_for_address = AsyncMock(return_value=_make_sigs(5))
        helius.get_parsed_transactions = AsyncMock(return_value=[
            HeliusTransaction(
                signature="sig0", type="SWAP", fee=5000,
                fee_payer=CREATOR, timestamp=1000000,
                native_transfers=[],  # No SOL inflows
            )
        ])

        result = await trace_creator_funding(session, helius, CREATOR)

        assert result is not None
        assert result.funder is None
        assert result.reason == "no_sol_funder_found"

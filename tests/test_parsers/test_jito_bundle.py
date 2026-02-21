"""Tests for Jito bundle snipe detection."""

import pytest
from unittest.mock import AsyncMock

from src.parsers.helius.models import HeliusNativeTransfer, HeliusSignature, HeliusTransaction
from src.parsers.jito_bundle import (
    JITO_TIP_ACCOUNTS,
    JitoBundleResult,
    _find_jito_tip,
    detect_jito_bundle,
)


def _make_helius() -> AsyncMock:
    helius = AsyncMock()
    helius.get_signatures_for_address = AsyncMock(return_value=[])
    helius.get_parsed_transactions = AsyncMock(return_value=[])
    return helius


def _make_sig(sig: str, slot: int) -> HeliusSignature:
    return HeliusSignature(signature=sig, slot=slot)


def _make_tx(
    fee_payer: str,
    native_transfers: list[HeliusNativeTransfer] | None = None,
) -> HeliusTransaction:
    return HeliusTransaction(
        signature="sig_test",
        fee_payer=fee_payer,
        native_transfers=native_transfers or [],
    )


class TestJitoBundleConstants:
    def test_tip_accounts_count(self) -> None:
        """Must have exactly 8 tip accounts."""
        assert len(JITO_TIP_ACCOUNTS) == 8

    def test_tip_accounts_are_base58(self) -> None:
        """All tip accounts should be valid base58 strings."""
        import string
        base58_chars = set(string.digits + string.ascii_letters) - {"0", "O", "I", "l"}
        for addr in JITO_TIP_ACCOUNTS:
            assert len(addr) >= 32
            assert all(c in base58_chars for c in addr)


class TestJitoBundleResult:
    def test_no_detection_zero_impact(self) -> None:
        result = JitoBundleResult()
        assert result.score_impact == 0
        assert result.risk_boost == 0

    def test_single_sniper_impact(self) -> None:
        result = JitoBundleResult(
            jito_bundle_detected=True,
            sniper_count=1,
            sniper_wallets=["wallet1"],
        )
        assert result.score_impact == -10
        assert result.risk_boost == 10

    def test_multiple_snipers_impact(self) -> None:
        result = JitoBundleResult(
            jito_bundle_detected=True,
            sniper_count=3,
            sniper_wallets=["w1", "w2", "w3"],
        )
        assert result.score_impact == -15
        assert result.risk_boost == 15


class TestFindJitoTip:
    def test_no_transfers_returns_none(self) -> None:
        tx = _make_tx("payer")
        assert _find_jito_tip(tx) is None

    def test_non_jito_transfer_returns_none(self) -> None:
        tx = _make_tx("payer", [
            HeliusNativeTransfer(
                from_user_account="payer",
                to_user_account="random_wallet",
                amount=100_000_000,
            ),
        ])
        assert _find_jito_tip(tx) is None

    def test_jito_tip_found(self) -> None:
        tip_acct = list(JITO_TIP_ACCOUNTS)[0]
        tx = _make_tx("payer", [
            HeliusNativeTransfer(
                from_user_account="payer",
                to_user_account=tip_acct,
                amount=10_000_000,  # 0.01 SOL
            ),
        ])
        result = _find_jito_tip(tx)
        assert result is not None
        assert result[0] == tip_acct
        assert abs(result[1] - 0.01) < 0.001

    def test_zero_amount_ignored(self) -> None:
        tip_acct = list(JITO_TIP_ACCOUNTS)[0]
        tx = _make_tx("payer", [
            HeliusNativeTransfer(
                from_user_account="payer",
                to_user_account=tip_acct,
                amount=0,
            ),
        ])
        assert _find_jito_tip(tx) is None


class TestDetectJitoBundle:
    @pytest.mark.asyncio
    async def test_no_signatures_returns_empty(self) -> None:
        helius = _make_helius()
        result = await detect_jito_bundle(helius, "token123")
        assert not result.jito_bundle_detected
        assert result.sniper_count == 0

    @pytest.mark.asyncio
    async def test_no_jito_tip_returns_empty(self) -> None:
        """Transactions without Jito tip accounts are not flagged."""
        helius = _make_helius()
        helius.get_signatures_for_address.return_value = [
            _make_sig("sig1", slot=100),
            _make_sig("sig2", slot=100),
        ]
        helius.get_parsed_transactions.return_value = [
            _make_tx("wallet1"),
            _make_tx("wallet2"),
        ]
        result = await detect_jito_bundle(helius, "token123")
        assert not result.jito_bundle_detected

    @pytest.mark.asyncio
    async def test_jito_tip_detected(self) -> None:
        """Transaction with Jito tip account is detected as bundle."""
        tip_acct = list(JITO_TIP_ACCOUNTS)[0]
        helius = _make_helius()
        helius.get_signatures_for_address.return_value = [
            _make_sig("sig1", slot=100),
        ]
        helius.get_parsed_transactions.return_value = [
            _make_tx("sniper_wallet", [
                HeliusNativeTransfer(
                    from_user_account="sniper_wallet",
                    to_user_account=tip_acct,
                    amount=10_000_000,
                ),
            ]),
        ]
        result = await detect_jito_bundle(helius, "token_mint")
        assert result.jito_bundle_detected
        assert result.sniper_count == 1
        assert result.sniper_wallets == ["sniper_wallet"]
        assert result.tip_account_used == tip_acct
        assert result.tip_amount_sol is not None

    @pytest.mark.asyncio
    async def test_creator_self_snipe(self) -> None:
        """Creator sniping their own token is detected."""
        tip_acct = list(JITO_TIP_ACCOUNTS)[1]
        helius = _make_helius()
        helius.get_signatures_for_address.return_value = [
            _make_sig("sig1", slot=200),
        ]
        helius.get_parsed_transactions.return_value = [
            _make_tx("creator_addr", [
                HeliusNativeTransfer(
                    from_user_account="creator_addr",
                    to_user_account=tip_acct,
                    amount=5_000_000,
                ),
            ]),
        ]
        result = await detect_jito_bundle(
            helius, "mint_addr", creator_address="creator_addr"
        )
        assert result.jito_bundle_detected
        assert "creator_addr" in result.sniper_wallets

    @pytest.mark.asyncio
    async def test_transactions_beyond_slot_plus1_ignored(self) -> None:
        """Only check creation slot and slot+1."""
        helius = _make_helius()
        helius.get_signatures_for_address.return_value = [
            _make_sig("sig_later", slot=105),  # slot+5, ignored
            _make_sig("sig_creation", slot=100),  # creation slot
        ]
        # Only creation slot sig should be fetched
        helius.get_parsed_transactions.return_value = [
            _make_tx("wallet1"),  # No Jito tip
        ]
        result = await detect_jito_bundle(helius, "token_mint")
        assert not result.jito_bundle_detected
        # Verify only 1 sig was sent (the creation slot one)
        call_args = helius.get_parsed_transactions.call_args
        assert len(call_args[0][0]) == 1

    @pytest.mark.asyncio
    async def test_helius_error_handled(self) -> None:
        """Helius errors are caught gracefully."""
        helius = _make_helius()
        helius.get_signatures_for_address.side_effect = Exception("API error")
        result = await detect_jito_bundle(helius, "token123")
        assert not result.jito_bundle_detected

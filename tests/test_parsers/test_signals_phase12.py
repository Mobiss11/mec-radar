"""Tests for Phase 12 signal rules R23-R28."""

from unittest.mock import MagicMock

import pytest

from src.parsers.jupiter.models import SellSimResult
from src.parsers.mint_parser import MintInfo
from src.parsers.signals import evaluate_signals


def _make_snapshot(**overrides):
    snap = MagicMock()
    snap.liquidity_usd = 50000
    snap.dex_liquidity_usd = None
    snap.holders_count = 100
    snap.market_cap = 100000
    snap.score = 40
    snap.volume_1h = 10000
    snap.dex_volume_1h = None
    snap.volume_5m = 2000
    snap.dex_volume_5m = None
    snap.volume_24h = None
    snap.buys_1h = 50
    snap.sells_1h = 20
    snap.buys_5m = 10
    snap.sells_5m = 5
    snap.buys_24h = None
    snap.sells_24h = None
    snap.smart_wallets_count = 0
    snap.top10_holders_pct = None
    snap.price = 0.001
    snap.volatility_5m = None
    snap.lp_removed_pct = None
    for k, v in overrides.items():
        setattr(snap, k, v)
    return snap


def _make_security(**overrides):
    sec = MagicMock()
    sec.is_honeypot = False
    sec.lp_burned = False
    sec.lp_locked = False
    sec.contract_renounced = True
    sec.sell_tax = None
    sec.buy_tax = None
    for k, v in overrides.items():
        setattr(sec, k, v)
    return sec


class TestR23Token2022Danger:
    def test_fires_on_dangerous_extensions(self) -> None:
        mint = MintInfo(dangerous_extensions=["PERMANENT_DELEGATE"])
        result = evaluate_signals(_make_snapshot(), _make_security(), mint_info=mint)
        names = [r.name for r in result.rules_fired]
        assert "token2022_danger" in names

    def test_no_fire_on_clean_mint(self) -> None:
        mint = MintInfo(dangerous_extensions=[])
        result = evaluate_signals(_make_snapshot(), _make_security(), mint_info=mint)
        names = [r.name for r in result.rules_fired]
        assert "token2022_danger" not in names


class TestR24SellSimFailed:
    def test_fires_on_failed_sell(self) -> None:
        sim = SellSimResult(sellable=False, error="No route found")
        result = evaluate_signals(_make_snapshot(), _make_security(), sell_sim_result=sim)
        names = [r.name for r in result.rules_fired]
        assert "sell_sim_failed" in names

    def test_no_fire_on_sellable(self) -> None:
        sim = SellSimResult(sellable=True)
        result = evaluate_signals(_make_snapshot(), _make_security(), sell_sim_result=sim)
        names = [r.name for r in result.rules_fired]
        assert "sell_sim_failed" not in names


class TestR25BundledBuy:
    def test_fires_on_bundled(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), bundled_buy_detected=True
        )
        names = [r.name for r in result.rules_fired]
        assert "bundled_buy" in names


class TestR26SerialDeployer:
    def test_fires_on_serial_scammer(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), pumpfun_dead_tokens=10
        )
        names = [r.name for r in result.rules_fired]
        assert "serial_deployer" in names

    def test_no_fire_on_low_count(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), pumpfun_dead_tokens=2
        )
        names = [r.name for r in result.rules_fired]
        assert "serial_deployer" not in names


class TestR27LpNotBurned:
    def test_fires_when_not_burned(self) -> None:
        sec = _make_security(lp_burned=False, lp_locked=False)
        result = evaluate_signals(
            _make_snapshot(), sec, raydium_lp_burned=False
        )
        names = [r.name for r in result.rules_fired]
        assert "lp_not_burned" in names


class TestR28GoPlusHoneypot:
    def test_fires_on_goplus_honeypot(self) -> None:
        result = evaluate_signals(
            _make_snapshot(), _make_security(), goplus_is_honeypot=True
        )
        names = [r.name for r in result.rules_fired]
        assert "goplus_honeypot" in names
        # Weight should be -10
        rule = next(r for r in result.rules_fired if r.name == "goplus_honeypot")
        assert rule.weight == -10

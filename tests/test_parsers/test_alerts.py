"""Tests for real-time alert dispatcher."""

import asyncio

import pytest

from src.parsers.alerts import AlertDispatcher, TokenAlert, _format_telegram_message


@pytest.fixture
def dispatcher():
    return AlertDispatcher(cooldown_sec=5)


def _make_alert(**kwargs) -> TokenAlert:
    defaults = {
        "token_address": "AbcDef123456789012345678901234567890abcd",
        "symbol": "TEST",
        "score": 55,
        "action": "buy",
        "reasons": {"high_score": "Score 55 >= 50", "buy_pressure": "Buy ratio 3.2x"},
        "price": 0.00123,
        "market_cap": 50000.0,
        "liquidity": 25000.0,
        "source": "pumpportal",
    }
    defaults.update(kwargs)
    return TokenAlert(**defaults)


@pytest.mark.asyncio
async def test_dispatch_logs_alert(dispatcher, capsys):
    """Dispatch should log the alert."""
    alert = _make_alert()
    await dispatcher.dispatch(alert)
    assert dispatcher.total_sent == 1


@pytest.mark.asyncio
async def test_dispatch_deduplicates(dispatcher):
    """Same token within cooldown should not send twice."""
    alert = _make_alert()
    await dispatcher.dispatch(alert)
    await dispatcher.dispatch(alert)
    assert dispatcher.total_sent == 1  # Second one deduplicated


@pytest.mark.asyncio
async def test_dispatch_different_tokens(dispatcher):
    """Different tokens should each get an alert."""
    alert1 = _make_alert(token_address="addr1_" + "0" * 35)
    alert2 = _make_alert(token_address="addr2_" + "0" * 35)
    await dispatcher.dispatch(alert1)
    await dispatcher.dispatch(alert2)
    assert dispatcher.total_sent == 2


def test_format_telegram_message():
    alert = _make_alert()
    msg = _format_telegram_message(alert)
    assert "BUY" in msg
    assert "TEST" in msg
    assert "55" in msg
    assert "$50,000" in msg
    assert "high_score" in msg or "Score 55" in msg


def test_format_telegram_strong_buy():
    alert = _make_alert(action="strong_buy")
    msg = _format_telegram_message(alert)
    assert "STRONG_BUY" in msg


@pytest.mark.asyncio
async def test_close(dispatcher):
    """Close should be safe to call multiple times."""
    await dispatcher.close()
    await dispatcher.close()

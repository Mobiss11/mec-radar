"""Tests for Rugcheck.xyz client and scoring integration."""

from unittest.mock import AsyncMock, patch

import pytest

from src.parsers.rugcheck.client import RugcheckClient
from src.parsers.rugcheck.models import RugcheckReport, RugcheckRisk


@pytest.mark.asyncio
async def test_parse_report_from_json():
    """RugcheckReport parses valid API response."""
    data = {
        "score": 45,
        "risks": [
            {"name": "Freeze Authority still enabled", "level": "danger", "score": 20},
            {"name": "Low Liquidity", "level": "warn", "score": 10},
        ],
        "mint": "So11111111111111111111111111111111",
        "tokenMeta": {"name": "TestToken", "symbol": "TST"},
    }
    report = RugcheckReport(
        score=data["score"],
        risks=[RugcheckRisk(**r) for r in data["risks"]],
        mint=data["mint"],
        token_name=data["tokenMeta"]["name"],
        token_symbol=data["tokenMeta"]["symbol"],
    )
    assert report.score == 45
    assert len(report.risks) == 2
    assert report.risks[0].name == "Freeze Authority still enabled"
    assert report.risks[0].level == "danger"


@pytest.mark.asyncio
async def test_client_returns_report():
    """Client correctly fetches and parses a report."""
    mock_response_data = {
        "score": 15,
        "risks": [{"name": "Low amount of LP Providers", "level": "info", "score": 5}],
        "tokenMeta": {"name": "SafeToken", "symbol": "SAFE"},
    }
    client = RugcheckClient(max_rps=100)
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: mock_response_data
    with patch.object(client, "_client") as mock_http:
        mock_http.get = AsyncMock(return_value=mock_resp)
        report = await client.get_token_report("TestMint123")

    assert report is not None
    assert report.score == 15
    assert len(report.risks) == 1
    await client.close()


def test_rugcheck_scoring_penalty_dangerous():
    """Rugcheck score >= 50 applies -15 penalty in scoring v2."""
    from decimal import Decimal

    from src.models.token import TokenSecurity, TokenSnapshot
    from src.parsers.scoring import compute_score

    snap = TokenSnapshot(
        token_id=1,
        liquidity_usd=Decimal("50000"),
        holders_count=100,
        volume_1h=Decimal("30000"),
    )
    sec = TokenSecurity(
        token_id=1, is_honeypot=False, lp_burned=True,
        contract_renounced=True, sell_tax=Decimal("0"),
        top10_holders_pct=Decimal("20"), is_mintable=False,
    )
    score_neutral = compute_score(snap, sec, rugcheck_score=5)
    score_danger = compute_score(snap, sec, rugcheck_score=60)
    # Phase 54: rc<10 is neutral (0), dangerous gets -15 => diff = 15
    assert score_neutral > score_danger
    assert score_neutral - score_danger == 15


def test_rugcheck_scoring_neutral_for_low_rc():
    """Phase 54: Rugcheck score < 10 gives NO bonus (neutral 0, not +5).

    rc=1 means "unanalyzed" (Token-2022), NOT "clean".
    All pump.fun Token-2022 tokens have rc=1 — both scams and profitable.
    """
    from decimal import Decimal

    from src.models.token import TokenSecurity, TokenSnapshot
    from src.parsers.scoring import compute_score

    snap = TokenSnapshot(
        token_id=1,
        liquidity_usd=Decimal("50000"),
        holders_count=100,
        volume_1h=Decimal("30000"),
    )
    sec = TokenSecurity(
        token_id=1, is_honeypot=False, lp_burned=True,
        contract_renounced=True, sell_tax=Decimal("0"),
        top10_holders_pct=Decimal("20"), is_mintable=False,
    )
    score_no_rugcheck = compute_score(snap, sec)
    score_low_rc = compute_score(snap, sec, rugcheck_score=5)
    # Phase 54: rc<10 is neutral — same score as no rugcheck data
    assert score_low_rc == score_no_rugcheck


def test_rugcheck_report_empty_risks():
    """Report with no risks should parse cleanly."""
    report = RugcheckReport(score=0, risks=[], mint="test")
    assert report.score == 0
    assert len(report.risks) == 0

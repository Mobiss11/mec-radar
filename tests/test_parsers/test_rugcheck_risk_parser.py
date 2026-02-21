"""Tests for rugcheck risk string parsing."""

from src.parsers.rugcheck_risk_parser import parse_rugcheck_risks


class TestRugcheckRiskParser:
    def test_multiple_dangers(self) -> None:
        """Multiple danger-level risks = high risk boost."""
        risks = "Mutable metadata, Freeze authority still enabled, Mint authority still enabled"
        result = parse_rugcheck_risks(risks)

        assert result.danger_count == 3
        assert result.risk_boost == 20
        assert result.total_risks == 3

    def test_clean_token(self) -> None:
        """No known danger risks."""
        risks = "Low holder count, New token (less than 7 days)"
        result = parse_rugcheck_risks(risks)

        assert result.danger_count == 0
        assert result.warning_count == 2
        assert result.risk_boost == 0

    def test_empty_string(self) -> None:
        """Empty or None risks string."""
        assert parse_rugcheck_risks(None).total_risks == 0
        assert parse_rugcheck_risks("").total_risks == 0
        assert parse_rugcheck_risks("  ").total_risks == 0

    def test_mixed_risks(self) -> None:
        """Mix of danger and warning risks."""
        risks = "Mutable metadata, Low holder count, Copycat token"
        result = parse_rugcheck_risks(risks)

        assert result.danger_count == 2
        assert result.warning_count == 1
        assert result.risk_boost == 10

    def test_unknown_risk_names(self) -> None:
        """Unknown risk names should not count as danger or warning."""
        risks = "Some new risk, Another unknown thing"
        result = parse_rugcheck_risks(risks)

        assert result.danger_count == 0
        assert result.warning_count == 0
        assert result.total_risks == 2

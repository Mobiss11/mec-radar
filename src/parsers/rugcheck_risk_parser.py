"""Rugcheck risk string parser — extract structured risk info.

The rugcheck_risks field in TokenSecurity stores risk names as comma-separated string.
This module provides parsing and scoring based on risk names.
"""

from dataclasses import dataclass

# Known dangerous risk names from Rugcheck API
DANGER_RISKS = frozenset({
    "Mutable metadata",
    "Freeze authority still enabled",
    "Mint authority still enabled",
    "Low liquidity",
    "Top holder owns more than 50%",
    "Single holder ownership risk",
    "LP not burned or locked",
    "Copycat token",
    "High holder concentration",
    "Potential honeypot",
})

WARNING_RISKS = frozenset({
    "Low holder count",
    "New token (less than 7 days)",
    "No social links",
    "Large amount of LP tokens held by team",
    "Top 10 holders own more than 50%",
    "Low amount of LP providers",
})


@dataclass
class RugcheckRiskAnalysis:
    """Structured risk analysis from rugcheck_risks string."""

    danger_count: int = 0
    warning_count: int = 0
    total_risks: int = 0
    risk_names: list[str] | None = None

    @property
    def risk_boost(self) -> int:
        """Risk score boost based on danger count."""
        if self.danger_count >= 3:
            return 20
        if self.danger_count >= 2:
            return 10
        if self.danger_count >= 1:
            return 5
        return 0


def parse_rugcheck_risks(risks_string: str | None) -> RugcheckRiskAnalysis:
    """Parse comma-separated rugcheck_risks string into structured analysis.

    Args:
        risks_string: The rugcheck_risks field from TokenSecurity.
    """
    if not risks_string or not risks_string.strip():
        return RugcheckRiskAnalysis()

    risk_names = [r.strip() for r in risks_string.split(",") if r.strip()]
    if not risk_names:
        return RugcheckRiskAnalysis()

    danger_count = sum(1 for r in risk_names if r in DANGER_RISKS)
    warning_count = sum(1 for r in risk_names if r in WARNING_RISKS)

    return RugcheckRiskAnalysis(
        danger_count=danger_count,
        warning_count=warning_count,
        total_risks=len(risk_names),
        risk_names=risk_names,
    )

"""Pydantic models for Rugcheck.xyz API responses."""

from pydantic import BaseModel


class RugcheckRisk(BaseModel):
    """Individual risk detected by Rugcheck."""

    name: str
    description: str = ""
    level: str = "info"  # "warn", "danger", "info"
    score: int = 0


class RugcheckReport(BaseModel):
    """Summary report from Rugcheck.xyz.

    score: 0 = safest, 100 = most dangerous.
    risks: list of detected risk factors.
    """

    score: int = 0
    risks: list[RugcheckRisk] = []
    mint: str = ""
    token_name: str = ""
    token_symbol: str = ""

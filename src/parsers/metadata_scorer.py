"""Token metadata scoring — score based on social links and description.

Uses existing token data (website, twitter, telegram, description)
that is collected but currently NOT used in scoring.
"""

from dataclasses import dataclass

from loguru import logger


@dataclass
class MetadataScoreResult:
    """Result of metadata scoring."""

    score: int = 0  # -4 to +9
    has_description: bool = False
    has_website: bool = False
    has_twitter: bool = False
    has_telegram: bool = False
    socials_count: int = 0


def score_metadata(
    description: str | None = None,
    website: str | None = None,
    twitter: str | None = None,
    telegram: str | None = None,
) -> MetadataScoreResult:
    """Score token based on metadata presence and quality.

    Returns score from -4 (no socials) to +9 (full socials + good description).
    """
    score = 0
    has_desc = bool(description and len(description.strip()) > 20)
    has_web = bool(website and len(website.strip()) > 5)
    has_tw = bool(twitter and len(twitter.strip()) > 3)
    has_tg = bool(telegram and len(telegram.strip()) > 3)

    if has_desc:
        # Check for generic/copypasted descriptions
        desc_lower = description.strip().lower() if description else ""
        generic_phrases = {"just a token", "the best token", "to the moon"}
        is_generic = any(gp in desc_lower for gp in generic_phrases)
        if is_generic:
            score -= 1
        else:
            score += 2

    if has_web:
        score += 3
    if has_tw:
        score += 2
    if has_tg:
        score += 2

    socials_count = sum([has_web, has_tw, has_tg])

    if socials_count == 0:
        score -= 3  # No socials at all is a red flag

    return MetadataScoreResult(
        score=score,
        has_description=has_desc,
        has_website=has_web,
        has_twitter=has_tw,
        has_telegram=has_tg,
        socials_count=socials_count,
    )

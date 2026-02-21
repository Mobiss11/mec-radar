"""Website and domain checker — WHOIS age + HTTP reachability.

No API key needed. Uses python-whois + httpx HEAD request.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from loguru import logger


@dataclass
class WebsiteCheckResult:
    """Result of website/domain check."""

    url: str = ""
    is_reachable: bool = False
    status_code: int | None = None
    domain_age_days: int | None = None
    has_ssl: bool = False
    redirect_url: str | None = None


async def check_website(url: str) -> WebsiteCheckResult:
    """Check if website exists, is reachable, and get domain age.

    Returns WebsiteCheckResult with reachability and domain info.
    """
    result = WebsiteCheckResult(url=url)

    if not url:
        return result

    # Normalize URL
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    # 1. HTTP HEAD check
    try:
        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            verify=False,  # some token sites have bad certs
        ) as client:
            resp = await client.head(url)
            result.is_reachable = resp.status_code < 500
            result.status_code = resp.status_code
            result.has_ssl = str(resp.url).startswith("https://")
            if str(resp.url) != url:
                result.redirect_url = str(resp.url)
    except (httpx.RequestError, httpx.HTTPStatusError):
        result.is_reachable = False

    # 2. Domain age via WHOIS (run in executor to avoid blocking)
    try:
        domain = _extract_domain(url)
        if domain:
            age_days = await asyncio.to_thread(_whois_domain_age, domain)
            result.domain_age_days = age_days
    except Exception:
        pass

    return result


def _extract_domain(url: str) -> str:
    """Extract root domain from URL."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or ""
    # Remove www prefix
    if host.startswith("www."):
        host = host[4:]
    return host


def _whois_domain_age(domain: str) -> int | None:
    """Get domain age in days via python-whois. Blocking call."""
    try:
        import whois

        w = whois.whois(domain)
        creation = w.creation_date
        if isinstance(creation, list):
            creation = creation[0]
        if isinstance(creation, datetime):
            if creation.tzinfo is None:
                creation = creation.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - creation
            return max(age.days, 0)
    except Exception as e:
        logger.debug(f"[WHOIS] Failed for {domain}: {e}")
    return None

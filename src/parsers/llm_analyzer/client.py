"""LLM-based token analysis via OpenRouter API.

Uses Gemini 2.5 Flash Lite for cost-effective analysis:
- ~$0.03/1M input, ~$0.15/1M output
- ~1s latency, good quality for classification tasks

Analyzes: token description, website content, Twitter sentiment.
Returns a risk score 0-100 and summary.
"""

import asyncio

import httpx
from loguru import logger

from src.parsers.rate_limiter import RateLimiter

MAX_RETRIES = 2
RETRY_DELAYS = [2.0, 5.0]


class LLMAnalysisResult:
    """Result of LLM token analysis."""

    def __init__(self) -> None:
        self.risk_score: int = 50  # 0=safe, 100=scam
        self.summary: str = ""
        self.red_flags: list[str] = []
        self.green_flags: list[str] = []


class LLMAnalyzerClient:
    """Token analysis via OpenRouter (Gemini Flash)."""

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        api_key: str,
        model: str = "google/gemini-2.5-flash-lite",
        max_rps: float = 2.0,
    ) -> None:
        self._rate_limiter = RateLimiter(max_rps)
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=30.0,  # LLM responses can be slow
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def analyze_token(
        self,
        *,
        symbol: str | None = None,
        name: str | None = None,
        description: str | None = None,
        website_url: str | None = None,
        twitter_handle: str | None = None,
        creator_token_count: int | None = None,
        top10_holder_pct: float | None = None,
    ) -> LLMAnalysisResult:
        """Analyze token metadata for scam/rug indicators.

        Returns risk score 0-100 and structured flags.
        """
        result = LLMAnalysisResult()

        # Build context
        context_parts = []
        if symbol:
            context_parts.append(f"Symbol: ${symbol}")
        if name:
            context_parts.append(f"Name: {name}")
        if description:
            context_parts.append(f"Description: {description[:500]}")
        if website_url:
            context_parts.append(f"Website: {website_url}")
        if twitter_handle:
            context_parts.append(f"Twitter: @{twitter_handle}")
        if creator_token_count is not None:
            context_parts.append(f"Creator has launched {creator_token_count} tokens")
        if top10_holder_pct is not None:
            context_parts.append(f"Top 10 holders own {top10_holder_pct:.1f}%")

        if not context_parts:
            return result

        token_context = "\n".join(context_parts)

        prompt = f"""Analyze this Solana memecoin for scam/rug-pull risk. Be concise.

TOKEN INFO:
{token_context}

Respond in this EXACT JSON format (no markdown):
{{"risk_score": <0-100>, "red_flags": ["flag1", ...], "green_flags": ["flag1", ...], "summary": "1 sentence"}}

Rules:
- 0-20 = likely legitimate, 80-100 = likely scam
- Red flags: copycat name, no socials, generic description, serial deployer, high concentration
- Green flags: unique concept, established socials, reasonable distribution, clear utility
- If description is empty/generic, that's a red flag (+20 risk)
- If creator launched 5+ tokens, that's a red flag (+30 risk)"""

        for attempt in range(MAX_RETRIES + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.post(
                    "/chat/completions",
                    json={
                        "model": self._model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 200,
                        "temperature": 0.1,
                    },
                )

                if resp.status_code == 429:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[LLM] Rate limited, retrying in {delay}s")
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code != 200:
                    logger.debug(f"[LLM] API error: {resp.status_code}")
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                        continue
                    return result

                data = resp.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )

                return self._parse_response(content)

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.debug(f"[LLM] {type(e).__name__}, retry in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"[LLM] Failed after {MAX_RETRIES + 1} attempts: {e}")
            except Exception as e:
                logger.warning(f"[LLM] Unexpected error: {e}")
                break

        return result

    def _parse_response(self, content: str) -> LLMAnalysisResult:
        """Parse LLM JSON response into result."""
        import json

        result = LLMAnalysisResult()

        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
        if content.endswith("```"):
            content = content.rsplit("```", 1)[0]
        content = content.strip()

        try:
            data = json.loads(content)
            result.risk_score = max(0, min(100, int(data.get("risk_score", 50))))
            result.red_flags = data.get("red_flags", [])
            result.green_flags = data.get("green_flags", [])
            result.summary = data.get("summary", "")
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"[LLM] Parse failed: {e}, content: {content[:200]}")

        return result

    async def close(self) -> None:
        await self._client.aclose()

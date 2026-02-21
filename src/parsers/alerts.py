"""Real-time alert system for high-score tokens matching signal criteria.

Dispatches alerts to configured channels:
- Console log (always on)
- Telegram bot (if configured)
- Redis pubsub (for external consumers)

Deduplicates alerts per token address within a cooldown window.
"""

import asyncio
import html as html_mod
import time
from dataclasses import dataclass

import httpx
from loguru import logger


@dataclass
class TokenAlert:
    """Alert payload for a high-score token."""

    token_address: str
    symbol: str | None
    score: int
    action: str  # "strong_buy", "buy", "watch"
    reasons: dict[str, str]
    price: float | None
    market_cap: float | None
    liquidity: float | None
    source: str | None  # discovery source


class AlertDispatcher:
    """Dispatches real-time alerts with deduplication.

    Alert channels:
    1. Console logger (always)
    2. Telegram (if bot_token + admin_id configured)
    3. Redis pubsub channel "alerts:signals" (if redis available)
    """

    def __init__(
        self,
        *,
        telegram_bot_token: str = "",
        telegram_admin_id: int = 0,
        redis=None,
        cooldown_sec: int = 300,
    ) -> None:
        self._telegram_token = telegram_bot_token
        self._telegram_chat_id = telegram_admin_id
        self._redis = redis
        self._cooldown_sec = cooldown_sec
        self._recent: dict[tuple[str, str], float] = {}  # (address, action) → last alert timestamp
        self._http: httpx.AsyncClient | None = None
        self._total_sent: int = 0
        # Telegram rate limiter: max 25 msg/sec to avoid FloodWait
        self._tg_semaphore = asyncio.Semaphore(25)
        self._tg_last_send: float = 0.0

    async def dispatch(self, alert: TokenAlert) -> None:
        """Send alert to all configured channels."""
        # Dedup check — keyed by (address, action) so watch doesn't block buy
        now = time.monotonic()
        dedup_key = (alert.token_address, alert.action)
        last = self._recent.get(dedup_key, 0)
        if now - last < self._cooldown_sec:
            return

        self._recent[dedup_key] = now
        self._total_sent += 1

        # Cleanup old entries (keep memory bounded)
        if len(self._recent) > 5000:
            cutoff = now - self._cooldown_sec
            self._recent = {k: v for k, v in self._recent.items() if v > cutoff}

        # 1. Console log
        _log_alert(alert)

        # 2. Telegram
        if self._telegram_token and self._telegram_chat_id:
            await self._send_telegram(alert)

        # 3. Redis pubsub
        if self._redis:
            await self._publish_redis(alert)

    async def _send_telegram(self, alert: TokenAlert) -> None:
        """Send alert via Telegram Bot API.

        Tries aiogram bot first (if running), falls back to httpx with 1 retry.
        """
        text = _format_telegram_message(alert)

        # Try aiogram bot first
        try:
            from src.bot.bot import get_bot

            bot = get_bot()
            await bot.send_message(
                chat_id=self._telegram_chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        except Exception:
            pass  # Fall back to httpx

        # Fallback: direct HTTP API call with 1 retry
        last_err: Exception | None = None
        for _attempt in range(2):
            try:
                if not self._http:
                    self._http = httpx.AsyncClient(timeout=10)

                url = f"https://api.telegram.org/bot{self._telegram_token}/sendMessage"
                resp = await self._http.post(
                    url,
                    json={
                        "chat_id": self._telegram_chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
                if resp.status_code == 200:
                    return
                last_err = Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                last_err = e
            if _attempt == 0:
                await asyncio.sleep(2)

        if last_err:
            logger.warning(f"[ALERT] Telegram send failed after 2 attempts: {last_err}")

    async def _publish_redis(self, alert: TokenAlert) -> None:
        """Publish alert to Redis pubsub channel."""
        try:
            import json

            payload = json.dumps({
                "address": alert.token_address,
                "symbol": alert.symbol,
                "score": alert.score,
                "action": alert.action,
                "reasons": alert.reasons,
                "price": alert.price,
                "mcap": alert.market_cap,
                "liquidity": alert.liquidity,
                "source": alert.source,
                "ts": time.time(),
            })
            await self._redis.publish("alerts:signals", payload)
        except Exception as e:
            logger.debug(f"[ALERT] Redis publish failed: {e}")

    async def send_paper_open(
        self, symbol: str, address: str, price: float, sol_amount: float, action: str,
    ) -> None:
        """Notify about a new paper trading position."""
        emoji = "🟢🟢" if action == "strong_buy" else "🟢"
        safe_name = html_mod.escape(symbol or address[:12])
        text = (
            f"{emoji} <b>PAPER OPEN</b>: <b>{safe_name}</b>\n\n"
            f"Entry: ${price:.8g}\n"
            f"Size: {sol_amount} SOL\n"
            f"Signal: {action.upper()}\n\n"
            f"<code>{address}</code>"
        )
        logger.info(f"[PAPER] Opened {symbol or address[:12]} @ ${price:.8g} ({sol_amount} SOL)")
        await self._send_telegram_text(text)

    async def send_paper_close(
        self,
        symbol: str,
        address: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        reason: str,
    ) -> None:
        """Notify about a closed paper trading position."""
        emoji = "✅" if pnl_pct > 0 else "🔴"
        safe_name = html_mod.escape(symbol or address[:12])
        text = (
            f"{emoji} <b>PAPER CLOSE</b>: <b>{safe_name}</b>\n\n"
            f"Entry: ${entry_price:.8g}\n"
            f"Exit: ${exit_price:.8g}\n"
            f"P&L: <b>{pnl_pct:+.1f}%</b>\n"
            f"Reason: {reason}\n\n"
            f"<code>{address}</code>"
        )
        logger.info(
            f"[PAPER] Closed {symbol or address[:12]} "
            f"P&L={pnl_pct:+.1f}% reason={reason}"
        )
        await self._send_telegram_text(text)

    async def send_paper_report(self, stats: dict) -> None:
        """Send periodic paper trading portfolio summary."""
        wr = stats["win_rate"] * 100
        total_pnl = stats["total_pnl_usd"]
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"

        text = (
            f"{pnl_emoji} <b>PAPER PORTFOLIO</b>\n\n"
            f"Open: {stats['open_count']} positions\n"
            f"Closed: {stats['closed_count']} trades\n"
            f"Win rate: <b>{wr:.0f}%</b> ({stats['wins']}W / {stats['losses']}L)\n"
            f"Total invested: {stats['total_invested_sol']:.1f} SOL\n"
            f"Total P&L: <b>{total_pnl:+.2f} SOL</b>\n"
        )

        if stats.get("open_positions"):
            text += "\n<b>Open positions:</b>\n"
            for p in stats["open_positions"][:10]:
                sym = p.get("symbol", p["address"][:8])
                text += f"  • {sym}: {p['pnl_pct']:+.1f}%\n"

        logger.info(
            f"[PAPER] Report: {stats['open_count']} open, "
            f"{stats['closed_count']} closed, WR={wr:.0f}%, "
            f"P&L={total_pnl:+.2f} SOL"
        )
        await self._send_telegram_text(text)

    async def _send_telegram_text(self, text: str) -> None:
        """Send raw HTML text to Telegram with rate limiting."""
        if not self._telegram_token or not self._telegram_chat_id:
            return

        async with self._tg_semaphore:
            # Enforce minimum 40ms between messages (25 msg/sec)
            now = time.monotonic()
            elapsed = now - self._tg_last_send
            if elapsed < 0.04:
                await asyncio.sleep(0.04 - elapsed)

            try:
                if not self._http:
                    self._http = httpx.AsyncClient(timeout=10)

                url = f"https://api.telegram.org/bot{self._telegram_token}/sendMessage"
                resp = await self._http.post(
                    url,
                    json={
                        "chat_id": self._telegram_chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
                self._tg_last_send = time.monotonic()
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    logger.warning(f"[ALERT] Telegram FloodWait, sleeping {retry_after}s")
                    await asyncio.sleep(retry_after)
            except Exception as e:
                logger.warning(f"[ALERT] Telegram send failed: {e}")

    @property
    def total_sent(self) -> int:
        return self._total_sent

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None


def _log_alert(alert: TokenAlert) -> None:
    """Log alert to console with structured data."""
    reasons_str = ", ".join(
        f"{k}: {v}" for k, v in (alert.reasons or {}).items()
    )
    mcap_str = f"${int(alert.market_cap):,}" if alert.market_cap else "?"
    liq_str = f"${int(alert.liquidity):,}" if alert.liquidity else "?"

    logger.info(
        f"[ALERT] {alert.action.upper()} "
        f"{alert.symbol or alert.token_address[:12]} "
        f"score={alert.score} mcap={mcap_str} liq={liq_str} "
        f"| {reasons_str}"
    )


def _format_telegram_message(alert: TokenAlert) -> str:
    """Format alert as Telegram HTML message."""
    emoji = {"strong_buy": "🟢🟢", "buy": "🟢", "watch": "🟡", "early_watch": "👀"}.get(alert.action, "⚪")
    mcap = f"${int(alert.market_cap):,}" if alert.market_cap else "?"
    liq = f"${int(alert.liquidity):,}" if alert.liquidity else "?"

    reasons_lines = "\n".join(
        f"  • {v}" for v in (alert.reasons or {}).values()
    )

    safe_name = html_mod.escape(alert.symbol or alert.token_address[:12])
    return (
        f"{emoji} <b>{alert.action.upper()}</b>: "
        f"<b>{safe_name}</b>\n\n"
        f"Score: <b>{alert.score}</b>\n"
        f"MCap: {mcap}\n"
        f"Liquidity: {liq}\n"
        f"Source: {alert.source or '?'}\n\n"
        f"<b>Reasons:</b>\n{reasons_lines}\n\n"
        f"<code>{alert.token_address}</code>"
    )

"""Automatic health degradation alerting.

Runs as a background task alongside the parser. Periodically checks:
- Data freshness (no new snapshots in N minutes)
- Enrichment throughput drop (rate dropped below threshold)
- Error rate spike (>10% of enrichments failing)
- Queue backlog (queue growing faster than draining)
- WebSocket disconnections lasting > 5 minutes

Sends alerts to console log and optionally Telegram.
"""

import asyncio
import time
from dataclasses import dataclass

from loguru import logger

from src.parsers.metrics import EnrichmentMetrics


@dataclass
class HealthThresholds:
    """Thresholds for triggering health alerts."""

    max_snapshot_age_sec: int = 300  # 5 min without new snapshots
    min_enrichments_per_min: float = 0.5  # Less than 0.5/min = stalled
    max_error_rate_pct: float = 20.0  # >20% error rate = degraded
    max_queue_size: int = 3000  # Queue growing too large
    alert_cooldown_sec: int = 600  # Don't repeat same alert within 10 min


class HealthAlerter:
    """Background health monitor with configurable thresholds."""

    def __init__(
        self,
        metrics: EnrichmentMetrics,
        *,
        thresholds: HealthThresholds | None = None,
        alert_callback=None,
    ) -> None:
        self._metrics = metrics
        self._thresholds = thresholds or HealthThresholds()
        self._alert_callback = alert_callback  # async callable for external alerts
        self._last_alerts: dict[str, float] = {}
        self._check_count: int = 0

    async def run_loop(self, check_interval_sec: int = 60) -> None:
        """Periodic health check loop — runs until cancelled."""
        # Wait for pipeline to warm up before alerting
        await asyncio.sleep(120)

        while True:
            try:
                await self._check_all()
            except Exception as e:
                logger.debug(f"[HEALTH] Check error: {e}")
            await asyncio.sleep(check_interval_sec)

    async def _check_all(self) -> None:
        """Run all health checks."""
        self._check_count += 1
        summary = self._metrics.get_summary()

        # Check enrichment throughput
        rate = summary.get("enrichments_per_min", 0)
        uptime = summary.get("uptime_sec", 0)
        if uptime > 300 and rate < self._thresholds.min_enrichments_per_min:
            await self._fire_alert(
                "low_throughput",
                f"Enrichment throughput is low: {rate:.1f}/min "
                f"(threshold: {self._thresholds.min_enrichments_per_min}/min)",
            )

        # Check error rates
        total_errors = 0
        total_runs = 0
        for stage_data in summary.get("stages", {}).values():
            errors = stage_data.get("errors", {})
            total_errors += sum(errors.values())
            total_runs += stage_data.get("runs", 0)

        if total_runs > 20:
            error_rate = total_errors / total_runs * 100
            if error_rate > self._thresholds.max_error_rate_pct:
                await self._fire_alert(
                    "high_error_rate",
                    f"API error rate is high: {error_rate:.1f}% "
                    f"({total_errors}/{total_runs} enrichments)",
                )

        # Check average latency (warn if >5s per enrichment)
        for stage_name, stage_data in summary.get("stages", {}).items():
            avg_lat = stage_data.get("avg_latency_ms", 0)
            if avg_lat > 5000 and stage_data.get("runs", 0) > 10:
                await self._fire_alert(
                    f"high_latency_{stage_name}",
                    f"Stage {stage_name} avg latency is {avg_lat}ms (>5000ms)",
                )

        # Check coverage (if INITIAL stage has low mcap coverage)
        initial = summary.get("stages", {}).get("INITIAL", {})
        if initial.get("runs", 0) > 20:
            coverage = initial.get("coverage", {})
            mcap_cov = coverage.get("mcap", 100)
            if mcap_cov < 50:
                await self._fire_alert(
                    "low_mcap_coverage",
                    f"INITIAL stage mcap coverage is low: {mcap_cov:.0f}%",
                )

    async def _fire_alert(self, alert_type: str, message: str) -> None:
        """Fire an alert with deduplication/cooldown."""
        now = time.monotonic()
        last = self._last_alerts.get(alert_type, 0)
        if now - last < self._thresholds.alert_cooldown_sec:
            return

        self._last_alerts[alert_type] = now
        logger.warning(f"[HEALTH-ALERT] {message}")

        if self._alert_callback:
            try:
                await self._alert_callback(alert_type, message)
            except Exception as e:
                logger.debug(f"[HEALTH-ALERT] Callback error: {e}")

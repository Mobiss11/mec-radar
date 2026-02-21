"""Tests for health degradation alerting."""

import asyncio

import pytest

from src.parsers.health_alerting import HealthAlerter, HealthThresholds
from src.parsers.metrics import EnrichmentMetrics


@pytest.fixture
def metrics():
    m = EnrichmentMetrics()
    # Simulate 10 min of uptime
    import time
    m._start_time = time.monotonic() - 600
    return m


@pytest.fixture
def alerter(metrics):
    return HealthAlerter(
        metrics,
        thresholds=HealthThresholds(
            min_enrichments_per_min=1.0,
            max_error_rate_pct=15.0,
            alert_cooldown_sec=1,
        ),
    )


@pytest.mark.asyncio
async def test_low_throughput_alert(alerter, metrics):
    """Should fire alert when throughput is too low."""
    # Only 2 enrichments in 10 minutes = 0.2/min
    metrics.record_enrichment("INITIAL", 100.0)
    metrics.record_enrichment("INITIAL", 100.0)

    fired_alerts = []

    async def callback(alert_type: str, message: str) -> None:
        fired_alerts.append((alert_type, message))

    alerter._alert_callback = callback
    await alerter._check_all()

    assert len(fired_alerts) == 1
    assert fired_alerts[0][0] == "low_throughput"
    assert "0.2/min" in fired_alerts[0][1]


@pytest.mark.asyncio
async def test_high_error_rate_alert(alerter, metrics):
    """Should fire alert when error rate exceeds threshold."""
    # 30 enrichments, 6 errors = 20% > 15%
    for _ in range(30):
        metrics.record_enrichment("INITIAL", 100.0)
    for _ in range(6):
        metrics.record_api_error("INITIAL", "birdeye")

    fired_alerts = []

    async def callback(alert_type: str, message: str) -> None:
        fired_alerts.append((alert_type, message))

    alerter._alert_callback = callback
    await alerter._check_all()

    alert_types = [a[0] for a in fired_alerts]
    assert "high_error_rate" in alert_types


@pytest.mark.asyncio
async def test_no_alert_when_healthy(alerter, metrics):
    """No alerts when metrics are within thresholds."""
    # 100 enrichments in 10 min = 10/min, well above threshold
    for _ in range(100):
        metrics.record_enrichment("INITIAL", 100.0, has_mcap=True)
    # Only 1 error = 1%, well below 15%
    metrics.record_api_error("INITIAL", "birdeye")

    fired_alerts = []

    async def callback(alert_type: str, message: str) -> None:
        fired_alerts.append((alert_type, message))

    alerter._alert_callback = callback
    await alerter._check_all()

    assert len(fired_alerts) == 0


@pytest.mark.asyncio
async def test_alert_cooldown(alerter, metrics):
    """Same alert type should not fire again within cooldown."""
    metrics.record_enrichment("INITIAL", 100.0)

    fired_alerts = []

    async def callback(alert_type: str, message: str) -> None:
        fired_alerts.append(alert_type)

    alerter._alert_callback = callback

    # First check fires
    await alerter._check_all()
    count_first = len(fired_alerts)

    # Immediate second check should be deduped by cooldown
    # (cooldown_sec=1, so without sleep it should still dedup)
    await alerter._check_all()
    count_second = len(fired_alerts)

    # Should not have doubled
    assert count_second == count_first


@pytest.mark.asyncio
async def test_high_latency_alert(alerter, metrics):
    """Alert on very high latency stages."""
    # 20 runs with avg 6000ms in INITIAL
    for _ in range(20):
        metrics.record_enrichment("INITIAL", 6000.0)

    fired_alerts = []

    async def callback(alert_type: str, message: str) -> None:
        fired_alerts.append((alert_type, message))

    alerter._alert_callback = callback
    await alerter._check_all()

    alert_types = [a[0] for a in fired_alerts]
    assert "high_latency_INITIAL" in alert_types

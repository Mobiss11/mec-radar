"""Tests for enrichment pipeline metrics."""

from src.parsers.metrics import EnrichmentMetrics


def test_record_enrichment_basic():
    m = EnrichmentMetrics()
    m.record_enrichment("INITIAL", 150.0, has_price=True, has_mcap=True, has_score=True)
    m.record_enrichment("INITIAL", 250.0, has_price=True, has_mcap=False)

    summary = m.get_summary()
    assert summary["total_enrichments"] == 2
    assert "INITIAL" in summary["stages"]

    stage = summary["stages"]["INITIAL"]
    assert stage["runs"] == 2
    assert stage["avg_latency_ms"] == 200  # (150+250)/2
    assert stage["max_latency_ms"] == 250
    assert stage["coverage"]["price"] == 100.0
    assert stage["coverage"]["mcap"] == 50.0
    assert stage["coverage"]["score"] == 50.0


def test_record_api_errors():
    m = EnrichmentMetrics()
    m.record_enrichment("MIN_5", 100.0)
    m.record_api_error("MIN_5", "birdeye")
    m.record_api_error("MIN_5", "birdeye")
    m.record_api_error("MIN_5", "gmgn")

    summary = m.get_summary()
    errors = summary["stages"]["MIN_5"]["errors"]
    assert errors["birdeye"] == 2
    assert errors["gmgn"] == 1
    assert errors["dexscreener"] == 0


def test_record_prune():
    m = EnrichmentMetrics()
    m.record_prune()
    m.record_prune()
    summary = m.get_summary()
    assert summary["total_pruned"] == 2


def test_enrichments_per_min():
    m = EnrichmentMetrics()
    # Override start time to simulate 2 minutes of uptime
    import time
    m._start_time = time.monotonic() - 120  # 2 min ago
    for _ in range(10):
        m.record_enrichment("INITIAL", 100.0)

    summary = m.get_summary()
    # 10 enrichments in 2 min = 5/min
    assert summary["enrichments_per_min"] == 5.0


def test_format_stats_line():
    m = EnrichmentMetrics()
    m.record_enrichment("INITIAL", 200.0)
    m.record_prune()
    m.record_api_error("INITIAL", "birdeye")

    line = m.format_stats_line()
    assert "enriched=1" in line
    assert "pruned=1" in line
    assert "errors=1" in line
    assert "avg_lat=" in line


def test_multiple_stages():
    m = EnrichmentMetrics()
    m.record_enrichment("INITIAL", 100.0, has_mcap=True)
    m.record_enrichment("MIN_5", 200.0, has_mcap=True)
    m.record_enrichment("HOUR_1", 300.0, has_mcap=False)

    summary = m.get_summary()
    assert len(summary["stages"]) == 3
    assert summary["stages"]["INITIAL"]["avg_latency_ms"] == 100
    assert summary["stages"]["HOUR_1"]["avg_latency_ms"] == 300


def test_empty_metrics():
    m = EnrichmentMetrics()
    summary = m.get_summary()
    assert summary["total_enrichments"] == 0
    assert summary["total_pruned"] == 0
    assert summary["stages"] == {}
    line = m.format_stats_line()
    assert "enriched=0" in line

"""Enrichment pipeline metrics — latency, coverage, error rates.

Thread-safe counters that accumulate during runtime and can be
read by the stats reporter or health check.
"""

import time
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class StageMetrics:
    """Metrics for a single enrichment stage."""

    total_runs: int = 0
    total_latency_ms: float = 0.0
    max_latency_ms: float = 0.0

    # Coverage: how many snapshots had each field populated
    with_price: int = 0
    with_mcap: int = 0
    with_liquidity: int = 0
    with_holders: int = 0
    with_security: int = 0
    with_score: int = 0

    # API error counts
    birdeye_errors: int = 0
    gmgn_errors: int = 0
    dexscreener_errors: int = 0

    @property
    def avg_latency_ms(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.total_latency_ms / self.total_runs

    @property
    def coverage_pct(self) -> dict[str, float]:
        """Coverage percentages for key fields."""
        if self.total_runs == 0:
            return {}
        n = self.total_runs
        return {
            "price": self.with_price / n * 100,
            "mcap": self.with_mcap / n * 100,
            "liquidity": self.with_liquidity / n * 100,
            "holders": self.with_holders / n * 100,
            "security": self.with_security / n * 100,
            "score": self.with_score / n * 100,
        }


class EnrichmentMetrics:
    """Global metrics accumulator for the enrichment pipeline.

    Thread-safe via a simple lock (enrichment is single-threaded async
    but the stats reporter reads concurrently).
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._stages: dict[str, StageMetrics] = {}
        self._total_enrichments: int = 0
        self._total_pruned: int = 0
        self._start_time: float = time.monotonic()

    def _get_stage(self, stage_name: str) -> StageMetrics:
        if stage_name not in self._stages:
            self._stages[stage_name] = StageMetrics()
        return self._stages[stage_name]

    def record_enrichment(
        self,
        stage_name: str,
        latency_ms: float,
        *,
        has_price: bool = False,
        has_mcap: bool = False,
        has_liquidity: bool = False,
        has_holders: bool = False,
        has_security: bool = False,
        has_score: bool = False,
    ) -> None:
        """Record a completed enrichment run."""
        with self._lock:
            self._total_enrichments += 1
            sm = self._get_stage(stage_name)
            sm.total_runs += 1
            sm.total_latency_ms += latency_ms
            if latency_ms > sm.max_latency_ms:
                sm.max_latency_ms = latency_ms
            if has_price:
                sm.with_price += 1
            if has_mcap:
                sm.with_mcap += 1
            if has_liquidity:
                sm.with_liquidity += 1
            if has_holders:
                sm.with_holders += 1
            if has_security:
                sm.with_security += 1
            if has_score:
                sm.with_score += 1

    def record_latency(self, stage_name: str, latency_ms: float) -> None:
        """Record latency separately (when coverage is recorded elsewhere)."""
        with self._lock:
            sm = self._get_stage(stage_name)
            sm.total_latency_ms += latency_ms
            if latency_ms > sm.max_latency_ms:
                sm.max_latency_ms = latency_ms

    def record_api_error(self, stage_name: str, api: str) -> None:
        """Record an API error for a stage."""
        with self._lock:
            sm = self._get_stage(stage_name)
            if api == "birdeye":
                sm.birdeye_errors += 1
            elif api == "gmgn":
                sm.gmgn_errors += 1
            elif api == "dexscreener":
                sm.dexscreener_errors += 1

    def record_prune(self) -> None:
        """Record a pruned token."""
        with self._lock:
            self._total_pruned += 1

    def get_summary(self) -> dict:
        """Return a snapshot of all metrics."""
        with self._lock:
            uptime = time.monotonic() - self._start_time
            summary: dict = {
                "uptime_sec": round(uptime),
                "total_enrichments": self._total_enrichments,
                "total_pruned": self._total_pruned,
                "enrichments_per_min": round(
                    self._total_enrichments / max(uptime / 60, 1), 1
                ),
                "stages": {},
            }
            for name, sm in self._stages.items():
                stage_data: dict = {
                    "runs": sm.total_runs,
                    "avg_latency_ms": round(sm.avg_latency_ms),
                    "max_latency_ms": round(sm.max_latency_ms),
                    "coverage": sm.coverage_pct,
                    "errors": {
                        "birdeye": sm.birdeye_errors,
                        "gmgn": sm.gmgn_errors,
                        "dexscreener": sm.dexscreener_errors,
                    },
                }
                summary["stages"][name] = stage_data
            return summary

    def format_stats_line(self) -> str:
        """One-line summary for the stats reporter."""
        with self._lock:
            total = self._total_enrichments
            pruned = self._total_pruned
            uptime = time.monotonic() - self._start_time
            rate = total / max(uptime / 60, 1)

            total_errors = sum(
                sm.birdeye_errors + sm.gmgn_errors + sm.dexscreener_errors
                for sm in self._stages.values()
            )
            avg_latency = 0.0
            if total > 0:
                total_lat = sum(sm.total_latency_ms for sm in self._stages.values())
                avg_latency = total_lat / total

            return (
                f"enriched={total} pruned={pruned} "
                f"rate={rate:.1f}/min "
                f"avg_lat={avg_latency:.0f}ms "
                f"errors={total_errors}"
            )


# Global singleton — imported by worker.py and stats_reporter
metrics = EnrichmentMetrics()

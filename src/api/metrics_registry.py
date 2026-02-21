"""Singleton registry for runtime objects shared between worker and dashboard API.

Populated once during ``run_parser()`` initialization.  FastAPI endpoints
read these references directly — thread-safe because everything runs in a
single asyncio event loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from src.parsers.alerts import AlertDispatcher
    from src.parsers.chainstack.grpc_client import ChainstackGrpcClient
    from src.parsers.enrichment_queue import PersistentEnrichmentQueue
    from src.parsers.gmgn.client import GmgnClient
    from src.parsers.meteora.ws_client import MeteoraDBCClient
    from src.parsers.metrics import EnrichmentMetrics
    from src.parsers.paper_trader import PaperTrader
    from src.parsers.pumpportal.ws_client import PumpPortalClient
    from src.parsers.solsniffer.client import SolSnifferClient
    from src.trading.real_trader import RealTrader


class MetricsRegistry:
    """Holds references to runtime objects for API access."""

    pipeline_metrics: EnrichmentMetrics | None = None
    pumpportal: PumpPortalClient | None = None
    grpc_client: ChainstackGrpcClient | None = None
    meteora_ws: MeteoraDBCClient | None = None
    gmgn: GmgnClient | None = None
    enrichment_queue: PersistentEnrichmentQueue | None = None
    alert_dispatcher: AlertDispatcher | None = None
    paper_trader: PaperTrader | None = None
    real_trader: RealTrader | None = None
    solsniffer: SolSnifferClient | None = None
    redis: Any | None = None  # Redis[bytes]


registry = MetricsRegistry()

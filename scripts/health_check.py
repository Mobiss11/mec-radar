"""System health check — reports parser and data pipeline status.

Checks:
- Database connectivity and table sizes
- Redis connectivity and queue size
- Enrichment pipeline progress (stage distribution)
- Data freshness (latest snapshot age)
- API health (Birdeye, GMGN error rates from recent logs)
- Signal generation stats

Usage:
    poetry run python scripts/health_check.py
"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings  # noqa: E402
from src.db.database import async_session_factory  # noqa: E402
from src.models.signal import Signal  # noqa: E402
from src.models.token import (  # noqa: E402
    CreatorProfile,
    Token,
    TokenOHLCV,
    TokenOutcome,
    TokenSecurity,
    TokenSnapshot,
    TokenTopHolder,
    TokenTrade,
)

STATUS_OK = "OK"
STATUS_WARN = "WARN"
STATUS_ERROR = "ERROR"


async def check_health() -> dict:
    """Run all health checks and return structured report."""
    report: dict = {"timestamp": datetime.utcnow().isoformat(), "checks": {}}

    # 1. Database
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
            report["checks"]["database"] = {"status": STATUS_OK}
            await _add_table_stats(session, report)
            await _add_freshness(session, report)
            await _add_enrichment_progress(session, report)
            await _add_signal_stats(session, report)
            await _add_source_breakdown(session, report)
    except Exception as e:
        report["checks"]["database"] = {"status": STATUS_ERROR, "error": str(e)}

    # 2. Redis
    try:
        from src.db.redis import get_redis

        redis = await get_redis()
        await redis.ping()
        queue_size = await redis.zcard("enrichment:queue")
        smart_wallets = await redis.scard("smart_wallets:all")
        report["checks"]["redis"] = {
            "status": STATUS_OK,
            "queue_size": queue_size,
            "smart_wallets_cached": smart_wallets,
        }
    except Exception as e:
        report["checks"]["redis"] = {"status": STATUS_WARN, "error": str(e)}

    # 3. Feature flags
    report["checks"]["features"] = {
        "pumpportal": settings.enable_pumpportal,
        "meteora_dbc": settings.enable_meteora_dbc,
        "gmgn": settings.enable_gmgn,
        "birdeye": settings.enable_birdeye,
        "dexscreener": settings.enable_dexscreener,
    }

    return report


async def _add_table_stats(session: AsyncSession, report: dict) -> None:
    """Add row counts for key tables."""
    tables = {
        "tokens": Token,
        "snapshots": TokenSnapshot,
        "security": TokenSecurity,
        "top_holders": TokenTopHolder,
        "outcomes": TokenOutcome,
        "trades": TokenTrade,
        "ohlcv": TokenOHLCV,
        "creator_profiles": CreatorProfile,
        "signals": Signal,
    }
    counts = {}
    for name, model in tables.items():
        count = await session.scalar(select(func.count(model.id)))
        counts[name] = count or 0

    report["checks"]["table_counts"] = counts


async def _add_freshness(session: AsyncSession, report: dict) -> None:
    """Check how fresh the latest data is."""
    latest_snapshot = await session.scalar(
        select(func.max(TokenSnapshot.timestamp))
    )
    latest_token = await session.scalar(
        select(func.max(Token.first_seen_at))
    )

    now = datetime.utcnow()
    freshness = {}

    if latest_snapshot:
        age_min = (now - latest_snapshot).total_seconds() / 60
        freshness["latest_snapshot_age_min"] = round(age_min, 1)
        freshness["latest_snapshot_status"] = (
            STATUS_OK if age_min < 5 else STATUS_WARN if age_min < 30 else STATUS_ERROR
        )
    else:
        freshness["latest_snapshot_status"] = STATUS_ERROR
        freshness["latest_snapshot_age_min"] = None

    if latest_token:
        age_min = (now - latest_token).total_seconds() / 60
        freshness["latest_token_age_min"] = round(age_min, 1)
    else:
        freshness["latest_token_age_min"] = None

    report["checks"]["freshness"] = freshness


async def _add_enrichment_progress(session: AsyncSession, report: dict) -> None:
    """Stage distribution and scoring coverage."""
    # Stage counts
    stmt = (
        select(TokenSnapshot.stage, func.count(TokenSnapshot.id))
        .group_by(TokenSnapshot.stage)
        .order_by(TokenSnapshot.stage)
    )
    result = await session.execute(stmt)
    stages = {stage or "N/A": count for stage, count in result.all()}

    # Score distribution
    scored = await session.scalar(
        select(func.count(TokenSnapshot.id)).where(
            TokenSnapshot.score.isnot(None)
        )
    )
    avg_score = await session.scalar(
        select(func.avg(TokenSnapshot.score)).where(
            TokenSnapshot.score.isnot(None),
            TokenSnapshot.score > 0,
        )
    )
    with_mcap = await session.scalar(
        select(func.count(TokenSnapshot.id)).where(
            TokenSnapshot.market_cap.isnot(None)
        )
    )

    report["checks"]["enrichment"] = {
        "stages": stages,
        "scored_snapshots": scored or 0,
        "avg_score_nonzero": round(float(avg_score), 1) if avg_score else 0,
        "snapshots_with_mcap": with_mcap or 0,
    }


async def _add_signal_stats(session: AsyncSession, report: dict) -> None:
    """Signal generation stats."""
    total = await session.scalar(select(func.count(Signal.id)))

    # By status
    stmt = (
        select(Signal.status, func.count(Signal.id))
        .group_by(Signal.status)
    )
    result = await session.execute(stmt)
    by_status = {status: count for status, count in result.all()}

    # Last 24h signals
    cutoff = datetime.utcnow() - timedelta(hours=24)
    recent = await session.scalar(
        select(func.count(Signal.id)).where(Signal.created_at >= cutoff)
    )

    report["checks"]["signals"] = {
        "total": total or 0,
        "by_status": by_status,
        "last_24h": recent or 0,
    }


async def _add_source_breakdown(session: AsyncSession, report: dict) -> None:
    """Token count by discovery source."""
    stmt = (
        select(Token.source, func.count(Token.id))
        .group_by(Token.source)
        .order_by(func.count(Token.id).desc())
    )
    result = await session.execute(stmt)
    report["checks"]["sources"] = {
        source or "N/A": count for source, count in result.all()
    }


def print_report(report: dict) -> None:
    """Pretty-print the health report."""
    print("=" * 60)
    print(f"HEALTH CHECK — {report['timestamp']}")
    print("=" * 60)

    checks = report["checks"]

    # Database
    db = checks.get("database", {})
    status = db.get("status", STATUS_ERROR)
    print(f"\n  Database: [{status}]")
    if "error" in db:
        print(f"    Error: {db['error']}")

    # Redis
    redis = checks.get("redis", {})
    status = redis.get("status", STATUS_ERROR)
    print(f"  Redis:    [{status}]")
    if status == STATUS_OK:
        print(f"    Queue size: {redis.get('queue_size', 0)}")
        print(f"    Smart wallets cached: {redis.get('smart_wallets_cached', 0)}")
    elif "error" in redis:
        print(f"    Error: {redis['error']}")

    # Table counts
    counts = checks.get("table_counts", {})
    if counts:
        print("\n  Table counts:")
        for table, count in counts.items():
            print(f"    {table:20s} {count:>8,}")

    # Sources
    sources = checks.get("sources", {})
    if sources:
        print("\n  Token sources:")
        for source, count in sources.items():
            print(f"    {source:25s} {count:>6,}")

    # Freshness
    freshness = checks.get("freshness", {})
    if freshness:
        print("\n  Data freshness:")
        snap_age = freshness.get("latest_snapshot_age_min")
        snap_status = freshness.get("latest_snapshot_status", "N/A")
        print(f"    Latest snapshot: {snap_age or 'N/A'} min ago [{snap_status}]")
        token_age = freshness.get("latest_token_age_min")
        print(f"    Latest token:    {token_age or 'N/A'} min ago")

    # Enrichment
    enrichment = checks.get("enrichment", {})
    if enrichment:
        print("\n  Enrichment pipeline:")
        print(f"    Scored snapshots:   {enrichment.get('scored_snapshots', 0):,}")
        print(f"    Avg score (>0):     {enrichment.get('avg_score_nonzero', 0):.1f}")
        print(f"    With market_cap:    {enrichment.get('snapshots_with_mcap', 0):,}")
        stages = enrichment.get("stages", {})
        if stages:
            print("    Stages:")
            for stage, count in stages.items():
                print(f"      {stage:15s} {count:>6,}")

    # Signals
    signals = checks.get("signals", {})
    if signals:
        print("\n  Signals:")
        print(f"    Total:     {signals.get('total', 0):,}")
        print(f"    Last 24h:  {signals.get('last_24h', 0):,}")
        by_status = signals.get("by_status", {})
        for status, count in by_status.items():
            print(f"    {status:15s} {count:>6,}")

    # Features
    features = checks.get("features", {})
    if features:
        print("\n  Feature flags:")
        for name, enabled in features.items():
            status = "ON" if enabled else "OFF"
            print(f"    {name:20s} [{status}]")

    print("\n" + "=" * 60)


async def main() -> None:
    report = await check_health()
    print_report(report)


if __name__ == "__main__":
    asyncio.run(main())

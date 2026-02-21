"""Backtesting framework for the scoring model.

Simulates entry decisions based on historical INITIAL scores and
evaluates performance against actual outcomes (peak_multiplier).

Supports:
- Multiple score thresholds
- Win/loss statistics per threshold
- Expected value calculation
- Comparison of scoring model versions (via SQL snapshots)

Usage:
    poetry run python scripts/backtest_scoring.py
    poetry run python scripts/backtest_scoring.py --min-score 40 --take-profit 3.0 --stop-loss 0.5
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings  # noqa: E402
from src.db.database import async_session_factory  # noqa: E402
from src.models.token import Token, TokenOutcome, TokenSnapshot  # noqa: E402


@dataclass
class BacktestConfig:
    """Parameters for a single backtest run."""

    min_score: int = 30
    take_profit: float = 2.0  # exit at Nx multiplier
    stop_loss: float = 0.5  # exit at -50%
    max_hold_hours: int = 24


@dataclass
class TradeResult:
    """Result of a simulated trade."""

    token_address: str
    symbol: str | None
    source: str | None
    entry_score: int
    peak_multiplier: float
    final_multiplier: float | None
    is_rug: bool
    hit_tp: bool  # hit take-profit
    hit_sl: bool  # hit stop-loss
    pnl_pct: float  # realized P&L %


@dataclass
class BacktestReport:
    """Aggregate results of a backtest run."""

    config: BacktestConfig
    total_signals: int
    total_trades: int  # signals with outcome data
    winners: int  # hit TP
    losers: int  # hit SL or rug
    neutral: int  # neither TP nor SL within hold period
    win_rate: float
    avg_pnl_pct: float
    total_pnl_pct: float
    best_trade: TradeResult | None
    worst_trade: TradeResult | None
    trades: list[TradeResult]


async def run_backtest(config: BacktestConfig) -> BacktestReport:
    """Run a backtest simulation against historical data."""
    async with async_session_factory() as session:
        trades = await _simulate_trades(session, config)

    winners = [t for t in trades if t.hit_tp]
    losers = [t for t in trades if t.hit_sl or t.is_rug]
    neutral = [t for t in trades if not t.hit_tp and not t.hit_sl and not t.is_rug]

    total = len(trades)
    win_rate = len(winners) / total * 100 if total > 0 else 0
    avg_pnl = sum(t.pnl_pct for t in trades) / total if total > 0 else 0
    total_pnl = sum(t.pnl_pct for t in trades)

    best = max(trades, key=lambda t: t.pnl_pct) if trades else None
    worst = min(trades, key=lambda t: t.pnl_pct) if trades else None

    # Count signals (INITIAL snapshots with score >= threshold)
    async with async_session_factory() as session:
        stmt = select(TokenSnapshot.id).where(
            TokenSnapshot.stage == "INITIAL",
            TokenSnapshot.score >= config.min_score,
        )
        result = await session.execute(stmt)
        total_signals = len(result.all())

    return BacktestReport(
        config=config,
        total_signals=total_signals,
        total_trades=total,
        winners=len(winners),
        losers=len(losers),
        neutral=len(neutral),
        win_rate=win_rate,
        avg_pnl_pct=avg_pnl,
        total_pnl_pct=total_pnl,
        best_trade=best,
        worst_trade=worst,
        trades=trades,
    )


async def _simulate_trades(
    session: AsyncSession, config: BacktestConfig
) -> list[TradeResult]:
    """Simulate trades: entry at INITIAL, exit based on outcome data."""
    stmt = (
        select(
            Token.address,
            Token.symbol,
            Token.source,
            TokenSnapshot.score,
            TokenOutcome.peak_multiplier,
            TokenOutcome.final_multiplier,
            TokenOutcome.is_rug,
        )
        .join(TokenOutcome, Token.id == TokenOutcome.token_id)
        .join(
            TokenSnapshot,
            (TokenSnapshot.token_id == Token.id)
            & (TokenSnapshot.stage == "INITIAL"),
        )
        .where(
            TokenSnapshot.score >= config.min_score,
            TokenOutcome.peak_multiplier.isnot(None),
        )
    )
    result = await session.execute(stmt)
    rows = result.all()

    trades: list[TradeResult] = []
    for addr, symbol, source, score, peak_m, final_m, is_rug in rows:
        peak = float(peak_m)
        final = float(final_m) if final_m else None

        hit_tp = peak >= config.take_profit
        # For SL: if final multiplier < stop_loss threshold
        hit_sl = final is not None and final < config.stop_loss
        rug = is_rug is True

        # P&L calculation
        if hit_tp:
            pnl_pct = (config.take_profit - 1.0) * 100  # took profit at TP
        elif rug:
            pnl_pct = -90.0  # rug = ~90% loss
        elif hit_sl:
            pnl_pct = (config.stop_loss - 1.0) * 100  # stopped out
        elif final is not None:
            pnl_pct = (final - 1.0) * 100  # held to end
        else:
            pnl_pct = (peak - 1.0) * 100  # best case (unrealized)

        trades.append(
            TradeResult(
                token_address=addr,
                symbol=symbol,
                source=source,
                entry_score=score,
                peak_multiplier=peak,
                final_multiplier=final,
                is_rug=rug,
                hit_tp=hit_tp,
                hit_sl=hit_sl,
                pnl_pct=pnl_pct,
            )
        )

    return trades


def print_report(report: BacktestReport) -> None:
    """Pretty-print a backtest report."""
    c = report.config
    print("=" * 65)
    print("BACKTEST REPORT")
    print(f"  Min score: {c.min_score} | TP: {c.take_profit}x | SL: {c.stop_loss}x")
    print("=" * 65)
    print(f"\n  Signals (score >= {c.min_score}): {report.total_signals}")
    print(f"  Trades with outcome:    {report.total_trades}")
    print(f"  Winners (hit TP):       {report.winners}")
    print(f"  Losers (SL/rug):        {report.losers}")
    print(f"  Neutral:                {report.neutral}")
    print(f"\n  Win rate:               {report.win_rate:.1f}%")
    print(f"  Avg P&L per trade:      {report.avg_pnl_pct:+.1f}%")
    print(f"  Total P&L (sum):        {report.total_pnl_pct:+.1f}%")

    if report.best_trade:
        b = report.best_trade
        print(f"\n  Best trade:  {b.symbol or b.token_address[:8]} "
              f"score={b.entry_score} peak={b.peak_multiplier:.1f}x "
              f"pnl={b.pnl_pct:+.1f}%")
    if report.worst_trade:
        w = report.worst_trade
        print(f"  Worst trade: {w.symbol or w.token_address[:8]} "
              f"score={w.entry_score} peak={w.peak_multiplier:.1f}x "
              f"pnl={w.pnl_pct:+.1f}%")

    # Score distribution of trades
    if report.trades:
        print("\n  Score distribution of trades:")
        buckets = [(30, 39), (40, 49), (50, 59), (60, 69), (70, 100)]
        for lo, hi in buckets:
            bucket_trades = [t for t in report.trades if lo <= t.entry_score <= hi]
            if not bucket_trades:
                continue
            bucket_winners = sum(1 for t in bucket_trades if t.hit_tp)
            bucket_wr = bucket_winners / len(bucket_trades) * 100
            bucket_avg = sum(t.pnl_pct for t in bucket_trades) / len(bucket_trades)
            print(f"    Score {lo}-{hi}: {len(bucket_trades)} trades, "
                  f"WR={bucket_wr:.0f}%, avg={bucket_avg:+.1f}%")

    print("\n" + "=" * 65)


async def run_multi_threshold() -> None:
    """Run backtests across multiple score thresholds for comparison."""
    print("\n" + "=" * 65)
    print("MULTI-THRESHOLD COMPARISON")
    print("=" * 65)
    print(f"\n  {'Threshold':>10s} {'Trades':>7s} {'WR':>7s} {'Avg PnL':>9s} {'Total PnL':>10s}")
    print(f"  {'-' * 46}")

    for threshold in [15, 20, 30, 40, 50, 60]:
        config = BacktestConfig(min_score=threshold)
        report = await run_backtest(config)
        print(
            f"  Score >= {threshold:<3d} {report.total_trades:>6d} "
            f"{report.win_rate:>6.1f}% "
            f"{report.avg_pnl_pct:>+8.1f}% "
            f"{report.total_pnl_pct:>+9.1f}%"
        )

    print()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest scoring model")
    parser.add_argument("--min-score", type=int, default=30)
    parser.add_argument("--take-profit", type=float, default=2.0)
    parser.add_argument("--stop-loss", type=float, default=0.5)
    parser.add_argument("--compare", action="store_true", help="Run multi-threshold comparison")
    args = parser.parse_args()

    if args.compare:
        await run_multi_threshold()
    else:
        config = BacktestConfig(
            min_score=args.min_score,
            take_profit=args.take_profit,
            stop_loss=args.stop_loss,
        )
        report = await run_backtest(config)
        print_report(report)


if __name__ == "__main__":
    asyncio.run(main())

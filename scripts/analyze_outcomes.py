"""Score vs outcome correlation analysis.

Analyzes how well the scoring model predicts token performance.
Generates a report with score buckets, hit rates, and signal quality metrics.

Usage:
    poetry run python scripts/analyze_outcomes.py
"""

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings  # noqa: E402
from src.db.database import async_session_factory  # noqa: E402
from src.models.token import Token, TokenOutcome, TokenSnapshot  # noqa: E402


SCORE_BUCKETS = [
    (0, 0, "Disqualified (0)"),
    (1, 14, "Very weak (1-14)"),
    (15, 29, "Weak (15-29)"),
    (30, 49, "Medium (30-49)"),
    (50, 69, "Good (50-69)"),
    (70, 100, "Strong (70-100)"),
]

MULTIPLIER_THRESHOLDS = [
    (Decimal("2.0"), "2x+"),
    (Decimal("3.0"), "3x+"),
    (Decimal("5.0"), "5x+"),
    (Decimal("10.0"), "10x+"),
]


async def analyze() -> None:
    """Run the full analysis."""
    async with async_session_factory() as session:
        print("=" * 70)
        print("SCORE vs OUTCOME CORRELATION ANALYSIS")
        print("=" * 70)

        await _print_data_coverage(session)
        await _print_score_distribution(session)
        await _print_score_vs_outcome(session)
        await _print_source_performance(session)
        await _print_top_performers(session)
        await _print_signal_quality(session)


async def _print_data_coverage(session: AsyncSession) -> None:
    """Print basic data coverage stats."""
    print("\n--- DATA COVERAGE ---")

    total_tokens = await session.scalar(select(func.count(Token.id)))
    total_snapshots = await session.scalar(select(func.count(TokenSnapshot.id)))
    scored_snapshots = await session.scalar(
        select(func.count(TokenSnapshot.id)).where(TokenSnapshot.score.isnot(None))
    )
    total_outcomes = await session.scalar(select(func.count(TokenOutcome.id)))
    outcomes_with_peak = await session.scalar(
        select(func.count(TokenOutcome.id)).where(
            TokenOutcome.peak_multiplier.isnot(None),
            TokenOutcome.peak_multiplier > Decimal("0"),
        )
    )
    outcomes_with_initial = await session.scalar(
        select(func.count(TokenOutcome.id)).where(
            TokenOutcome.initial_mcap.isnot(None)
        )
    )

    print(f"  Tokens:              {total_tokens:,}")
    print(f"  Snapshots:           {total_snapshots:,}")
    print(f"  Scored snapshots:    {scored_snapshots:,}")
    print(f"  Outcomes:            {total_outcomes:,}")
    print(f"  With initial_mcap:   {outcomes_with_initial:,}")
    print(f"  With peak_multiplier:{outcomes_with_peak:,}")

    # Stage distribution
    print("\n  Stage distribution:")
    stmt = (
        select(TokenSnapshot.stage, func.count(TokenSnapshot.id))
        .group_by(TokenSnapshot.stage)
        .order_by(TokenSnapshot.stage)
    )
    result = await session.execute(stmt)
    for stage, count in result.all():
        print(f"    {stage or 'N/A':15s} {count:,}")


async def _print_score_distribution(session: AsyncSession) -> None:
    """Print score distribution across buckets."""
    print("\n--- SCORE DISTRIBUTION (INITIAL stage only) ---")

    for lo, hi, label in SCORE_BUCKETS:
        count = await session.scalar(
            select(func.count(TokenSnapshot.id)).where(
                TokenSnapshot.score >= lo,
                TokenSnapshot.score <= hi,
                TokenSnapshot.stage == "INITIAL",
            )
        )
        print(f"  {label:25s} {count:>6,}")


async def _print_score_vs_outcome(session: AsyncSession) -> None:
    """Core analysis: for each score bucket, what % achieved each multiplier threshold."""
    print("\n--- SCORE vs OUTCOME (INITIAL score → peak multiplier) ---")

    # Join: token's INITIAL snapshot score → token's outcome peak_multiplier
    header = f"{'Score bucket':25s} {'Count':>6s}"
    for _, label in MULTIPLIER_THRESHOLDS:
        header += f" {label:>8s}"
    header += f" {'Avg peak':>10s} {'Rug %':>7s}"
    print(f"  {header}")
    print(f"  {'-' * len(header)}")

    for lo, hi, bucket_label in SCORE_BUCKETS:
        # Get tokens with INITIAL score in this bucket + their outcomes
        stmt = (
            select(TokenOutcome.peak_multiplier, TokenOutcome.is_rug)
            .join(Token, Token.id == TokenOutcome.token_id)
            .join(
                TokenSnapshot,
                (TokenSnapshot.token_id == Token.id)
                & (TokenSnapshot.stage == "INITIAL"),
            )
            .where(
                TokenSnapshot.score >= lo,
                TokenSnapshot.score <= hi,
                TokenOutcome.peak_multiplier.isnot(None),
            )
        )
        result = await session.execute(stmt)
        rows = result.all()

        count = len(rows)
        if count == 0:
            line = f"  {bucket_label:25s} {0:>6d}"
            for _ in MULTIPLIER_THRESHOLDS:
                line += f" {'N/A':>8s}"
            line += f" {'N/A':>10s} {'N/A':>7s}"
            print(line)
            continue

        multipliers = [r[0] for r in rows]
        rugs = [r[1] for r in rows if r[1] is not None]

        line = f"  {bucket_label:25s} {count:>6d}"
        for threshold, _ in MULTIPLIER_THRESHOLDS:
            hits = sum(1 for m in multipliers if m >= threshold)
            pct = hits / count * 100
            line += f" {pct:>7.1f}%"

        avg_peak = sum(multipliers) / len(multipliers) if multipliers else Decimal("0")
        rug_pct = sum(1 for r in rugs if r) / len(rugs) * 100 if rugs else 0
        line += f" {float(avg_peak):>10.2f}x"
        line += f" {rug_pct:>6.1f}%"
        print(line)


async def _print_source_performance(session: AsyncSession) -> None:
    """Breakdown by token source."""
    print("\n--- SOURCE PERFORMANCE ---")

    stmt = (
        select(
            Token.source,
            func.count(TokenOutcome.id),
            func.avg(TokenOutcome.peak_multiplier),
            func.count(TokenOutcome.id).filter(
                TokenOutcome.peak_multiplier >= Decimal("2.0")
            ),
        )
        .join(TokenOutcome, Token.id == TokenOutcome.token_id)
        .where(TokenOutcome.peak_multiplier.isnot(None))
        .group_by(Token.source)
        .order_by(func.count(TokenOutcome.id).desc())
    )
    result = await session.execute(stmt)
    rows = result.all()

    print(f"  {'Source':25s} {'Count':>6s} {'Avg peak':>10s} {'2x+ %':>8s}")
    print(f"  {'-' * 53}")
    for source, count, avg_peak, hits_2x in rows:
        pct_2x = hits_2x / count * 100 if count else 0
        avg_p = float(avg_peak) if avg_peak else 0
        print(f"  {source or 'N/A':25s} {count:>6d} {avg_p:>10.2f}x {pct_2x:>7.1f}%")


async def _print_top_performers(session: AsyncSession) -> None:
    """Show top 10 tokens by peak multiplier with their initial scores."""
    print("\n--- TOP 10 PERFORMERS ---")

    stmt = (
        select(
            Token.symbol,
            Token.address,
            Token.source,
            TokenOutcome.peak_multiplier,
            TokenOutcome.initial_mcap,
            TokenOutcome.peak_mcap,
            TokenOutcome.is_rug,
            TokenSnapshot.score,
        )
        .join(TokenOutcome, Token.id == TokenOutcome.token_id)
        .outerjoin(
            TokenSnapshot,
            (TokenSnapshot.token_id == Token.id)
            & (TokenSnapshot.stage == "INITIAL"),
        )
        .where(TokenOutcome.peak_multiplier.isnot(None))
        .order_by(TokenOutcome.peak_multiplier.desc())
        .limit(10)
    )
    result = await session.execute(stmt)
    rows = result.all()

    print(
        f"  {'Symbol':10s} {'Source':15s} {'Score':>6s} "
        f"{'Init mcap':>12s} {'Peak mcap':>12s} {'Peak':>8s} {'Rug':>4s}"
    )
    print(f"  {'-' * 71}")
    for symbol, addr, source, peak_m, init_m, peak_mcap, is_rug, score in rows:
        sym = (symbol or addr[:8]) if symbol else addr[:8]
        init_str = f"${int(init_m):,}" if init_m else "N/A"
        peak_str = f"${int(peak_mcap):,}" if peak_mcap else "N/A"
        rug_str = "YES" if is_rug else "no"
        score_str = str(score) if score is not None else "N/A"
        print(
            f"  {sym:10s} {(source or 'N/A'):15s} {score_str:>6s} "
            f"{init_str:>12s} {peak_str:>12s} {float(peak_m):>7.1f}x {rug_str:>4s}"
        )


async def _print_signal_quality(session: AsyncSession) -> None:
    """Compute signal quality metrics."""
    print("\n--- SIGNAL QUALITY METRICS ---")

    # For tokens with score >= 30, what's the 2x+ hit rate?
    for min_score in [30, 40, 50]:
        stmt = (
            select(
                func.count(TokenOutcome.id),
                func.count(TokenOutcome.id).filter(
                    TokenOutcome.peak_multiplier >= Decimal("2.0")
                ),
                func.count(TokenOutcome.id).filter(TokenOutcome.is_rug.is_(True)),
            )
            .join(Token, Token.id == TokenOutcome.token_id)
            .join(
                TokenSnapshot,
                (TokenSnapshot.token_id == Token.id)
                & (TokenSnapshot.stage == "INITIAL"),
            )
            .where(
                TokenSnapshot.score >= min_score,
                TokenOutcome.peak_multiplier.isnot(None),
            )
        )
        result = await session.execute(stmt)
        row = result.one()
        total, hits_2x, rugs = row

        if total > 0:
            hit_rate = hits_2x / total * 100
            rug_rate = rugs / total * 100 if rugs else 0
            print(
                f"  Score >= {min_score}: {total} tokens, "
                f"2x+ hit rate = {hit_rate:.1f}%, "
                f"rug rate = {rug_rate:.1f}%"
            )
        else:
            print(f"  Score >= {min_score}: no data")

    # Overall precision/recall for score >= 30 predicting 2x+
    print("\n  Overall stats:")
    total_with_outcomes = await session.scalar(
        select(func.count(TokenOutcome.id)).where(
            TokenOutcome.peak_multiplier.isnot(None)
        )
    )
    total_2x = await session.scalar(
        select(func.count(TokenOutcome.id)).where(
            TokenOutcome.peak_multiplier >= Decimal("2.0")
        )
    )
    print(f"  Tokens with outcomes:  {total_with_outcomes:,}")
    print(f"  Tokens achieving 2x+:  {total_2x:,}")
    if total_with_outcomes and total_with_outcomes > 0:
        base_rate = (total_2x or 0) / total_with_outcomes * 100
        print(f"  Base rate (2x+):       {base_rate:.1f}%")

    print("\n" + "=" * 70)
    print("Run the parser for 2+ weeks to get meaningful correlation data.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(analyze())

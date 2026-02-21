"""Auto-calibration of scoring and signal thresholds.

Reads token_outcomes + token_snapshots from the DB, buckets by score,
and finds optimal thresholds for:
- Minimum score to generate signals (currently 35)
- Net-score breakpoints for strong_buy / buy / watch (currently 8/5/2)

Usage:
    poetry run python scripts/auto_calibrate.py
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from loguru import logger
from sqlalchemy import select, and_

from config.settings import settings
from src.db.database import async_session_factory
from src.models.signal import Signal
from src.models.token import TokenOutcome, TokenSnapshot


@dataclass
class BucketStats:
    """Statistics for a score bucket."""

    score_min: int
    score_max: int
    count: int = 0
    wins_2x: int = 0
    wins_3x: int = 0
    wins_5x: int = 0
    wins_10x: int = 0
    rugs: int = 0
    avg_multiplier: Decimal = Decimal("0")

    @property
    def win_rate_2x(self) -> float:
        return (self.wins_2x / self.count * 100) if self.count else 0.0

    @property
    def win_rate_3x(self) -> float:
        return (self.wins_3x / self.count * 100) if self.count else 0.0

    @property
    def rug_rate(self) -> float:
        return (self.rugs / self.count * 100) if self.count else 0.0


@dataclass
class SignalBucketStats:
    """Statistics for a signal net_score bucket."""

    action: str
    count: int = 0
    wins_2x: int = 0
    rugs: int = 0
    avg_roi: float = 0.0


async def load_score_outcomes() -> list[dict]:
    """Load INITIAL snapshots with their outcomes."""
    async with async_session_factory() as session:
        stmt = (
            select(
                TokenSnapshot.score,
                TokenOutcome.peak_multiplier,
                TokenOutcome.is_rug,
                TokenOutcome.final_multiplier,
            )
            .join(TokenOutcome, TokenOutcome.token_id == TokenSnapshot.token_id)
            .where(
                and_(
                    TokenSnapshot.stage == "INITIAL",
                    TokenSnapshot.score.isnot(None),
                )
            )
        )
        result = await session.execute(stmt)
        return [
            {
                "score": row.score,
                "peak_mult": row.peak_multiplier or Decimal("1"),
                "is_rug": row.is_rug or False,
                "final_mult": row.final_multiplier or Decimal("1"),
            }
            for row in result.all()
        ]


async def load_signal_outcomes() -> list[dict]:
    """Load signals with their outcome data."""
    async with async_session_factory() as session:
        stmt = select(Signal).where(Signal.outcome_updated_at.isnot(None))
        result = await session.execute(stmt)
        signals = result.scalars().all()
        return [
            {
                "status": s.status,
                "score": s.score,
                "peak_mult": s.peak_multiplier_after or Decimal("1"),
                "peak_roi": s.peak_roi_pct or Decimal("0"),
                "is_rug": s.is_rug_after or False,
            }
            for s in signals
        ]


def analyse_score_buckets(data: list[dict]) -> list[BucketStats]:
    """Bucket tokens by score and compute win rates."""
    buckets_def = [
        (0, 10), (10, 20), (20, 30), (30, 40), (40, 50),
        (50, 60), (60, 70), (70, 80), (80, 90), (90, 100),
    ]
    buckets = [BucketStats(lo, hi) for lo, hi in buckets_def]

    for d in data:
        score = d["score"]
        for b in buckets:
            if b.score_min <= score < b.score_max or (b.score_max == 100 and score == 100):
                b.count += 1
                mult = d["peak_mult"]
                if mult >= 2:
                    b.wins_2x += 1
                if mult >= 3:
                    b.wins_3x += 1
                if mult >= 5:
                    b.wins_5x += 1
                if mult >= 10:
                    b.wins_10x += 1
                if d["is_rug"]:
                    b.rugs += 1
                b.avg_multiplier += mult
                break

    for b in buckets:
        if b.count > 0:
            b.avg_multiplier = b.avg_multiplier / b.count

    return buckets


def find_optimal_threshold(buckets: list[BucketStats], target_win_rate: float = 30.0) -> int:
    """Find the minimum score threshold where win rate (2x) exceeds target."""
    for b in buckets:
        if b.count >= 5 and b.win_rate_2x >= target_win_rate:
            return b.score_min
    return 50  # conservative default


def analyse_signal_actions(data: list[dict]) -> dict[str, SignalBucketStats]:
    """Analyse signal outcomes by action type."""
    by_action: dict[str, SignalBucketStats] = {}

    for d in data:
        action = d["status"]
        if action not in by_action:
            by_action[action] = SignalBucketStats(action=action)
        stats = by_action[action]
        stats.count += 1
        if d["peak_mult"] >= 2:
            stats.wins_2x += 1
        if d["is_rug"]:
            stats.rugs += 1
        stats.avg_roi += float(d["peak_roi"])

    for stats in by_action.values():
        if stats.count > 0:
            stats.avg_roi /= stats.count

    return by_action


def print_report(buckets: list[BucketStats], signal_stats: dict[str, SignalBucketStats], optimal_threshold: int) -> None:
    """Print calibration report."""
    print("\n" + "=" * 70)
    print("SCORE → OUTCOME CALIBRATION REPORT")
    print("=" * 70)

    print(f"\n{'Score':>10} {'Count':>6} {'2x%':>6} {'3x%':>6} {'5x%':>6} {'10x%':>6} {'Rug%':>6} {'AvgMult':>8}")
    print("-" * 60)

    total_count = 0
    total_wins = 0
    for b in buckets:
        if b.count == 0:
            continue
        total_count += b.count
        total_wins += b.wins_2x
        print(
            f"{b.score_min:>3}-{b.score_max:<3}  "
            f"{b.count:>6} "
            f"{b.win_rate_2x:>5.1f}% "
            f"{b.win_rate_3x:>5.1f}% "
            f"{(b.wins_5x / b.count * 100) if b.count else 0:>5.1f}% "
            f"{(b.wins_10x / b.count * 100) if b.count else 0:>5.1f}% "
            f"{b.rug_rate:>5.1f}% "
            f"{b.avg_multiplier:>7.2f}x"
        )

    print(f"\nTotal tokens with outcomes: {total_count}")
    if total_count > 0:
        print(f"Overall 2x win rate: {total_wins / total_count * 100:.1f}%")
    print(f"\nRecommended minimum score threshold: {optimal_threshold}")

    if signal_stats:
        print(f"\n{'Action':>12} {'Count':>6} {'2x%':>6} {'Rug%':>6} {'AvgROI':>8}")
        print("-" * 45)
        for action in ["strong_buy", "buy", "watch", "avoid"]:
            s = signal_stats.get(action)
            if not s or s.count == 0:
                continue
            win_pct = s.wins_2x / s.count * 100
            rug_pct = s.rugs / s.count * 100
            print(
                f"{action:>12} "
                f"{s.count:>6} "
                f"{win_pct:>5.1f}% "
                f"{rug_pct:>5.1f}% "
                f"{s.avg_roi:>7.1f}%"
            )
    else:
        print("\nNo signal outcome data available yet (signals need outcome_updated_at).")

    print("\n" + "=" * 70)


def build_recommendations(buckets: list[BucketStats], signal_stats: dict[str, SignalBucketStats], optimal: int) -> dict:
    """Build recommendations dict (for JSON export and printing)."""
    recs: dict = {
        "score_threshold": optimal,
        "buckets": [],
        "signal_actions": {},
    }

    for b in buckets:
        if b.count == 0:
            continue
        recs["buckets"].append({
            "range": f"{b.score_min}-{b.score_max}",
            "count": b.count,
            "win_rate_2x": round(b.win_rate_2x, 1),
            "win_rate_3x": round(b.win_rate_3x, 1),
            "rug_rate": round(b.rug_rate, 1),
            "avg_multiplier": float(round(b.avg_multiplier, 2)),
        })

    for action, s in signal_stats.items():
        if s.count == 0:
            continue
        recs["signal_actions"][action] = {
            "count": s.count,
            "win_rate_2x": round(s.wins_2x / s.count * 100, 1),
            "rug_rate": round(s.rugs / s.count * 100, 1),
            "avg_roi": round(s.avg_roi, 1),
        }

    high_buckets = [b for b in buckets if b.score_min >= 60 and b.count >= 5]
    if high_buckets:
        best = max(high_buckets, key=lambda b: b.win_rate_2x)
        recs["best_bucket"] = f"{best.score_min}-{best.score_max}"
        recs["best_bucket_win_rate"] = round(best.win_rate_2x, 1)

    return recs


async def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-calibrate scoring thresholds")
    parser.add_argument(
        "--save-json",
        action="store_true",
        help="Save recommendations to config/calibration.json",
    )
    args = parser.parse_args()

    logger.info("Loading score → outcome data...")
    score_data = await load_score_outcomes()
    logger.info(f"Loaded {len(score_data)} tokens with outcomes")

    signal_data = await load_signal_outcomes()
    logger.info(f"Loaded {len(signal_data)} signals with outcomes")

    if not score_data:
        print("No outcome data yet. Run the enrichment pipeline and collect data first.")
        sys.exit(0)

    buckets = analyse_score_buckets(score_data)
    optimal = find_optimal_threshold(buckets)
    signal_stats = analyse_signal_actions(signal_data)

    print_report(buckets, signal_stats, optimal)

    recs = build_recommendations(buckets, signal_stats, optimal)

    # Recommendations
    print("\nRECOMMENDATIONS:")
    print(f"  1. Score threshold: update worker.py score >= {optimal}")
    if "best_bucket" in recs:
        print(f"  2. Best bucket: {recs['best_bucket']} ({recs['best_bucket_win_rate']:.1f}% win rate)")
    print("  3. Run this script periodically to re-calibrate as data grows")

    if args.save_json:
        out_path = Path("config/calibration.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(recs, indent=2, ensure_ascii=False))
        print(f"\nSaved recommendations to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())

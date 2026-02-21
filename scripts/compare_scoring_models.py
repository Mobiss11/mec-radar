"""A/B comparison of scoring models v2 vs v3.

Loads all INITIAL snapshots with outcome data and computes scores
using both models, then compares predictive accuracy.

Metrics:
- Hit rate at various multiplier thresholds (2x, 3x, 5x)
- Average multiplier for tokens above score threshold
- False positive rate (high score → rug/dump)
- Precision/recall for "strong_buy" signals

Usage:
    poetry run python scripts/compare_scoring_models.py
    poetry run python scripts/compare_scoring_models.py --threshold 40
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config.settings import settings  # noqa: E402
from src.db.database import async_session_factory  # noqa: E402
from src.models.token import (  # noqa: E402
    CreatorProfile,
    Token,
    TokenOutcome,
    TokenSecurity,
    TokenSnapshot,
)
from src.parsers.scoring import compute_score  # noqa: E402
from src.parsers.scoring_v3 import compute_score_v3  # noqa: E402


@dataclass
class ModelStats:
    """Aggregate stats for a single scoring model."""

    name: str
    total_scored: int = 0
    above_threshold: int = 0
    hit_2x: int = 0
    hit_3x: int = 0
    hit_5x: int = 0
    hit_10x: int = 0
    rugs: int = 0
    avg_peak_multiplier: float = 0.0
    avg_score: float = 0.0
    # Per-bucket stats
    bucket_stats: dict = field(default_factory=dict)


@dataclass
class TokenData:
    """Joined token data for scoring comparison."""

    snapshot: TokenSnapshot
    security: TokenSecurity | None
    outcome: TokenOutcome
    token: Token
    creator_profile: CreatorProfile | None


async def load_token_data(session: AsyncSession) -> list[TokenData]:
    """Load INITIAL snapshots joined with security, outcome, and token data."""
    stmt = (
        select(TokenSnapshot, TokenOutcome, Token)
        .join(TokenOutcome, TokenSnapshot.token_id == TokenOutcome.token_id)
        .join(Token, TokenSnapshot.token_id == Token.id)
        .where(
            TokenSnapshot.stage == "INITIAL",
            TokenOutcome.peak_multiplier.isnot(None),
        )
    )
    result = await session.execute(stmt)
    rows = result.all()

    token_data: list[TokenData] = []
    for snapshot, outcome, token in rows:
        # Load security
        sec_stmt = select(TokenSecurity).where(TokenSecurity.token_id == token.id)
        sec_result = await session.execute(sec_stmt)
        security = sec_result.scalar_one_or_none()

        # Load creator profile
        creator_prof: CreatorProfile | None = None
        if token.creator_address:
            cp_stmt = select(CreatorProfile).where(
                CreatorProfile.address == token.creator_address
            )
            cp_result = await session.execute(cp_stmt)
            creator_prof = cp_result.scalar_one_or_none()

        token_data.append(
            TokenData(
                snapshot=snapshot,
                security=security,
                outcome=outcome,
                token=token,
                creator_profile=creator_prof,
            )
        )

    return token_data


def evaluate_model(
    model_name: str,
    score_fn,
    data: list[TokenData],
    threshold: int,
    *,
    stored_field: str | None = None,
) -> ModelStats:
    """Evaluate a scoring function against historical data.

    If stored_field is set, prefer the pre-computed score from snapshot
    (e.g. "score" or "score_v3") over re-computing.
    """
    stats = ModelStats(name=model_name)
    scores: list[int] = []
    above_scores: list[tuple[int, float, bool]] = []  # (score, peak_mult, is_rug)

    for td in data:
        # Prefer stored score if available
        score = None
        if stored_field:
            score = getattr(td.snapshot, stored_field, None)
        if score is None:
            score = score_fn(
                td.snapshot,
                td.security,
                creator_profile=td.creator_profile,
                holder_velocity=None,  # Not stored in snapshot
            )
        if score is None:
            continue

        stats.total_scored += 1
        scores.append(score)

        if score >= threshold:
            stats.above_threshold += 1
            peak = float(td.outcome.peak_multiplier)
            is_rug = td.outcome.is_rug is True

            above_scores.append((score, peak, is_rug))

            if peak >= 2.0:
                stats.hit_2x += 1
            if peak >= 3.0:
                stats.hit_3x += 1
            if peak >= 5.0:
                stats.hit_5x += 1
            if peak >= 10.0:
                stats.hit_10x += 1
            if is_rug:
                stats.rugs += 1

    if above_scores:
        stats.avg_peak_multiplier = sum(p for _, p, _ in above_scores) / len(above_scores)
    if scores:
        stats.avg_score = sum(scores) / len(scores)

    # Per-bucket analysis
    buckets = [(15, 29), (30, 44), (45, 59), (60, 74), (75, 100)]
    for lo, hi in buckets:
        bucket_items = [(s, p, r) for s, p, r in above_scores if lo <= s <= hi]
        if not bucket_items:
            continue
        bucket_peaks = [p for _, p, _ in bucket_items]
        bucket_rugs = sum(1 for _, _, r in bucket_items if r)
        stats.bucket_stats[f"{lo}-{hi}"] = {
            "count": len(bucket_items),
            "avg_peak": sum(bucket_peaks) / len(bucket_peaks),
            "hit_2x": sum(1 for p in bucket_peaks if p >= 2.0),
            "hit_5x": sum(1 for p in bucket_peaks if p >= 5.0),
            "rugs": bucket_rugs,
        }

    return stats


def print_comparison(v2: ModelStats, v3: ModelStats, threshold: int) -> None:
    """Pretty-print A/B comparison of two models."""
    print("=" * 70)
    print(f"A/B SCORING MODEL COMPARISON (threshold >= {threshold})")
    print("=" * 70)

    print(f"\n  {'Metric':<35s} {'v2 (current)':>15s} {'v3 (momentum)':>15s}")
    print(f"  {'-' * 65}")

    rows = [
        ("Total scored", str(v2.total_scored), str(v3.total_scored)),
        (f"Above threshold (>={threshold})", str(v2.above_threshold), str(v3.above_threshold)),
        ("Avg score (all)", f"{v2.avg_score:.1f}", f"{v3.avg_score:.1f}"),
        ("", "", ""),
        ("Hit 2x+", f"{v2.hit_2x} ({_pct(v2.hit_2x, v2.above_threshold)})", f"{v3.hit_2x} ({_pct(v3.hit_2x, v3.above_threshold)})"),
        ("Hit 3x+", f"{v2.hit_3x} ({_pct(v2.hit_3x, v2.above_threshold)})", f"{v3.hit_3x} ({_pct(v3.hit_3x, v3.above_threshold)})"),
        ("Hit 5x+", f"{v2.hit_5x} ({_pct(v2.hit_5x, v2.above_threshold)})", f"{v3.hit_5x} ({_pct(v3.hit_5x, v3.above_threshold)})"),
        ("Hit 10x+", f"{v2.hit_10x} ({_pct(v2.hit_10x, v2.above_threshold)})", f"{v3.hit_10x} ({_pct(v3.hit_10x, v3.above_threshold)})"),
        ("", "", ""),
        ("Rugs (false positives)", f"{v2.rugs} ({_pct(v2.rugs, v2.above_threshold)})", f"{v3.rugs} ({_pct(v3.rugs, v3.above_threshold)})"),
        ("Avg peak multiplier", f"{v2.avg_peak_multiplier:.2f}x", f"{v3.avg_peak_multiplier:.2f}x"),
    ]

    for label, val_v2, val_v3 in rows:
        if not label:
            print()
            continue
        print(f"  {label:<35s} {val_v2:>15s} {val_v3:>15s}")

    # Bucket analysis
    all_buckets = sorted(set(list(v2.bucket_stats.keys()) + list(v3.bucket_stats.keys())))
    if all_buckets:
        print(f"\n  {'Score Bucket':<15s} {'v2 count':>10s} {'v2 2x%':>8s} {'v2 avg':>8s} "
              f"{'v3 count':>10s} {'v3 2x%':>8s} {'v3 avg':>8s}")
        print(f"  {'-' * 75}")
        for bucket in all_buckets:
            b2 = v2.bucket_stats.get(bucket, {})
            b3 = v3.bucket_stats.get(bucket, {})
            c2 = b2.get("count", 0)
            c3 = b3.get("count", 0)
            print(
                f"  {bucket:<15s} "
                f"{c2:>10d} {_pct(b2.get('hit_2x', 0), c2):>8s} {b2.get('avg_peak', 0):>7.2f}x "
                f"{c3:>10d} {_pct(b3.get('hit_2x', 0), c3):>8s} {b3.get('avg_peak', 0):>7.2f}x"
            )

    # Winner
    print("\n  " + "-" * 65)
    v2_eff = v2.hit_2x / max(v2.above_threshold, 1)
    v3_eff = v3.hit_2x / max(v3.above_threshold, 1)
    winner = "v2" if v2_eff > v3_eff else "v3" if v3_eff > v2_eff else "TIE"
    print(f"  2x hit rate: v2={v2_eff:.1%} vs v3={v3_eff:.1%} -> Winner: {winner}")

    v2_fp = v2.rugs / max(v2.above_threshold, 1)
    v3_fp = v3.rugs / max(v3.above_threshold, 1)
    fp_winner = "v2" if v2_fp < v3_fp else "v3" if v3_fp < v2_fp else "TIE"
    print(f"  False positive rate: v2={v2_fp:.1%} vs v3={v3_fp:.1%} -> Better: {fp_winner}")

    print("\n" + "=" * 70)


def _pct(n: int, total: int) -> str:
    """Format as percentage string."""
    if total == 0:
        return "0%"
    return f"{n / total * 100:.0f}%"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Compare scoring models v2 vs v3")
    parser.add_argument("--threshold", type=int, default=30, help="Score threshold")
    args = parser.parse_args()

    print("Loading token data...")
    async with async_session_factory() as session:
        data = await load_token_data(session)

    if not data:
        print("No INITIAL snapshots with outcome data found.")
        return

    print(f"Loaded {len(data)} tokens with outcomes. Evaluating models...")

    v2_stats = evaluate_model("v2", compute_score, data, args.threshold, stored_field="score")
    v3_stats = evaluate_model("v3", compute_score_v3, data, args.threshold, stored_field="score_v3")

    print_comparison(v2_stats, v3_stats, args.threshold)


if __name__ == "__main__":
    asyncio.run(main())

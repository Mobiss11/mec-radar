"""Whale & holder dynamics — detect accumulation, distribution, concentration patterns.

Compares top holders between two consecutive snapshots for a token to
identify whale behaviour changes.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.token import TokenSnapshot, TokenTopHolder


@dataclass
class HolderChange:
    """Change in a single holder's position between snapshots."""

    address: str
    old_pct: Decimal | None
    new_pct: Decimal | None
    delta_pct: Decimal  # positive = accumulated, negative = distributed
    is_new: bool = False
    is_exited: bool = False


@dataclass
class HolderDiff:
    """Result of comparing top holders between two snapshots."""

    token_id: int
    old_snapshot_id: int
    new_snapshot_id: int
    changes: list[HolderChange] = field(default_factory=list)
    new_whales: list[HolderChange] = field(default_factory=list)
    exited_whales: list[HolderChange] = field(default_factory=list)
    top10_pct_old: Decimal = Decimal("0")
    top10_pct_new: Decimal = Decimal("0")

    @property
    def concentration_delta(self) -> Decimal:
        """Change in top-10 concentration. Positive = more concentrated."""
        return self.top10_pct_new - self.top10_pct_old

    @property
    def net_accumulation_pct(self) -> Decimal:
        """Net change in top holder positions. Positive = whales buying."""
        return sum((c.delta_pct for c in self.changes), Decimal("0"))


@dataclass
class WhalePattern:
    """Detected whale behaviour pattern."""

    pattern: str  # "accumulation", "distribution", "concentration", "dilution"
    severity: str  # "low", "medium", "high"
    description: str
    score_impact: int  # bonus/penalty for scoring


async def diff_holders(
    session: AsyncSession, token_id: int
) -> HolderDiff | None:
    """Compare top holders between the two most recent snapshots.

    Returns None if fewer than 2 snapshots with holders exist.
    """
    # Get 2 most recent snapshots for this token
    snap_stmt = (
        select(TokenSnapshot.id)
        .where(TokenSnapshot.token_id == token_id)
        .order_by(TokenSnapshot.timestamp.desc())
        .limit(2)
    )
    snap_result = await session.execute(snap_stmt)
    snap_ids = [row[0] for row in snap_result.all()]

    if len(snap_ids) < 2:
        return None

    new_snap_id, old_snap_id = snap_ids[0], snap_ids[1]

    # Fetch holders for both snapshots
    old_holders = await _get_holders_map(session, old_snap_id)
    new_holders = await _get_holders_map(session, new_snap_id)

    if not old_holders and not new_holders:
        return None

    diff = HolderDiff(
        token_id=token_id,
        old_snapshot_id=old_snap_id,
        new_snapshot_id=new_snap_id,
        top10_pct_old=sum(old_holders.values(), Decimal("0")),
        top10_pct_new=sum(new_holders.values(), Decimal("0")),
    )

    all_addresses = set(old_holders) | set(new_holders)

    for addr in all_addresses:
        old_pct = old_holders.get(addr)
        new_pct = new_holders.get(addr)

        old_val = old_pct or Decimal("0")
        new_val = new_pct or Decimal("0")
        delta = new_val - old_val

        change = HolderChange(
            address=addr,
            old_pct=old_pct,
            new_pct=new_pct,
            delta_pct=delta,
            is_new=addr not in old_holders,
            is_exited=addr not in new_holders,
        )

        diff.changes.append(change)

        if change.is_new and new_val >= Decimal("1.0"):
            diff.new_whales.append(change)
        elif change.is_exited and old_val >= Decimal("1.0"):
            diff.exited_whales.append(change)

    return diff


def detect_patterns(diff: HolderDiff) -> list[WhalePattern]:
    """Analyse a HolderDiff and return detected whale patterns."""
    patterns: list[WhalePattern] = []

    # 1. New whale accumulation (new addresses with >= 2% each)
    large_new = [w for w in diff.new_whales if (w.new_pct or 0) >= Decimal("2")]
    if large_new:
        total_new_pct = sum((w.new_pct or Decimal("0")) for w in large_new)
        severity = "high" if total_new_pct >= Decimal("5") else "medium"
        patterns.append(WhalePattern(
            pattern="accumulation",
            severity=severity,
            description=f"{len(large_new)} new whale(s) accumulated {total_new_pct:.1f}% total",
            score_impact=5 if severity == "medium" else 8,
        ))

    # 2. Distribution (whales exiting or reducing > 3%)
    distributors = [
        c for c in diff.changes
        if c.delta_pct < Decimal("-3") and not c.is_new
    ]
    if distributors:
        total_sold = abs(sum(c.delta_pct for c in distributors))
        severity = "high" if total_sold >= Decimal("10") else "medium" if total_sold >= Decimal("5") else "low"
        score_map = {"high": -10, "medium": -5, "low": -2}
        patterns.append(WhalePattern(
            pattern="distribution",
            severity=severity,
            description=f"{len(distributors)} whale(s) sold {total_sold:.1f}% total",
            score_impact=score_map[severity],
        ))

    # 3. Concentration increase (top-10 ownership growing)
    conc_delta = diff.concentration_delta
    if conc_delta >= Decimal("5"):
        severity = "high" if conc_delta >= Decimal("10") else "medium"
        patterns.append(WhalePattern(
            pattern="concentration",
            severity=severity,
            description=f"Top-10 concentration increased by {conc_delta:.1f}%",
            score_impact=-3 if severity == "medium" else -7,
        ))

    # 4. Dilution (top-10 decreasing = more distributed = healthier)
    if conc_delta <= Decimal("-5"):
        severity = "medium" if conc_delta <= Decimal("-10") else "low"
        patterns.append(WhalePattern(
            pattern="dilution",
            severity=severity,
            description=f"Top-10 concentration decreased by {abs(conc_delta):.1f}% (healthier)",
            score_impact=3,
        ))

    return patterns


async def analyse_whale_dynamics(
    session: AsyncSession, token_id: int
) -> tuple[HolderDiff | None, list[WhalePattern]]:
    """Full whale analysis: diff + pattern detection. Returns (diff, patterns)."""
    diff = await diff_holders(session, token_id)
    if diff is None:
        return None, []

    patterns = detect_patterns(diff)

    # Check new whales against known sybil clusters
    if diff.new_whales:
        try:
            from src.parsers.wallet_cluster import check_clustered_holders

            new_addrs = [w.address for w in diff.new_whales]
            cluster_ids = await check_clustered_holders(session, new_addrs)
            if cluster_ids:
                patterns.append(WhalePattern(
                    pattern="sybil_cluster",
                    severity="high",
                    description=f"New whales match {len(cluster_ids)} known cluster(s)",
                    score_impact=-8,
                ))
        except Exception as e:
            logger.debug(f"[WHALE] Cluster check failed: {e}")

    if patterns:
        logger.info(
            f"[WHALE] token_id={token_id}: "
            + ", ".join(f"{p.pattern}({p.severity})" for p in patterns)
        )

    return diff, patterns


async def _get_holders_map(
    session: AsyncSession, snapshot_id: int
) -> dict[str, Decimal]:
    """Get {address: percentage} map for a snapshot's top holders."""
    stmt = (
        select(TokenTopHolder.address, TokenTopHolder.percentage)
        .where(TokenTopHolder.snapshot_id == snapshot_id)
        .order_by(TokenTopHolder.rank)
    )
    result = await session.execute(stmt)
    return {
        row.address: row.percentage or Decimal("0")
        for row in result.all()
    }

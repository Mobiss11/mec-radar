"""Test enrichment task ordering for priority queue."""

from src.parsers.enrichment_types import (
    NEXT_STAGE,
    STAGE_SCHEDULE,
    EnrichmentPriority,
    EnrichmentStage,
    EnrichmentTask,
)


def test_migration_before_normal():
    migration = EnrichmentTask(
        priority=EnrichmentPriority.MIGRATION,
        scheduled_at=100.0,
        address="migration_token",
    )
    normal = EnrichmentTask(
        priority=EnrichmentPriority.NORMAL,
        scheduled_at=50.0,  # Earlier time, but lower priority
        address="normal_token",
    )
    assert migration < normal


def test_same_priority_ordered_by_time():
    early = EnrichmentTask(
        priority=EnrichmentPriority.NORMAL,
        scheduled_at=50.0,
        address="early",
    )
    late = EnrichmentTask(
        priority=EnrichmentPriority.NORMAL,
        scheduled_at=100.0,
        address="late",
    )
    assert early < late


def test_stage_defaults():
    task = EnrichmentTask(
        priority=EnrichmentPriority.NORMAL,
        scheduled_at=0.0,
        address="test",
    )
    assert task.stage == EnrichmentStage.INITIAL
    assert task.fetch_security is True
    assert task.is_migration is False
    assert task.discovery_time == 0.0
    assert task.last_score is None


def test_all_stages_in_next_stage_chain():
    """Every stage except HOUR_24 has a successor (PRE_SCAN → ... → HOUR_24)."""
    stage = EnrichmentStage.PRE_SCAN
    visited = {stage}
    while NEXT_STAGE[stage] is not None:
        stage = NEXT_STAGE[stage]
        assert stage not in visited, f"Cycle at {stage}"
        visited.add(stage)
    assert stage == EnrichmentStage.HOUR_24
    assert len(visited) == len(EnrichmentStage)


def test_stage_offsets_monotonically_increasing():
    stages = list(EnrichmentStage)
    offsets = [STAGE_SCHEDULE[s].offset_sec for s in stages]
    for i in range(1, len(offsets)):
        assert offsets[i] > offsets[i - 1], (
            f"{stages[i].name} offset {offsets[i]} <= {stages[i-1].name} offset {offsets[i-1]}"
        )


def test_stage_config_initial():
    config = STAGE_SCHEDULE[EnrichmentStage.INITIAL]
    assert config.fetch_gmgn_info is True
    assert config.fetch_security is True
    assert config.fetch_top_holders is True
    assert config.fetch_dexscreener is False
    assert config.prune_below_score is None


def test_stage_config_min2_quick_price():
    """MIN_2 has GMGN + DexScreener for quick price check."""
    config = STAGE_SCHEDULE[EnrichmentStage.MIN_2]
    assert config.fetch_gmgn_info is True  # Phase 10: quick price check
    assert config.fetch_dexscreener is True
    assert config.fetch_security is False


def test_stage_config_pruning():
    """MIN_5 and MIN_15 have prune thresholds (Phase 10: raised)."""
    min5 = STAGE_SCHEDULE[EnrichmentStage.MIN_5]
    assert min5.prune_below_score == 20  # was 15 — less aggressive

    min15 = STAGE_SCHEDULE[EnrichmentStage.MIN_15]
    assert min15.prune_below_score == 25  # was 20 — quality gate


def test_enrichment_task_with_last_score():
    task = EnrichmentTask(
        priority=EnrichmentPriority.NORMAL,
        scheduled_at=0.0,
        address="test",
        last_score=42,
    )
    assert task.last_score == 42

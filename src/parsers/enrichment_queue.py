"""Redis-backed enrichment queue — survives process restarts.

Uses Redis sorted set for scheduling (score = scheduled_at) and hash for task data.
Falls back to in-memory asyncio.PriorityQueue if Redis is unavailable.
"""

import asyncio
import json
from decimal import Decimal

from loguru import logger
from redis.asyncio import Redis

from src.parsers.enrichment_types import (
    EnrichmentPriority,
    EnrichmentStage,
    EnrichmentTask,
)
from src.parsers.jupiter.models import SellSimResult
from src.parsers.mint_parser import MintInfo

REDIS_KEY_QUEUE = "enrichment:queue"  # Sorted set: score=priority*1e12+scheduled_at
REDIS_KEY_TASKS = "enrichment:tasks"  # Hash: address:stage → task JSON


def _task_id(task: EnrichmentTask) -> str:
    return f"{task.address}:{task.stage.name}"


def _sort_score(task: EnrichmentTask) -> float:
    """Combine priority, stage bucket, and scheduled_at into a single sortable score.

    Three-tier ordering:
    1. Priority: 0 (migration) sorts before 1 (normal)  — *1e12
    2. Stage bucket: INITIAL+ sorts before PRE_SCAN      — *0.5e12
       PRE_SCAN is high-volume cheap filter; INITIAL+ are pre-vetted tokens
       ready for signal generation. Without this, constant PRE_SCAN inflow
       (~15/min) starves INITIAL tasks by 7-9 minutes.
    3. Scheduled_at: FIFO within same bucket
    """
    # PRE_SCAN = bucket 1 (lower priority), INITIAL+ = bucket 0 (higher)
    stage_bucket = 1 if task.stage == EnrichmentStage.PRE_SCAN else 0
    return task.priority * 1e12 + stage_bucket * 0.5e12 + task.scheduled_at


def _serialize_value(obj: object) -> object:
    """Convert non-JSON-serializable types (Decimal) to float."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _serialize_value(v) for k, v in obj.items()}
    return obj


def _task_to_dict(task: EnrichmentTask) -> dict:
    d = {
        "priority": task.priority,
        "scheduled_at": task.scheduled_at,
        "address": task.address,
        "stage": task.stage.name,
        "fetch_security": task.fetch_security,
        "is_migration": task.is_migration,
        "discovery_time": task.discovery_time,
        "last_score": task.last_score,
        "instant_rejected": task.instant_rejected,
        "prescan_risk_boost": task.prescan_risk_boost,
    }
    # Serialize prescan results as simple dicts if present
    if task.prescan_mint_info is not None:
        try:
            raw = (
                task.prescan_mint_info.__dict__
                if hasattr(task.prescan_mint_info, "__dict__")
                else task.prescan_mint_info
            )
            d["prescan_mint_info"] = _serialize_value(raw)
        except Exception:
            d["prescan_mint_info"] = None
    if task.prescan_sell_sim is not None:
        try:
            raw = (
                task.prescan_sell_sim.__dict__
                if hasattr(task.prescan_sell_sim, "__dict__")
                else task.prescan_sell_sim
            )
            d["prescan_sell_sim"] = _serialize_value(raw)
        except Exception:
            d["prescan_sell_sim"] = None
    return d


def _dict_to_task(data: dict) -> EnrichmentTask:
    # Reconstruct SellSimResult from dict if present
    sell_sim_raw = data.get("prescan_sell_sim")
    sell_sim = None
    if isinstance(sell_sim_raw, dict):
        sell_sim = SellSimResult(**sell_sim_raw)
    elif sell_sim_raw is not None:
        sell_sim = sell_sim_raw

    # Reconstruct MintInfo from dict if present
    mint_info_raw = data.get("prescan_mint_info")
    mint_info = None
    if isinstance(mint_info_raw, dict):
        mint_info = MintInfo(**mint_info_raw)
    elif mint_info_raw is not None:
        mint_info = mint_info_raw

    return EnrichmentTask(
        priority=data["priority"],
        scheduled_at=data["scheduled_at"],
        address=data["address"],
        stage=EnrichmentStage[data["stage"]],
        fetch_security=data.get("fetch_security", True),
        is_migration=data.get("is_migration", False),
        discovery_time=data.get("discovery_time", 0.0),
        last_score=data.get("last_score"),
        instant_rejected=data.get("instant_rejected", False),
        prescan_risk_boost=data.get("prescan_risk_boost", 0),
        prescan_mint_info=mint_info,
        prescan_sell_sim=sell_sim,
    )


class PersistentEnrichmentQueue:
    """Redis-backed priority queue with in-memory fallback.

    - put(): stores task in Redis sorted set + hash
    - get(): polls Redis for ready tasks (scheduled_at <= now)
    - Survives restarts: pending tasks persist in Redis
    """

    def __init__(self, redis: Redis | None, maxsize: int = 5000) -> None:
        self._redis = redis
        self._fallback: asyncio.PriorityQueue[EnrichmentTask] = asyncio.PriorityQueue(
            maxsize=maxsize
        )
        self._use_redis = redis is not None

    async def put(self, task: EnrichmentTask, *, allow_update: bool = False) -> None:
        """Add task to queue. Deduplicates by address:stage.

        Args:
            task: Enrichment task to enqueue.
            allow_update: If True, overwrite existing task (for stage progression).
                          If False, skip if task already queued (dedup between sources).
        """
        if self._use_redis:
            try:
                tid = _task_id(task)
                if not allow_update:
                    existing = await self._redis.zscore(REDIS_KEY_QUEUE, tid)
                    if existing is not None:
                        logger.debug(f"[QUEUE] Dedup: {tid} already queued, skipping")
                        return
                pipe = self._redis.pipeline()
                pipe.zadd(REDIS_KEY_QUEUE, {tid: _sort_score(task)})
                pipe.hset(REDIS_KEY_TASKS, tid, json.dumps(_task_to_dict(task)))
                await pipe.execute()
                return
            except Exception as e:
                logger.debug(f"[QUEUE] Redis put failed, using fallback: {e}")

        try:
            self._fallback.put_nowait(task)
        except asyncio.QueueFull:
            logger.warning("[QUEUE] Queue full, dropping task")

    async def get(self) -> EnrichmentTask:
        """Get next ready task (blocking). Returns task with scheduled_at <= now."""
        while True:
            if self._use_redis:
                try:
                    task = await self._try_redis_get()
                    if task is not None:
                        return task
                except Exception as e:
                    logger.debug(f"[QUEUE] Redis get failed: {e}")

            # Fallback or no ready task in Redis — use in-memory queue
            if not self._use_redis:
                return await self._fallback.get()

            # Redis has no ready tasks — sleep and retry
            await asyncio.sleep(1.0)

    async def _try_redis_get(self) -> EnrichmentTask | None:
        """Try to pop the highest-priority ready task from Redis."""
        now = asyncio.get_event_loop().time()
        # Max score covers all ready tasks: priority 1, scheduled_at <= now
        max_score = 1e12 + now

        results = await self._redis.zrangebyscore(
            REDIS_KEY_QUEUE, "-inf", max_score, start=0, num=1
        )
        if not results:
            return None

        tid = results[0]

        # Check if this task is actually ready
        score = await self._redis.zscore(REDIS_KEY_QUEUE, tid)
        if score is None:
            return None

        priority = int(score // 1e12)
        scheduled_at = score - priority * 1e12

        if scheduled_at > now:
            # Not ready yet — only return if it'll be ready within 2s
            if scheduled_at - now > 2.0:
                return None

        # Pop atomically
        removed = await self._redis.zrem(REDIS_KEY_QUEUE, tid)
        if not removed:
            return None  # Another worker got it

        task_json = await self._redis.hget(REDIS_KEY_TASKS, tid)
        await self._redis.hdel(REDIS_KEY_TASKS, tid)

        if not task_json:
            return None

        return _dict_to_task(json.loads(task_json))

    async def qsize(self) -> int:
        """Approximate queue size."""
        if self._use_redis:
            try:
                return await self._redis.zcard(REDIS_KEY_QUEUE)
            except Exception:
                pass
        return self._fallback.qsize()

    async def task_done(self) -> None:
        """Compatibility with asyncio.PriorityQueue interface."""
        if not self._use_redis:
            self._fallback.task_done()

    async def restore_from_redis(self) -> int:
        """On startup, count how many tasks were recovered from Redis."""
        if not self._use_redis:
            return 0
        try:
            count = await self._redis.zcard(REDIS_KEY_QUEUE)
            if count > 0:
                logger.info(f"[QUEUE] Recovered {count} pending tasks from Redis")
            return count
        except Exception:
            return 0

    async def purge_stale(self) -> int:
        """Bulk-remove stale tasks from Redis queue on startup.

        PRE_SCAN: max 5 min, INITIAL: max 15 min, others: 3x stage offset.
        Returns number of purged tasks.
        """
        if not self._use_redis:
            return 0

        from src.parsers.enrichment_types import STAGE_SCHEDULE

        now = asyncio.get_event_loop().time()
        staleness_limits = {
            EnrichmentStage.PRE_SCAN: 300,
            EnrichmentStage.INITIAL: 900,
        }

        try:
            all_tasks = await self._redis.hgetall(REDIS_KEY_TASKS)
        except Exception:
            return 0

        to_remove: list[str] = []
        for tid, task_json in all_tasks.items():
            try:
                data = json.loads(task_json)
                stage = EnrichmentStage[data["stage"]]
                scheduled_at = data["scheduled_at"]
                max_age = staleness_limits.get(
                    stage,
                    STAGE_SCHEDULE[stage].offset_sec * 3,
                )
                if now - scheduled_at > max_age:
                    to_remove.append(tid)
            except Exception:
                to_remove.append(tid)  # malformed task — remove

        if not to_remove:
            return 0

        try:
            pipe = self._redis.pipeline()
            pipe.zrem(REDIS_KEY_QUEUE, *to_remove)
            pipe.hdel(REDIS_KEY_TASKS, *to_remove)
            await pipe.execute()
            logger.info(
                f"[QUEUE] Purged {len(to_remove)} stale tasks "
                f"(of {len(all_tasks)} total)"
            )
        except Exception as e:
            logger.warning(f"[QUEUE] Purge failed: {e}")
            return 0

        return len(to_remove)

    async def migrate_scores(self) -> int:
        """Re-score all tasks in Redis using current _sort_score formula.

        Needed after score formula changes (e.g. adding stage_weight).
        Reads task data from hash, recomputes scores, updates sorted set.
        Returns number of migrated tasks.
        """
        if not self._use_redis:
            return 0

        try:
            all_tasks = await self._redis.hgetall(REDIS_KEY_TASKS)
        except Exception:
            return 0

        if not all_tasks:
            return 0

        migrated = 0
        pipe = self._redis.pipeline()
        for tid, task_json in all_tasks.items():
            try:
                data = json.loads(task_json)
                task = _dict_to_task(data)
                new_score = _sort_score(task)
                pipe.zadd(REDIS_KEY_QUEUE, {tid: new_score})
                migrated += 1
            except Exception:
                continue  # skip malformed tasks (purge_stale handles them)

        if migrated:
            try:
                await pipe.execute()
                logger.info(f"[QUEUE] Migrated scores for {migrated} tasks")
            except Exception as e:
                logger.warning(f"[QUEUE] Score migration failed: {e}")
                return 0

        return migrated

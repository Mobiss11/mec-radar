"""Smart money tracker — caches known smart wallets, checks holder overlap.

Refreshes wallet list from GMGN every 30 min, caches in Redis set.
During enrichment, compares token's top holders against cached smart wallets.
"""

import asyncio
import json

from loguru import logger
from redis.asyncio import Redis

from src.parsers.gmgn.client import GmgnClient
from src.parsers.gmgn.exceptions import GmgnError
from src.parsers.gmgn.models import GmgnSmartWallet, GmgnTopHolder

REDIS_KEY_WALLETS = "smart_money:wallets"  # Redis SET of wallet addresses
REDIS_KEY_WALLET_DATA = "smart_money:data"  # Redis HASH address→json
REFRESH_INTERVAL_SEC = 30 * 60  # 30 min
WALLET_CATEGORIES = ["7d", "30d"]  # GMGN rank periods (top PnL wallets)


class SmartMoneyTracker:
    """Tracks smart wallets and checks token holder overlap."""

    def __init__(self, redis: Redis, gmgn: GmgnClient) -> None:
        self._redis = redis
        self._gmgn = gmgn
        self._local_cache: set[str] = set()  # In-memory fallback

    async def refresh_wallets(self) -> int:
        """Fetch smart wallets from GMGN and cache in Redis. Returns wallet count."""
        all_wallets: list[GmgnSmartWallet] = []
        for category in WALLET_CATEGORIES:
            try:
                wallets = await self._gmgn.get_smart_wallets(category=category, limit=100)
                all_wallets.extend(wallets)
            except GmgnError as e:
                logger.debug(f"[SMART] Failed to fetch {category}: {e}")

        if not all_wallets:
            logger.warning("[SMART] No wallets fetched from GMGN")
            return len(self._local_cache)

        # Deduplicate by address
        unique: dict[str, GmgnSmartWallet] = {}
        for w in all_wallets:
            if w.address:
                unique[w.address] = w

        # Store in Redis
        pipe = self._redis.pipeline()
        pipe.delete(REDIS_KEY_WALLETS)
        pipe.delete(REDIS_KEY_WALLET_DATA)
        if unique:
            pipe.sadd(REDIS_KEY_WALLETS, *unique.keys())
            for addr, wallet in unique.items():
                pipe.hset(
                    REDIS_KEY_WALLET_DATA,
                    addr,
                    json.dumps(wallet.model_dump(mode="json")),
                )
            # Expire in 2x refresh interval as safety net
            pipe.expire(REDIS_KEY_WALLETS, REFRESH_INTERVAL_SEC * 2)
            pipe.expire(REDIS_KEY_WALLET_DATA, REFRESH_INTERVAL_SEC * 2)
        await pipe.execute()

        # Update local cache
        self._local_cache = set(unique.keys())
        logger.info(f"[SMART] Cached {len(unique)} smart wallets across {len(WALLET_CATEGORIES)} categories")
        return len(unique)

    async def check_holders(
        self, holders: list[GmgnTopHolder],
    ) -> list[str]:
        """Check which holders are smart wallets. Returns list of smart wallet addresses."""
        if not holders:
            return []

        holder_addrs = {h.address for h in holders if h.address}
        if not holder_addrs:
            return []

        # Try Redis first
        try:
            if await self._redis.exists(REDIS_KEY_WALLETS):
                matches = await self._redis.sinter(
                    REDIS_KEY_WALLETS,
                    *[]  # Can't sinter with a Python set, use smembers + intersection
                )
                # Actually: check each holder against the set
                smart_addrs: list[str] = []
                for addr in holder_addrs:
                    if await self._redis.sismember(REDIS_KEY_WALLETS, addr):
                        smart_addrs.append(addr)
                return smart_addrs
        except Exception:
            pass

        # Fallback to local cache
        return [addr for addr in holder_addrs if addr in self._local_cache]

    async def check_holders_batch(
        self, holder_addresses: set[str],
    ) -> list[str]:
        """Check addresses against smart wallet cache using pipeline. More efficient."""
        if not holder_addresses:
            return []

        try:
            pipe = self._redis.pipeline()
            for addr in holder_addresses:
                pipe.sismember(REDIS_KEY_WALLETS, addr)
            results = await pipe.execute()
            return [
                addr for addr, is_member in zip(holder_addresses, results) if is_member
            ]
        except Exception:
            # Fallback to local cache
            return [addr for addr in holder_addresses if addr in self._local_cache]

    async def get_wallet_quality(self, addresses: list[str]) -> float:
        """Get average quality score for smart wallets (0.0-1.0 based on win_rate).

        Returns 0.5 (neutral) if no data available.
        """
        if not addresses:
            return 0.5

        qualities: list[float] = []
        try:
            pipe = self._redis.pipeline()
            for addr in addresses:
                pipe.hget(REDIS_KEY_WALLET_DATA, addr)
            results = await pipe.execute()

            for data_json in results:
                if data_json:
                    data = json.loads(data_json)
                    win_rate = data.get("win_rate")
                    if win_rate is not None:
                        qualities.append(float(win_rate) / 100.0)
        except Exception:
            pass

        return sum(qualities) / len(qualities) if qualities else 0.5

    async def get_weighted_count(self, smart_addresses: list[str]) -> float:
        """Compute weighted smart money count by wallet category.

        Category weights:
        - pump_smart: 1.0 (highest quality signal)
        - smart_degen: 0.7 (decent quality)
        - snipe_bot: 0.3 (low quality — automated, not conviction)
        - unknown: 0.5 (default if category missing)

        Returns weighted count (e.g. 2 pump_smart + 1 snipe_bot = 2.3).
        """
        CATEGORY_WEIGHTS = {
            "pump_smart": 1.0,
            "smart_degen": 0.7,
            "snipe_bot": 0.3,
        }
        DEFAULT_WEIGHT = 0.5

        if not smart_addresses:
            return 0.0

        weighted = 0.0
        try:
            pipe = self._redis.pipeline()
            for addr in smart_addresses:
                pipe.hget(REDIS_KEY_WALLET_DATA, addr)
            results = await pipe.execute()

            for data_json in results:
                if data_json:
                    data = json.loads(data_json)
                    category = data.get("category", "")
                    weighted += CATEGORY_WEIGHTS.get(category, DEFAULT_WEIGHT)
                else:
                    weighted += DEFAULT_WEIGHT
        except Exception:
            # Fallback: treat all as default weight
            weighted = len(smart_addresses) * DEFAULT_WEIGHT

        return round(weighted, 2)

    async def refresh_loop(self) -> None:
        """Background task: refresh wallet cache every 30 min."""
        while True:
            try:
                await self.refresh_wallets()
            except Exception as e:
                logger.error(f"[SMART] Refresh failed: {e}")
            await asyncio.sleep(REFRESH_INTERVAL_SEC)

import asyncio


class RateLimiter:
    """Token bucket rate limiter for async HTTP clients."""

    def __init__(self, max_rps: float) -> None:
        self._min_interval = 1.0 / max_rps
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = asyncio.get_event_loop().time()


class SharedRateLimiter:
    """Global rate limiter shared across multiple concurrent workers.

    Uses a single asyncio.Lock to serialize access from all workers,
    ensuring total RPS stays within limit regardless of worker count.
    Pass the SAME instance to all clients that share an API key.
    """

    _instances: dict[str, "SharedRateLimiter"] = {}

    def __init__(self, key: str, max_rps: float) -> None:
        self._key = key
        self._min_interval = 1.0 / max_rps
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    @classmethod
    def get_or_create(cls, key: str, max_rps: float) -> "SharedRateLimiter":
        """Get existing limiter for key or create a new one.

        Thread-safe in asyncio: no await between check and set.
        Must be called BEFORE spawning concurrent workers that share the limiter.
        """
        inst = cls._instances.get(key)
        if inst is None:
            inst = cls(key, max_rps)
            cls._instances[key] = inst
        return inst

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = asyncio.get_event_loop().time()

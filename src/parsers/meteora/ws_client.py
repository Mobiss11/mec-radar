"""WebSocket client for Meteora DBC events via Solana logsSubscribe.

Mirrors the PumpPortalClient pattern: ConnectionState enum, exponential backoff,
typed callbacks. Connects to Helius WS and subscribes to DBC program logs.

logsSubscribe gives us: signature + log messages. We detect new pools and migrations
from the instruction names in logs. The worker then uses MeteoraClient.get_transaction()
to extract full account details.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from enum import Enum

import websockets
from loguru import logger

from src.parsers.meteora.constants import (
    DBC_PROGRAM_ID,
    INSTRUCTION_INIT_POOL,
    INSTRUCTION_INIT_POOL_DYNAMIC,
    INSTRUCTION_MIGRATE_DAMM_V2,
)
from src.parsers.meteora.models import MeteoraMigration, MeteoraNewPool


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ACTIVE = "active"


class MeteoraDBCClient:
    """WebSocket client for Meteora DBC real-time events via Solana logsSubscribe.

    Uses a single connection. Detects new pool creation and migration events
    from transaction logs mentioning the DBC program.
    """

    def __init__(self, ws_url: str) -> None:
        self._ws_url = ws_url
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._state = ConnectionState.DISCONNECTED
        self._running = False
        self._reconnect_delay = 5.0
        self._max_reconnect_delay = 60.0
        self._message_count = 0
        self._subscription_id: int | None = None

        # Typed callbacks
        self.on_new_pool: Callable[[MeteoraNewPool], Awaitable[None]] | None = None
        self.on_migration: Callable[[MeteoraMigration], Awaitable[None]] | None = None
        self._pending_tasks: set[asyncio.Task] = set()

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def message_count(self) -> int:
        return self._message_count

    async def connect(self) -> None:
        """Connect and listen. Auto-reconnects on disconnect."""
        self._running = True
        while self._running:
            try:
                self._state = ConnectionState.CONNECTING
                async with websockets.connect(
                    self._ws_url,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._state = ConnectionState.CONNECTED
                    self._reconnect_delay = 5.0
                    await self._subscribe()
                    self._state = ConnectionState.ACTIVE
                    logger.info("[MDBC] Solana WS connected, logsSubscribe active")
                    await self._listen()
            except (
                websockets.ConnectionClosed,
                ConnectionError,
                OSError,
                TimeoutError,
            ) as e:
                logger.warning(f"[MDBC] WS disconnected: {e}")
                self._state = ConnectionState.DISCONNECTED
                self._ws = None
                self._subscription_id = None
                if self._running:
                    logger.info(f"[MDBC] Reconnecting in {self._reconnect_delay:.0f}s...")
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(
                        self._reconnect_delay * 2, self._max_reconnect_delay
                    )

    async def _subscribe(self) -> None:
        """Send logsSubscribe RPC to listen for DBC program transactions."""
        if not self._ws:
            return
        subscribe_msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [DBC_PROGRAM_ID]},
                {"commitment": "confirmed"},
            ],
        })
        await self._ws.send(subscribe_msg)
        # Wait for subscription confirmation
        try:
            response = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
            data = json.loads(response)
            if "result" in data:
                self._subscription_id = data["result"]
                logger.debug(f"[MDBC] logsSubscribe id={self._subscription_id}")
        except (asyncio.TimeoutError, json.JSONDecodeError) as e:
            logger.warning(f"[MDBC] Subscribe confirmation failed: {e}")

    async def _listen(self) -> None:
        """Process incoming log notifications."""
        if not self._ws:
            return

        async for message in self._ws:
            self._message_count += 1
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue

            # logsSubscribe notifications come as:
            # {"method": "logsNotification", "params": {"result": {"value": {...}}}}
            params = data.get("params")
            if not params:
                continue

            result = params.get("result", {})
            value = result.get("value", {})
            signature = value.get("signature")
            logs = value.get("logs", [])

            if not signature or not logs:
                continue

            # Check for error in transaction
            err = value.get("err")
            if err:
                continue  # Skip failed transactions

            self._process_logs(signature, logs)

    def _process_logs(self, signature: str, logs: list[str]) -> None:
        """Detect DBC events from transaction log lines.

        Instruction names appear in logs as "Instruction: InitializeVirtualPoolWithSplToken"
        (PascalCase, after Anchor decompilation).
        """
        is_new_pool = False
        is_migration_v2 = False

        for line in logs:
            if INSTRUCTION_INIT_POOL in line or INSTRUCTION_INIT_POOL_DYNAMIC in line:
                is_new_pool = True
            elif INSTRUCTION_MIGRATE_DAMM_V2 in line:
                is_migration_v2 = True

        if is_new_pool and self.on_new_pool:
            event = MeteoraNewPool(signature=signature)
            task = asyncio.create_task(self._safe_callback(self.on_new_pool, event))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
        elif is_migration_v2 and self.on_migration:
            event = MeteoraMigration(signature=signature, migration_type="damm_v2")
            task = asyncio.create_task(self._safe_callback(self.on_migration, event))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)

    async def _safe_callback(
        self, callback: Callable[..., Awaitable[None]], event: object
    ) -> None:
        """Execute callback with error handling and timeout."""
        try:
            await asyncio.wait_for(callback(event), timeout=30.0)
        except asyncio.TimeoutError:
            logger.error(f"[MDBC] Callback timed out for {type(event).__name__}")
        except Exception as e:
            logger.error(f"[MDBC] Callback error: {e}")

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._state = ConnectionState.DISCONNECTED

import asyncio
import json
from collections.abc import Awaitable, Callable
from enum import Enum

import websockets
from loguru import logger
from pydantic import ValidationError

from src.parsers.pumpportal.models import (
    PumpPortalMigration,
    PumpPortalNewToken,
    PumpPortalTrade,
)

PUMPPORTAL_WS_URL = "wss://pumpportal.fun/api/data"


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ACTIVE = "active"


class PumpPortalClient:
    """WebSocket client for PumpPortal real-time Pump.fun events.

    CRITICAL: Uses a single connection for all subscriptions (max 15 connections allowed).
    """

    def __init__(self) -> None:
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._state = ConnectionState.DISCONNECTED
        self._running = False
        self._tracked_wallets: set[str] = set()
        self._tracked_tokens: set[str] = set()
        self._reconnect_delay = 5.0
        self._max_reconnect_delay = 60.0
        self._message_count = 0

        # Typed callbacks
        self.on_new_token: Callable[[PumpPortalNewToken], Awaitable[None]] | None = None
        self.on_migration: Callable[[PumpPortalMigration], Awaitable[None]] | None = None
        self.on_trade: Callable[[PumpPortalTrade], Awaitable[None]] | None = None

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
                    PUMPPORTAL_WS_URL,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._state = ConnectionState.CONNECTED
                    self._reconnect_delay = 5.0
                    await self._subscribe_all()
                    self._state = ConnectionState.ACTIVE
                    logger.info("PumpPortal WS connected and subscribed")
                    await self._listen()
            except (
                websockets.ConnectionClosed,
                ConnectionError,
                OSError,
                TimeoutError,
            ) as e:
                logger.warning(f"PumpPortal WS disconnected: {e}")
                self._state = ConnectionState.DISCONNECTED
                self._ws = None
                if self._running:
                    logger.info(f"Reconnecting in {self._reconnect_delay:.0f}s...")
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(
                        self._reconnect_delay * 2, self._max_reconnect_delay
                    )

    async def _subscribe_all(self) -> None:
        if not self._ws:
            return

        await self._ws.send(json.dumps({"method": "subscribeNewToken"}))
        await self._ws.send(json.dumps({"method": "subscribeMigration"}))

        if self._tracked_wallets:
            await self._ws.send(
                json.dumps(
                    {
                        "method": "subscribeAccountTrade",
                        "keys": list(self._tracked_wallets),
                    }
                )
            )

        if self._tracked_tokens:
            await self._ws.send(
                json.dumps(
                    {
                        "method": "subscribeTokenTrade",
                        "keys": list(self._tracked_tokens),
                    }
                )
            )

    async def _listen(self) -> None:
        if not self._ws:
            return

        async for message in self._ws:
            self._message_count += 1
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue

            tx_type = data.get("txType")

            try:
                if tx_type == "create" and self.on_new_token:
                    token = PumpPortalNewToken.model_validate(data)
                    await self.on_new_token(token)
                elif tx_type == "migration" and self.on_migration:
                    migration = PumpPortalMigration.model_validate(data)
                    await self.on_migration(migration)
                elif tx_type in ("buy", "sell") and self.on_trade:
                    trade = PumpPortalTrade.model_validate(data)
                    await self.on_trade(trade)
            except ValidationError as e:
                logger.debug(f"Validation error for {tx_type}: {e}")
            except Exception as e:
                logger.error(f"Error handling {tx_type} event: {e}")

    def track_wallets(self, addresses: list[str]) -> None:
        """Set wallets to track. Takes effect on next reconnect or via dynamic subscribe."""
        self._tracked_wallets = set(addresses)

    def track_tokens(self, mint_addresses: list[str]) -> None:
        """Set tokens to track. Takes effect on next reconnect."""
        self._tracked_tokens = set(mint_addresses)

    async def subscribe_wallets_live(self, addresses: list[str]) -> None:
        """Subscribe to wallet trades on active connection."""
        self._tracked_wallets.update(addresses)
        if self._ws and self._state == ConnectionState.ACTIVE:
            await self._ws.send(
                json.dumps({"method": "subscribeAccountTrade", "keys": addresses})
            )

    async def subscribe_tokens_live(self, mint_addresses: list[str]) -> None:
        """Subscribe to token trades on active connection.

        Allows real-time monitoring of buy/sell activity for tracked tokens.
        """
        self._tracked_tokens.update(mint_addresses)
        if self._ws and self._state == ConnectionState.ACTIVE:
            await self._ws.send(
                json.dumps({"method": "subscribeTokenTrade", "keys": mint_addresses})
            )

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._state = ConnectionState.DISCONNECTED

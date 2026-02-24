"""Chainstack Yellowstone gRPC client for real-time Solana transaction streaming.

Replaces PumpPortal WebSocket with sub-second latency gRPC stream.
Uses the same callback interface (on_new_token, on_migration, on_trade)
so worker.py integration is seamless.

Phase 45: Added Raydium AMM transaction filter for RugGuard LP removal detection.
"""

import asyncio
import struct
from collections.abc import Awaitable, Callable
from decimal import Decimal
from enum import Enum

import base58
import grpc
from loguru import logger

from src.parsers.chainstack.decoder import (
    PUMP_PROGRAM_ID,
    classify_instruction,
    decode_buy_instruction,
    decode_create_instruction,
    decode_sell_instruction,
    is_migration_transaction,
)
from src.parsers.chainstack.proto import geyser_pb2, geyser_pb2_grpc
from src.parsers.pumpportal.models import (
    PumpPortalMigration,
    PumpPortalNewToken,
    PumpPortalTrade,
)

# Raydium AMM v4 program ID
RAYDIUM_AMM_PROGRAM_ID = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

# Raydium AMM v4 instruction discriminators (first 1 byte = instruction index)
# removeLiquidity = instruction index 4 in Raydium AMM v4
RAYDIUM_REMOVE_LIQUIDITY_IX = 4
# withdrawSRM = 5, swap = 9, etc.
# We only care about removeLiquidity for rug detection


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ACTIVE = "active"


class ChainstackGrpcClient:
    """Yellowstone gRPC client streaming pump.fun transactions from Chainstack.

    Uses the same PumpPortalNewToken/Trade/Migration models for compatibility
    with existing worker.py handlers.
    """

    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        username: str = "",
        password: str = "",
    ) -> None:
        self._endpoint = endpoint
        self._token = token
        self._username = username
        self._password = password
        self._state = ConnectionState.DISCONNECTED
        self._running = False
        self._reconnect_delay = 3.0
        self._max_reconnect_delay = 60.0
        self._message_count = 0
        self._token_count = 0
        self._trade_count = 0
        self._reconnect_count = 0
        self._decode_errors = 0
        self._lp_removal_count = 0

        # Callbacks — same interface as PumpPortalClient
        self.on_new_token: Callable[[PumpPortalNewToken], Awaitable[None]] | None = None
        self.on_migration: Callable[[PumpPortalMigration], Awaitable[None]] | None = None
        self.on_trade: Callable[[PumpPortalTrade], Awaitable[None]] | None = None
        # Phase 45: RugGuard — LP removal callback (mint, signature, sol_amount, token_amount)
        self.on_lp_removal: Callable[[str, str, int, int], Awaitable[None]] | None = None

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def message_count(self) -> int:
        return self._message_count

    async def connect(self) -> None:
        """Connect to Chainstack gRPC and stream pump.fun transactions.

        Auto-reconnects on disconnect with exponential backoff.
        """
        self._running = True
        while self._running:
            channel: grpc.aio.Channel | None = None
            try:
                self._state = ConnectionState.CONNECTING
                channel = self._create_channel()
                stub = geyser_pb2_grpc.GeyserStub(channel)

                self._state = ConnectionState.CONNECTED
                self._reconnect_delay = 3.0
                logger.info(
                    f"[GRPC] Connected to Chainstack gRPC ({self._endpoint})"
                )

                request = self._build_subscribe_request()
                self._state = ConnectionState.ACTIVE
                _filters = []
                if self._has_pump_discovery:
                    _filters.append("pump.fun")
                if self.on_lp_removal:
                    _filters.append("raydium_amm")
                logger.info(f"[GRPC] Subscribed to {'+'.join(_filters)} transactions (PROCESSED)")

                async for update in stub.Subscribe(iter([request])):
                    if not self._running:
                        break
                    self._message_count += 1
                    await self._process_update(update)

                    # Periodic stats logging every 1000 messages
                    if self._message_count % 1000 == 0:
                        logger.info(
                            f"[GRPC] Stats: {self._message_count} msgs, "
                            f"{self._token_count} tokens, {self._trade_count} trades, "
                            f"{self._lp_removal_count} lp_removals, "
                            f"{self._decode_errors} decode errors"
                        )

            except grpc.RpcError as e:
                logger.warning(f"[GRPC] Connection error: {e.code()} — {e.details()}")
            except asyncio.CancelledError:
                logger.info("[GRPC] Stream cancelled")
                break
            except Exception as e:
                logger.warning(f"[GRPC] Unexpected error: {e}")
            finally:
                self._state = ConnectionState.DISCONNECTED
                if channel is not None:
                    try:
                        await asyncio.wait_for(channel.close(), timeout=5.0)
                    except asyncio.TimeoutError:
                        logger.warning("[GRPC] Channel close timeout")
                    except Exception:
                        pass

            if self._running:
                self._reconnect_count += 1
                logger.info(
                    f"[GRPC] Reconnecting in {self._reconnect_delay:.0f}s "
                    f"(reconnect #{self._reconnect_count})"
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay
                )

    def _create_channel(self) -> grpc.aio.Channel:
        """Create authenticated gRPC channel with keepalive."""
        auth = grpc.metadata_call_credentials(
            lambda _, callback: callback(
                (("x-token", self._token),),
                None,
            )
        )
        creds = grpc.composite_channel_credentials(
            grpc.ssl_channel_credentials(),
            auth,
        )
        options = [
            ("grpc.keepalive_time_ms", 30_000),
            ("grpc.keepalive_timeout_ms", 10_000),
            ("grpc.keepalive_permit_without_calls", True),
            ("grpc.http2.min_time_between_pings_ms", 10_000),
            ("grpc.max_receive_message_length", 64 * 1024 * 1024),  # 64MB
        ]
        return grpc.aio.secure_channel(self._endpoint, creds, options=options)

    @property
    def _has_pump_discovery(self) -> bool:
        """True if any pump.fun discovery callback is set."""
        return bool(self.on_new_token or self.on_migration or self.on_trade)

    def _build_subscribe_request(self) -> geyser_pb2.SubscribeRequest:
        """Build subscription for active filters only.

        pump.fun filter is only added when discovery callbacks are set.
        Raydium AMM filter is only added when on_lp_removal is set (RugGuard).
        At least one filter must be present.
        """
        request = geyser_pb2.SubscribeRequest()

        # Filter 1: pump.fun transactions (token discovery, trades, migrations)
        # Only added if discovery callbacks are registered
        if self._has_pump_discovery:
            tx_filter = request.transactions["pump_txs"]
            tx_filter.account_include.append(PUMP_PROGRAM_ID)
            tx_filter.failed = False

        # Filter 2: Raydium AMM transactions (LP removal detection for RugGuard)
        # Only added if on_lp_removal callback is set to avoid unnecessary traffic
        if self.on_lp_removal:
            raydium_filter = request.transactions["raydium_amm_txs"]
            raydium_filter.account_include.append(RAYDIUM_AMM_PROGRAM_ID)
            raydium_filter.failed = False
            logger.info("[GRPC] Raydium AMM filter added for RugGuard LP monitoring")

        # Fastest commitment level
        request.commitment = geyser_pb2.CommitmentLevel.PROCESSED

        return request

    async def _process_update(
        self, update: geyser_pb2.SubscribeUpdate
    ) -> None:
        """Process a single gRPC update — decode and dispatch to callbacks."""
        if not update.HasField("transaction"):
            return

        tx_wrapper = update.transaction
        tx = tx_wrapper.transaction
        msg = tx.transaction.message
        if not msg:
            return

        account_keys = list(msg.account_keys)
        signature = base58.b58encode(bytes(tx.signature)).decode()

        # Check for migration first
        if is_migration_transaction(account_keys):
            try:
                await asyncio.wait_for(
                    self._handle_migration(signature, account_keys),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.error(f"[GRPC] Migration callback timeout: {signature[:16]}")
            except Exception as e:
                logger.error(f"[GRPC] Migration handler error: {e}")
            return

        # Check which filter matched (pump.fun or Raydium)
        _has_pump = False
        _has_raydium = False
        _raydium_amm_bytes = base58.b58decode(RAYDIUM_AMM_PROGRAM_ID)

        for ix in msg.instructions:
            program_idx = ix.program_id_index
            if program_idx >= len(account_keys):
                continue
            prog_bytes = bytes(account_keys[program_idx])
            prog_id = base58.b58encode(prog_bytes).decode()
            if prog_id == PUMP_PROGRAM_ID:
                _has_pump = True
            if prog_bytes == _raydium_amm_bytes:
                _has_raydium = True

        # Phase 45: Handle Raydium AMM transactions (RugGuard)
        if _has_raydium and self.on_lp_removal:
            await self._handle_raydium_tx(signature, msg, account_keys)

        # Process pump.fun instructions
        if _has_pump:
            for ix in msg.instructions:
                program_idx = ix.program_id_index
                if program_idx >= len(account_keys):
                    continue

                program_id = base58.b58encode(bytes(account_keys[program_idx])).decode()
                if program_id != PUMP_PROGRAM_ID:
                    continue

                ix_type = classify_instruction(bytes(ix.data))
                if not ix_type:
                    self._decode_errors += 1
                    continue

                ix_accounts = list(ix.accounts)

                if ix_type == "create":
                    await self._handle_create(
                        signature, bytes(ix.data), account_keys, ix_accounts
                    )
                elif ix_type == "buy":
                    await self._handle_buy(
                        signature, bytes(ix.data), account_keys, ix_accounts
                    )
                elif ix_type == "sell":
                    await self._handle_sell(
                        signature, bytes(ix.data), account_keys, ix_accounts
                    )

    async def _handle_raydium_tx(
        self,
        signature: str,
        msg: "Any",
        account_keys: list[bytes],
    ) -> None:
        """Handle Raydium AMM transaction — detect removeLiquidity.

        Raydium AMM v4 removeLiquidity instruction:
        - Instruction index byte 0 = 4 (removeLiquidity)
        - Accounts layout: [amm, authority, openOrders, lpMint, coinMint, pcMint,
                            poolCoinToken, poolPcToken, withdrawQueue, lpWallet, ...]
        - coinMint (index 4) or pcMint (index 5) is the token mint
        - We extract the non-SOL mint as the token being rugged

        SOL mint = "So11111111111111111111111111111111111111112"
        """
        _raydium_bytes = base58.b58decode(RAYDIUM_AMM_PROGRAM_ID)
        _sol_mint = "So11111111111111111111111111111111111111112"

        for ix in msg.instructions:
            program_idx = ix.program_id_index
            if program_idx >= len(account_keys):
                continue
            if bytes(account_keys[program_idx]) != _raydium_bytes:
                continue

            ix_data = bytes(ix.data)
            if len(ix_data) < 1:
                continue

            # Raydium AMM v4 uses a single-byte instruction index
            ix_index = ix_data[0]
            if ix_index != RAYDIUM_REMOVE_LIQUIDITY_IX:
                continue

            # Found removeLiquidity! Extract token mint from accounts.
            # Raydium AMM v4 removeLiquidity account layout:
            # [0] amm, [1] authority, [2] openOrders, [3] targetOrders,
            # [4] lpMint, [5] coinMint, [6] pcMint,
            # [7] poolCoinToken, [8] poolPcToken,
            # [9] withdrawQueue, [10] tempLpToken, [11] lpWallet,
            # [12] coinWallet, [13] pcWallet, [14] tokenProgram
            ix_accounts = list(ix.accounts)
            if len(ix_accounts) < 7:
                continue

            def _get_account(idx: int) -> str:
                if idx >= len(ix_accounts):
                    return ""
                ai = ix_accounts[idx]
                if ai >= len(account_keys):
                    return ""
                return base58.b58encode(bytes(account_keys[ai])).decode()

            coin_mint = _get_account(5)
            pc_mint = _get_account(6)

            # Pick the non-SOL mint as the token
            mint = ""
            if coin_mint and coin_mint != _sol_mint:
                mint = coin_mint
            elif pc_mint and pc_mint != _sol_mint:
                mint = pc_mint

            if not mint:
                continue

            # Parse LP amount from instruction data if available
            # removeLiquidity data: [1 byte ix_index][8 bytes lp_amount]
            lp_amount = 0
            if len(ix_data) >= 9:
                try:
                    lp_amount = struct.unpack_from("<Q", ix_data, 1)[0]
                except struct.error:
                    pass

            self._lp_removal_count += 1

            try:
                await asyncio.wait_for(
                    self.on_lp_removal(mint, signature, lp_amount, 0),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.error(f"[GRPC] on_lp_removal timeout: {mint[:16]}")
            except Exception as e:
                logger.error(f"[GRPC] Error in on_lp_removal callback: {e}")

    async def _handle_create(
        self,
        signature: str,
        ix_data: bytes,
        account_keys: list[bytes],
        ix_accounts: list[int],
    ) -> None:
        """Handle pump.fun create (new token) instruction."""
        if not self.on_new_token:
            return

        decoded = decode_create_instruction(ix_data, account_keys, ix_accounts)
        if not decoded or not decoded["mint"]:
            return

        token = PumpPortalNewToken(
            signature=signature,
            mint=decoded["mint"],
            name=decoded.get("name"),
            symbol=decoded.get("symbol"),
            uri=decoded.get("uri"),
            traderPublicKey=decoded.get("creator"),
            txType="create",
            bondingCurveKey=decoded.get("bonding_curve_key"),
        )

        self._token_count += 1
        try:
            await asyncio.wait_for(self.on_new_token(token), timeout=5.0)
        except asyncio.TimeoutError:
            logger.error(f"[GRPC] on_new_token timeout: {token.mint[:16]}")
        except Exception as e:
            logger.error(f"[GRPC] Error in on_new_token callback: {e}")

    async def _handle_buy(
        self,
        signature: str,
        ix_data: bytes,
        account_keys: list[bytes],
        ix_accounts: list[int],
    ) -> None:
        """Handle pump.fun buy instruction."""
        if not self.on_trade:
            return

        decoded = decode_buy_instruction(ix_data, account_keys, ix_accounts)
        if not decoded or not decoded["mint"]:
            return

        # Convert lamports to SOL
        sol_amount = Decimal(decoded.get("max_sol_cost", 0)) / Decimal(10**9)

        trade = PumpPortalTrade(
            signature=signature,
            mint=decoded["mint"],
            txType="buy",
            traderPublicKey=decoded.get("trader", ""),
            tokenAmount=Decimal(decoded.get("token_amount", 0)),
            solAmount=sol_amount,
            bondingCurveKey=decoded.get("bonding_curve_key"),
        )

        self._trade_count += 1
        try:
            await asyncio.wait_for(self.on_trade(trade), timeout=5.0)
        except asyncio.TimeoutError:
            logger.error(f"[GRPC] on_trade(buy) timeout: {trade.mint[:16]}")
        except Exception as e:
            logger.debug(f"[GRPC] Error in on_trade callback: {e}")

    async def _handle_sell(
        self,
        signature: str,
        ix_data: bytes,
        account_keys: list[bytes],
        ix_accounts: list[int],
    ) -> None:
        """Handle pump.fun sell instruction."""
        if not self.on_trade:
            return

        decoded = decode_sell_instruction(ix_data, account_keys, ix_accounts)
        if not decoded or not decoded["mint"]:
            return

        sol_amount = Decimal(decoded.get("min_sol_output", 0)) / Decimal(10**9)

        trade = PumpPortalTrade(
            signature=signature,
            mint=decoded["mint"],
            txType="sell",
            traderPublicKey=decoded.get("trader", ""),
            tokenAmount=Decimal(decoded.get("token_amount", 0)),
            solAmount=sol_amount,
            bondingCurveKey=decoded.get("bonding_curve_key"),
        )

        self._trade_count += 1
        try:
            await asyncio.wait_for(self.on_trade(trade), timeout=5.0)
        except asyncio.TimeoutError:
            logger.error(f"[GRPC] on_trade(sell) timeout: {trade.mint[:16]}")
        except Exception as e:
            logger.debug(f"[GRPC] Error in on_trade callback: {e}")

    async def _handle_migration(
        self,
        signature: str,
        account_keys: list[bytes],
    ) -> None:
        """Handle Raydium migration transaction."""
        if not self.on_migration:
            return

        # Extract mint from transaction accounts.
        # Migration txs: pump program + Raydium + system programs + the mint.
        # We skip all known programs and pick the first real token mint.
        _SKIP_PREFIXES = ("1111", "Sysvar", "Token", "AToken", "Memo")
        _SKIP_PROGRAMS = {
            base58.b58decode(PUMP_PROGRAM_ID),
            base58.b58decode("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"),  # Raydium AMM
            base58.b58decode("27haf8L6oxUeXrHrgEgsexjSY5hbVUWEmvv9Nyxg8vQv"),  # Raydium Authority
            base58.b58decode("11111111111111111111111111111111"),  # System Program
            base58.b58decode("ComputeBudget111111111111111111111111111111"),
        }
        mint = ""
        for key in account_keys:
            key_bytes = bytes(key)
            if len(key_bytes) != 32 or key_bytes in _SKIP_PROGRAMS:
                continue
            candidate = base58.b58encode(key_bytes).decode()
            if any(candidate.startswith(p) for p in _SKIP_PREFIXES):
                continue
            mint = candidate
            break

        if not mint:
            return

        migration = PumpPortalMigration(
            mint=mint,
            signature=signature,
        )

        try:
            await self.on_migration(migration)
        except Exception as e:
            logger.error(f"[GRPC] Error in on_migration callback: {e}")

    async def stop(self) -> None:
        """Gracefully stop the gRPC stream."""
        self._running = False
        self._state = ConnectionState.DISCONNECTED
        logger.info(
            f"[GRPC] Stopped. Processed {self._message_count} messages, "
            f"{self._token_count} tokens, {self._trade_count} trades, "
            f"{self._lp_removal_count} lp_removals"
        )

# Meteora DBC Tracker -- Research & Integration Plan

## 1. Program Identification

| Item | Value |
|------|-------|
| **DBC Program ID** | `dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN` |
| **Pool Authority** | `FhVo3mqL8PW5pH5U2CN4XE33DokiyZnUwuGpH2hmHLuM` |
| **Networks** | Mainnet + Devnet |
| **DAMM v2 Program** | `cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG` |

---

## 2. DAMM v2 REST API (Post-Migration Pools)

**Base URL:** `https://damm-v2.datapi.meteora.ag`
**Rate Limit:** 10 RPS
**Devnet Base URL:** see `https://docs.meteora.ag/api-reference/damm-v2/devnet`

### 2.1 Endpoints

#### GET /pools
Paginated list of all DAMM v2 pools.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `page` | int | no | 1 | Page number (1-based, min 1) |
| `page_size` | int | no | 20 | Items per page (max 1000) |
| `query` | string | no | - | Search by name, token symbol/mint, or pool address |
| `sort_by` | string | no | `volume_24h:desc` | Sort field + direction. Fields: `volume_*`, `fee_*`, `fee_tvl_ratio_*`, `apr_*`, `tvl`, `fee_pct`, `bin_step`, `pool_created_at`, `farm_apy` |
| `filter_by` | string | no | - | AND-joined conditions: numeric (`=`, `>`, `>=`, `<`, `<=`), boolean (`=true`), text (exact/multi-value OR) |

**Response 200:**
```json
{
  "total": 42,
  "pages": 3,
  "current_page": 1,
  "page_size": 20,
  "data": [PoolObject, ...]
}
```

#### GET /pools/{address}
Single pool by address.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `address` | string (path) | yes | Base58-encoded pool address |

**Response 200 -- PoolResponse:**
```
{
  address, name, created_at,
  pool_config: { fee_modes, rates, pool_type },
  token_x: { address, symbol, decimals, price, market_cap },
  token_y: { ... },
  token_x_amount, token_y_amount,
  tvl, vault_x, vault_y, alpha_vault,
  permanent_lock_liquidity,
  vested_liquidity: { three_month, six_month },
  current_price,
  volume: { 30m, 1h, 2h, 4h, 12h, 24h },
  fees: { ... }, protocol_fees: { ... }, fee_tvl_ratio: { ... },
  has_farm, farm_apr, farm_apy,
  is_blacklisted, tags, launchpad
}
```

#### GET /pools/{address}/ohlcv

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `address` | string (path) | yes | - | Pool address |
| `timeframe` | string | no | `24h` | `5m`, `30m`, `1h`, `2h`, `4h`, `12h`, `24h` |
| `start_time` | int | no | inferred | Unix seconds (inclusive) |
| `end_time` | int | no | now | Unix seconds (inclusive) |

**Response 200:**
```json
{
  "start_time": 1700000000,
  "end_time": 1700086400,
  "timeframe": "1h",
  "data": [
    { "timestamp": 1700000000, "timestamp_str": "...", "open": 1.23, "high": 1.45, "low": 1.20, "close": 1.40, "volume": 50000 }
  ]
}
```

#### GET /pools/{address}/volume/history

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `address` | string (path) | yes | - |
| `timeframe` | string | no | `24h` |
| `start_time` | int | no | inferred |
| `end_time` | int | no | now |

**Response 200:**
```json
{
  "start_time": ..., "end_time": ..., "timeframe": "...",
  "data": [
    { "timestamp": ..., "timestamp_str": "...", "volume": 50000, "fees": 50, "protocol_fees": 5 }
  ]
}
```

#### GET /pools/groups
Paginated list of pool groups (same token pair, different configs).

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `page` | int | no | 1 |
| `page_size` | int | no | 20 (max 100) |
| `query` | string | no | - |
| `sort_by` | string | no | `volume_24h:desc` |
| `filter_by` | string | no | - |
| `volume_tw` | string | no | `volume_24h` |
| `fee_tvl_ratio_tw` | string | no | `fee_tvl_ratio_24h` |

**Response 200:**
```json
{
  "total": ..., "pages": ..., "current_page": ..., "page_size": ...,
  "data": [
    { "group_name": "SOL/USDC", "token_x": "...", "token_y": "...", "pool_count": 3, "total_tvl": 500000, "total_volume": 100000, "max_fee_tvl_ratio": 0.05, "has_farm": true, "max_farm_apr": 12.5, "lexical_order_mints": "..." }
  ]
}
```

#### GET /pools/groups/{lexical_order_mints}
Pools within a specific group.

#### GET /stats/protocol_metrics
Protocol-level aggregated metrics (TVL, volume, fees).

---

## 3. DBC On-Chain Program -- Instructions

### 3.1 All Instructions (from IDL)

| Instruction | Discriminator | Purpose |
|-------------|--------------|---------|
| `initialize_virtual_pool_with_spl_token` | `[140, 85, 215, 176, 102, 54, 104, 79]` | Create new DBC pool (SPL token) |
| `initialize_virtual_pool_with_token2022` | - | Create new DBC pool (Token2022) |
| `swap` | - | Trade on bonding curve |
| `swap2` | - | Alternative swap with different params |
| `migrate_meteora_damm` | - | Graduate to DAMM v1 |
| `migration_damm_v2` | - | Graduate to DAMM v2 |
| `migration_meteora_damm_create_metadata` | - | Pre-migration metadata for v1 |
| `migration_damm_v2_create_metadata` | - | Pre-migration metadata for v2 |
| `migrate_meteora_damm_lock_lp_token` | - | Lock LP after v1 migration |
| `migrate_meteora_damm_claim_lp_token` | - | Claim LP after v1 migration |
| `create_config` | - | Create pool configuration |
| `create_locker` | - | Create vesting locker |
| `create_partner_metadata` | - | Partner metadata |
| `create_virtual_pool_metadata` | - | Pool metadata (name/logo/website) |
| `claim_trading_fee` | - | Claim accumulated trading fees |
| `claim_creator_trading_fee` | - | Creator-specific fee claim |
| `claim_pool_creation_fee` | - | Claim pool creation fee |
| `claim_protocol_fee` | - | Protocol fee claim |
| `creator_withdraw_surplus` | - | Creator withdraws leftover |
| `partner_withdraw_surplus` | - | Partner withdraws leftover |
| `protocol_withdraw_surplus` | - | Protocol withdraws leftover |
| `withdraw_leftover` | - | Withdraw remaining base tokens |
| `withdraw_migration_fee` | - | Withdraw migration fees |
| `transfer_pool_creator` | - | Transfer pool creator role |

### 3.2 Key Instruction: `initialize_virtual_pool_with_spl_token`

**Parameters (InitializePoolParameters):**
- `name` (string) -- Token name
- `symbol` (string) -- Token symbol
- `uri` (string) -- Metadata URI

**Key Accounts:**
- `config` -- PoolConfig PDA
- `creator` -- Pool creator (signer)
- `base_mint` -- New token mint
- `quote_mint` -- Quote token (SOL/USDC)
- `pool` -- VirtualPool PDA (derived from mints + config)
- `base_vault` -- Token vault
- `quote_vault` -- Quote vault
- `metadata_program`, `token_program`, `system_program`

### 3.3 Key Instruction: `swap`

**Parameters (SwapParameters):**
- Trade direction, amount in/out, minimum output (slippage)

**Key Accounts:**
- `pool` -- VirtualPool
- `config` -- PoolConfig
- `base_vault`, `quote_vault`
- `user_base_token`, `user_quote_token`
- `referral` (optional)

### 3.4 Migration Instructions

**DAMM v1 flow (5 steps):**
1. `migration_meteora_damm_create_metadata` -- create migration metadata account
2. `create_locker` (if locked vesting) -- create vesting locker
3. `migrate_meteora_damm` -- execute migration, create DAMM v1 pool
4. `migrate_meteora_damm_lock_lp_token` -- lock LP (if permanent)
5. `migrate_meteora_damm_claim_lp_token` -- claim LP (if % configured)

**DAMM v2 flow (2 steps):**
1. `create_locker` (if applicable) -- vesting
2. `migration_damm_v2` -- execute migration to DAMM v2 pool

**Migration Thresholds (automated keepers, mainnet only):**
- Keeper 1: 10 SOL, 750 USDC, or 1500 JUP
- Keeper 2: >= 750 USD equivalent in quote token

---

## 4. On-Chain Events (Emitted via Anchor Events)

### 4.1 All Events (24 total)

| Event | Key Fields |
|-------|------------|
| `EvtInitializePool` | pool, config, creator, base_mint, pool_type, activation_point |
| `EvtSwap` | pool, config, trade_direction, has_referral, params, swap_result, amount_in, current_timestamp |
| `EvtSwap2` | Similar to EvtSwap with SwapResult2 |
| `EvtCurveComplete` | pool, config, base_reserve, quote_reserve |
| `EvtCreateMeteoraMigrationMetadata` | virtual_pool |
| `EvtClaimTradingFee` | - |
| `EvtClaimCreatorTradingFee` | - |
| `EvtPartnerClaimPoolCreationFee` | - |
| `EvtClaimProtocolLiquidityMigrationFee` | - |
| `EvtPartnerWithdrawMigrationFee` | - |
| `EvtWithdrawMigrationFee` | - |

**Key events for tracking:**
- `EvtInitializePool` -- new token launch detected
- `EvtSwap` / `EvtSwap2` -- trades on bonding curve
- `EvtCurveComplete` -- bonding curve filled, ready for migration
- `migrate_meteora_damm` / `migration_damm_v2` instructions -- actual graduation

---

## 5. VirtualPool Account Data Layout (416 bytes, zero_copy)

```rust
pub struct VirtualPool {
    // Volatility tracking for dynamic fees
    pub volatility_tracker: VolatilityTracker,  // nested struct

    // Core references
    pub config: Pubkey,           // PoolConfig address
    pub creator: Pubkey,          // Pool creator wallet
    pub base_mint: Pubkey,        // Token mint address
    pub base_vault: Pubkey,       // Token vault
    pub quote_vault: Pubkey,      // SOL/USDC vault

    // Reserves
    pub base_reserve: u64,        // Current base token amount
    pub quote_reserve: u64,       // Current quote token amount

    // Accumulated fees
    pub protocol_base_fee: u64,
    pub protocol_quote_fee: u64,
    pub partner_base_fee: u64,
    pub partner_quote_fee: u64,

    // Price state
    pub sqrt_price: u128,         // Current sqrt price (Q64.64 or similar)
    pub activation_point: u64,    // Slot or timestamp when pool activates

    // Status flags
    pub pool_type: u8,            // 0 = SPL, 1 = Token2022
    pub is_migrated: u8,          // 0 = active bonding curve, 1 = migrated
    pub is_partner_withdraw_surplus: u8,
    pub is_protocol_withdraw_surplus: u8,
    pub migration_progress: u8,   // Migration step progress
    pub is_withdraw_leftover: u8,
    pub is_creator_withdraw_surplus: u8,
    pub migration_fee_withdraw_status: u8,

    // Metrics
    pub metrics: PoolMetrics,     // Volume/trade tracking
    pub finish_curve_timestamp: u64, // When curve completed (0 = not yet)

    // Creator fees
    pub creator_base_fee: u64,
    pub creator_quote_fee: u64,

    // Creation fee tracking
    pub legacy_creation_fee_bits: u8,
    pub creation_fee_bits: u8,
    pub has_swap: u8,             // Whether any swap has occurred
    pub _padding_0: [u8; 5],

    // Migration fees
    pub protocol_liquidity_migration_fee_bps: u16,
    pub _padding_1: [u8; 6],
    pub protocol_migration_base_fee_amount: u64,
    pub protocol_migration_quote_fee_amount: u64,
    pub _padding_2: [u64; 3],
}
```

**Account Discriminator:** First 8 bytes = Anchor account discriminator for `VirtualPool`
**Total Size:** 416 bytes (verified by `const_assert_eq!`)

### 5.1 Key Fields for Tracker

| Field | Offset | What it tells us |
|-------|--------|-----------------|
| `config` | after volatility_tracker | Link to PoolConfig (migration thresholds, fees) |
| `creator` | +32 | Who created the token |
| `base_mint` | +32 | Token mint address to look up metadata |
| `base_reserve` / `quote_reserve` | +8 each | Current liquidity in pool |
| `sqrt_price` | +16 | Current price (use `get_price_from_sqrt_price()`) |
| `is_migrated` | +1 | `0` = bonding curve active, `1` = graduated |
| `migration_progress` | +1 | Step of migration process |
| `finish_curve_timestamp` | +8 | Non-zero = curve filled, migration pending |
| `has_swap` | +1 | Whether trading has started |

---

## 6. PoolConfig Account Data Layout

```rust
pub struct PoolConfig {
    pub quote_mint: Pubkey,
    pub fee_claimer: Pubkey,
    pub leftover_receiver: Pubkey,
    pub pool_fees: PoolFeesConfig,
    pub partner_liquidity_vesting_info: LiquidityVestingInfo,
    pub creator_liquidity_vesting_info: LiquidityVestingInfo,
    pub padding_0: [u8; 14],
    pub padding_1: u16,
    pub collect_fee_mode: u8,
    pub migration_option: u8,        // 0 = DAMM v1, 1 = DAMM v2
    pub activation_type: u8,         // Slot-based or Timestamp-based
    pub token_decimal: u8,           // 6 or 9
    pub version: u8,
    pub token_type: u8,              // 0 = SPL, 1 = Token2022
    pub quote_token_flag: u8,
    pub partner_permanent_locked_liquidity_percentage: u8,
    pub partner_liquidity_percentage: u8,
    pub creator_permanent_locked_liquidity_percentage: u8,
    pub creator_liquidity_percentage: u8,
    pub migration_fee_option: u8,
    pub fixed_token_supply_flag: u8,
    pub creator_trading_fee_percentage: u8,
    pub token_update_authority: u8,
    pub migration_fee_percentage: u8,
    pub creator_migration_fee_percentage: u8,
    pub padding_2: [u8; 7],
    pub swap_base_amount: u64,
    pub migration_quote_threshold: u64,   // SOL/USDC needed to graduate
    pub migration_base_threshold: u64,
    pub migration_sqrt_price: u128,       // Price at graduation
    pub locked_vesting_config: LockedVestingConfig,
    pub pre_migration_token_supply: u64,
    pub post_migration_token_supply: u64,
    pub migrated_collect_fee_mode: u8,
    pub migrated_dynamic_fee: u8,
    pub migrated_pool_fee_bps: u16,
    pub migrated_pool_base_fee_mode: u8,
    pub enable_first_swap_with_min_fee: u8,
    pub _padding_1: [u8; 2],
    pub pool_creation_fee: u64,
    pub migrated_pool_base_fee_bytes: [u8; 16],
    pub sqrt_start_price: u128,           // Initial bonding curve price
    pub curve: [LiquidityDistributionConfig; 16],  // Up to 16 curve segments
}
```

### 6.1 Nested Types

```rust
pub struct PoolFeesConfig {
    pub base_fee: BaseFeeConfig,
    pub dynamic_fee: DynamicFeeConfig,
}

pub struct BaseFeeConfig {
    pub cliff_fee_numerator: u64,
    pub second_factor: u64,
    pub third_factor: u64,
    pub first_factor: u16,
    pub base_fee_mode: u8,    // 0=Linear, 1=Exponential, 2=RateLimiter
    pub padding_0: [u8; 5],
}

pub struct DynamicFeeConfig {
    pub initialized: u8,
    pub padding: [u8; 7],
    pub max_volatility_accumulator: u32,
    pub variable_fee_control: u32,
    pub bin_step: u16,
    pub filter_period: u16,
    pub decay_period: u16,
    pub reduction_factor: u16,
    pub padding2: [u8; 8],
    pub bin_step_u128: u128,
}

// Enums
pub enum MigrationOption { MeteoraDamm = 0, DammV2 = 1 }
pub enum BaseFeeMode { FeeSchedulerLinear, FeeSchedulerExponential, RateLimiter }
pub enum TokenType { SplToken, Token2022 }
```

---

## 7. Bonding Curve Formula

**Constant product:** `x * y = k` where:
- `x` = Virtual Base Reserve
- `y` = Virtual Curve Reserve
- `Liquidity = sqrt(x * y)`

**Up to 16 segments**, each with:
- `L_i` -- liquidity level
- `P_(i-1)` to `P_i` -- price boundaries (sqrt prices)

**Total base tokens in curve:**
```
BondingCurveAmount = Sum_i [ L_i * (1/sqrt(P_(i-1)) - 1/sqrt(P_i)) ]
```

**Migration quote threshold (graduation target):**
```
MigrationQuoteThreshold = Sum_i [ L_i * (sqrt(P_i) - sqrt(P_(i-1))) ]
```

**Migration price:**
```
P_max^2 = (MigrationQuoteThreshold * (1 - MigrationFee%)) / MigrationAmount
```

**Graduation progress calculation:**
```python
progress = pool.quote_reserve / config.migration_quote_threshold
# 0.0 = just launched, 1.0 = ready to graduate
```

---

## 8. Solana WebSocket RPC -- Real-Time Subscription

### 8.1 programSubscribe Method

Subscribe to ALL account changes for accounts owned by the DBC program.

**JSON-RPC Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "programSubscribe",
  "params": [
    "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN",
    {
      "encoding": "base64",
      "commitment": "confirmed",
      "filters": [
        { "dataSize": 416 }
      ]
    }
  ]
}
```

**Filters (AND logic, all must match):**
- `dataSize`: filter by account size (416 = VirtualPool)
- `memcmp`: compare bytes at offset
  ```json
  { "memcmp": { "offset": 0, "bytes": "<base58 discriminator>" } }
  ```

**Notification format:**
```json
{
  "jsonrpc": "2.0",
  "method": "programNotification",
  "params": {
    "subscription": 12345,
    "result": {
      "context": { "slot": 123456789 },
      "value": {
        "pubkey": "PoolAccountAddress...",
        "account": {
          "lamports": 1234567,
          "data": ["base64encodeddata...", "base64"],
          "owner": "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN",
          "executable": false,
          "rentEpoch": 123,
          "space": 416
        }
      }
    }
  }
}
```

### 8.2 logsSubscribe Method

Subscribe to transaction logs mentioning the DBC program (catches all instructions + events).

**JSON-RPC Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "logsSubscribe",
  "params": [
    { "mentions": ["dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"] },
    { "commitment": "confirmed" }
  ]
}
```

**Notification includes:**
- Transaction signature
- Log messages (Anchor event data is base64-encoded in logs prefixed with `Program data:`)

---

## 9. Python Integration Approaches

### 9.1 Approach A: solana-py WebSocket (Direct RPC)

```python
import asyncio
import base64
import struct
from solana.rpc.websocket_api import connect
from solders.pubkey import Pubkey

DBC_PROGRAM = "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"
VIRTUAL_POOL_SIZE = 416

async def monitor_dbc_pools(rpc_ws_url: str) -> None:
    async with connect(rpc_ws_url) as websocket:
        # Subscribe to all VirtualPool account changes
        await websocket.program_subscribe(
            Pubkey.from_string(DBC_PROGRAM),
            encoding="base64",
            commitment="confirmed",
            filters=[{"dataSize": VIRTUAL_POOL_SIZE}],
        )
        first_resp = await websocket.recv()
        subscription_id = first_resp[0].result
        print(f"Subscribed: {subscription_id}")

        async for msg in websocket:
            for notification in msg:
                if hasattr(notification, "result"):
                    value = notification.result.value
                    pubkey = str(value.pubkey)
                    data = base64.b64decode(value.account.data)
                    pool = decode_virtual_pool(data)

                    if pool["is_migrated"] == 0 and pool["has_swap"] == 0:
                        print(f"NEW POOL: {pubkey}")
                        print(f"  Creator: {pool['creator']}")
                        print(f"  Base Mint: {pool['base_mint']}")
                    elif pool["is_migrated"] == 1:
                        print(f"MIGRATED: {pubkey}")

def decode_virtual_pool(data: bytes) -> dict:
    """Decode VirtualPool account data (416 bytes).

    Layout after 8-byte discriminator:
    - VolatilityTracker (variable, need to calculate offset)
    - config: Pubkey (32 bytes)
    - creator: Pubkey (32 bytes)
    - base_mint: Pubkey (32 bytes)
    - base_vault: Pubkey (32 bytes)
    - quote_vault: Pubkey (32 bytes)
    - base_reserve: u64
    - quote_reserve: u64
    - ... fees ...
    - sqrt_price: u128
    - activation_point: u64
    - pool_type: u8
    - is_migrated: u8
    - ... more flags ...
    """
    # NOTE: exact offsets depend on VolatilityTracker size
    # Use IDL + borsh deserialization for production code
    # This is a simplified example showing the approach
    offset = 8  # skip discriminator
    # ... parse fields with struct.unpack_from ...
    return {}

if __name__ == "__main__":
    asyncio.run(monitor_dbc_pools("wss://api.mainnet-beta.solana.com"))
```

### 9.2 Approach B: logsSubscribe for Event-Based Detection

```python
import asyncio
import base64
import json
from solana.rpc.websocket_api import connect

DBC_PROGRAM = "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"

# Anchor event discriminators (first 8 bytes of sha256("event:<EventName>"))
EVT_INITIALIZE_POOL = bytes([...])  # compute from IDL
EVT_SWAP = bytes([...])
EVT_CURVE_COMPLETE = bytes([...])

async def monitor_dbc_events(rpc_ws_url: str) -> None:
    async with connect(rpc_ws_url) as websocket:
        # Subscribe to logs mentioning DBC program
        await websocket.logs_subscribe(
            filter_={"mentions": [DBC_PROGRAM]},
            commitment="confirmed",
        )
        first_resp = await websocket.recv()
        print(f"Subscribed to logs: {first_resp[0].result}")

        async for msg in websocket:
            for notification in msg:
                if hasattr(notification, "result"):
                    logs = notification.result.value.logs
                    signature = notification.result.value.signature

                    for log in logs:
                        if log.startswith("Program data:"):
                            event_data = base64.b64decode(log[len("Program data: "):])
                            discriminator = event_data[:8]

                            if discriminator == EVT_INITIALIZE_POOL:
                                handle_new_pool(event_data, signature)
                            elif discriminator == EVT_CURVE_COMPLETE:
                                handle_curve_complete(event_data, signature)

def handle_new_pool(data: bytes, sig: str) -> None:
    # Decode EvtInitializePool: pool, config, creator, base_mint, pool_type, activation_point
    print(f"New pool in tx: {sig}")

def handle_curve_complete(data: bytes, sig: str) -> None:
    # Decode EvtCurveComplete: pool, config, base_reserve, quote_reserve
    print(f"Curve complete in tx: {sig}")

if __name__ == "__main__":
    asyncio.run(monitor_dbc_events("wss://api.mainnet-beta.solana.com"))
```

### 9.3 Approach C: Hybrid (Recommended for Production)

```python
import asyncio
import httpx
from solana.rpc.websocket_api import connect
from solana.rpc.async_api import AsyncClient

DBC_PROGRAM = "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"
DAMM_V2_API = "https://damm-v2.datapi.meteora.ag"

async def main() -> None:
    # Three concurrent tasks
    await asyncio.gather(
        monitor_new_pools_ws(),      # WebSocket: detect new pools instantly
        monitor_migrations_ws(),      # WebSocket: detect graduations
        poll_damm_v2_api(),          # REST: enrich graduated pools with DAMM v2 data
    )

async def monitor_new_pools_ws() -> None:
    """logsSubscribe for initialize_virtual_pool_with_spl_token events."""
    async with connect("wss://api.mainnet-beta.solana.com") as ws:
        await ws.logs_subscribe(
            filter_={"mentions": [DBC_PROGRAM]},
            commitment="confirmed",
        )
        _ = await ws.recv()
        async for msg in ws:
            # Parse logs for EvtInitializePool
            pass

async def monitor_migrations_ws() -> None:
    """programSubscribe with memcmp filter on is_migrated field."""
    async with connect("wss://api.mainnet-beta.solana.com") as ws:
        await ws.program_subscribe(
            Pubkey.from_string(DBC_PROGRAM),
            encoding="base64",
            commitment="confirmed",
            filters=[{"dataSize": 416}],
        )
        _ = await ws.recv()
        async for msg in ws:
            # Check is_migrated flag, detect graduation
            pass

async def poll_damm_v2_api() -> None:
    """Poll DAMM v2 REST API for graduated pool details."""
    async with httpx.AsyncClient(base_url=DAMM_V2_API) as client:
        while True:
            resp = await client.get(
                "/pools",
                params={
                    "sort_by": "pool_created_at:desc",
                    "page_size": 50,
                    "filter_by": "launchpad=dbc",
                },
            )
            pools = resp.json()["data"]
            for pool in pools:
                # Process newly graduated DBC pools
                pass
            await asyncio.sleep(10)
```

### 9.4 Dependencies

```
solana>=0.35.0
solders>=0.22.0
httpx>=0.27.0
anchorpy>=0.20.0    # For IDL-based account deserialization
websockets>=13.0
structlog>=24.0
```

### 9.5 Using AnchorPy for Clean Deserialization

```python
from anchorpy import Program, Provider, Idl
from solders.pubkey import Pubkey
import json

# Load the DBC IDL
with open("dbc_idl.json") as f:
    idl = Idl.from_json(json.dumps(json.load(f)))

# IDL available at:
# https://github.com/MeteoraAg/dynamic-bonding-curve-sdk/blob/main/packages/dynamic-bonding-curve/src/idl/dynamic-bonding-curve/idl.json

program = Program(idl, Pubkey.from_string(DBC_PROGRAM), provider)

# Fetch and auto-deserialize a VirtualPool
pool = await program.account["VirtualPool"].fetch(pool_pubkey)
print(pool.base_mint)
print(pool.creator)
print(pool.is_migrated)
print(pool.sqrt_price)
print(pool.quote_reserve)
```

---

## 10. Bitquery GraphQL (Alternative Real-Time Source)

For teams preferring a managed indexer over raw RPC subscriptions.

### 10.1 New Pool Creation Subscription
```graphql
subscription {
  Solana {
    Instructions(
      where: {
        Instruction: {
          Program: {
            Address: { is: "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN" }
            Method: { is: "initialize_virtual_pool_with_spl_token" }
          }
        }
        Transaction: { Result: { Success: true } }
      }
    ) {
      Block { Time }
      Instruction {
        Accounts { Address, IsWritable, Token { Mint, Owner, ProgramId } }
        Program { Method, Arguments { Name, Type, Value { ... on Solana_ABI_Address_Value_Arg { address } ... on Solana_ABI_String_Value_Arg { string } } } }
      }
      Transaction { Signature, FeePayer }
    }
  }
}
```

### 10.2 Migration Events Subscription
```graphql
subscription {
  Solana {
    Instructions(
      where: {
        Instruction: {
          Program: {
            Address: { is: "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN" }
            Method: { in: ["migrate_meteora_damm", "migration_damm_v2"] }
          }
        }
        Transaction: { Result: { Success: true } }
      }
    ) {
      Block { Time }
      Instruction {
        Accounts { Address, Token { Mint } }
        Program { Method }
      }
      Transaction { Signature }
    }
  }
}
```

### 10.3 Real-Time Trades
```graphql
subscription {
  Solana {
    DEXTrades(
      where: {
        Trade: {
          Dex: { ProgramAddress: { is: "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN" } }
        }
      }
    ) {
      Trade {
        Buy { Currency { Name, Symbol, MintAddress }, Amount, PriceAgainstSellCurrency: Price }
        Sell { Amount, Currency { MintAddress }, PriceAgainstBuyCurrency: Price }
      }
      Block { Time }
    }
  }
}
```

---

## 11. Data Extraction Summary

### From a DBC VirtualPool account we can extract:

| Data Point | Source Field | How |
|------------|-------------|-----|
| Token mint address | `base_mint` | Direct Pubkey field |
| Creator wallet | `creator` | Direct Pubkey field |
| Current price | `sqrt_price` | `price = (sqrt_price / 2^64)^2` adjusted for decimals |
| Base liquidity | `base_reserve` | u64, divide by `10^decimals` |
| Quote liquidity | `quote_reserve` | u64, divide by `10^decimals` |
| Is graduated | `is_migrated` | 0 = active, 1 = migrated |
| Migration step | `migration_progress` | Which step of migration |
| Curve filled timestamp | `finish_curve_timestamp` | 0 = not yet, >0 = done |
| Has any trading | `has_swap` | 0 = no, 1 = yes |
| Pool type | `pool_type` | 0 = SPL, 1 = Token2022 |
| Config address | `config` | Links to PoolConfig for thresholds |
| Graduation progress % | `quote_reserve / config.migration_quote_threshold` | Requires reading both accounts |
| Total fees collected | `protocol_*_fee + partner_*_fee + creator_*_fee` | Sum all fee fields |

### From PoolConfig we additionally get:

| Data Point | Source Field |
|------------|-------------|
| Quote token (SOL/USDC) | `quote_mint` |
| Migration target | `migration_option` (0=DAMMv1, 1=DAMMv2) |
| Graduation threshold | `migration_quote_threshold` |
| Fee structure | `pool_fees` |
| Token decimals | `token_decimal` |
| Curve shape (16 segments) | `curve[]` |
| Starting price | `sqrt_start_price` |
| Post-migration token supply | `post_migration_token_supply` |
| Creator LP % | `creator_liquidity_percentage` |

---

## 12. Key Source Links

- [DBC Program Repository](https://github.com/MeteoraAg/dynamic-bonding-curve)
- [DBC SDK (TypeScript)](https://www.npmjs.com/package/@meteora-ag/dynamic-bonding-curve-sdk)
- [DBC IDL JSON](https://github.com/MeteoraAg/dynamic-bonding-curve-sdk/blob/main/packages/dynamic-bonding-curve/src/idl/dynamic-bonding-curve/idl.json)
- [Meteora DBC Docs](https://docs.meteora.ag/developer-guide/guides/dbc/overview)
- [DBC Config Key Docs](https://docs.meteora.ag/overview/products/dbc/dbc-config-key)
- [Bonding Curve Formulas](https://docs.meteora.ag/overview/products/dbc/bonding-curve-formulas)
- [SDK Functions Reference](https://docs.meteora.ag/developer-guide/guides/dbc/typescript-sdk/sdk-functions)
- [DAMM v2 API Overview](https://docs.meteora.ag/api-reference/damm-v2/overview)
- [DAMM v2 Swagger UI](https://damm-v2.datapi.meteora.ag/swagger-ui/)
- [meteora-dbc-client (Rust crate)](https://docs.rs/meteora-dbc-client/latest/meteora_dbc_client/all.html)
- [LYS Labs DBC Decoder](https://docs.lyslabs.ai/decoders/meteora-dbc)
- [Bitquery DBC API](https://docs.bitquery.io/docs/blockchain/Solana/meteora-dynamic-bonding-curve-api/)
- [Solana RPC WebSocket Docs](https://solana.com/docs/rpc/websocket)
- [solana-py GitHub](https://github.com/michaelhly/solana-py)
- [solana-py Subscribing to Events](https://michaelhly.com/solana-py/cookbook/development-guides/subscribing-to-events/)
- [Meteora Full Docs Index (LLMs.txt)](https://docs.meteora.ag/llms.txt)

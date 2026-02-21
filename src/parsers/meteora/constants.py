"""Meteora Dynamic Bonding Curve (DBC) program constants."""

DBC_PROGRAM_ID = "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"

# First 8 bytes of VirtualPool account data (Anchor discriminator)
VIRTUAL_POOL_DISCRIMINATOR = bytes([213, 224, 5, 209, 98, 69, 119, 92])

# Instruction names as they appear in Solana transaction logs (PascalCase)
INSTRUCTION_INIT_POOL = "InitializeVirtualPoolWithSplToken"
INSTRUCTION_INIT_POOL_DYNAMIC = "InitializePoolWithDynamicConfig"
INSTRUCTION_MIGRATE_DAMM_V2 = "MigrationDammV2"

# DAMM v2 REST API
DAMM_V2_BASE_URL = "https://damm-v2.datapi.meteora.ag"

# Typical graduation threshold for DBC pools (in lamports, ~85 SOL).
# Pool migrates to DAMM when quote_reserve reaches this.
# Different launchpads may use different thresholds, but 85 SOL is the standard.
DBC_GRADUATION_THRESHOLD_LAMPORTS = 85_000_000_000  # 85 SOL in lamports

# Known DBC launchpad pool_config addresses → human-readable names.
# pool_config is instruction account[1] in InitializeVirtualPoolWithSplToken.
KNOWN_LAUNCHPADS: dict[str, str] = {
    "FhVo3mqL8PW5pH5U2CN4XE33DokiyZnUwuGpH2hmHLuM": "believe",
    "9UiiTpuj5otqAW8fpu9rPqi432HvL1qV1GDcqpWo4yuG": "letsbonk",
    "HF4vNu7hxpaJYExxjom9YHACd1ohc3pEXCkT7CSfF8s": "boop",
}

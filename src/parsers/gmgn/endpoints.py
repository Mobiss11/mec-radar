BASE_URL = "https://gmgn.ai"

# Token data (new API — works without device_id)
TOKEN_INFO = "/api/v1/token_info/{chain}/{address}"
TOKEN_INFO_BATCH = "/api/v1/mutil_window_token_info"
TOKEN_SECURITY = "/defi/quotation/v1/tokens/security/{chain}/{address}"
TOP_HOLDERS = "/defi/quotation/v1/tokens/top_buyers/{chain}/{address}"

# Discovery
NEW_PAIRS = "/defi/quotation/v1/pairs/{chain}/new_pairs"
PUMP_TRENDING = "/defi/quotation/v1/rank/{chain}/pump"

# Smart wallets (v2 — rank-based, replaces old trendingWallets)
SMART_WALLETS = "/defi/quotation/v1/rank/{chain}/wallets/{period}"
WALLET_INFO = "/defi/quotation/v1/smartmoney/{chain}/walletNew/{address}"
WALLET_TRADES = "/defi/quotation/v1/smartmoney/{chain}/walletNew/{address}/trades"

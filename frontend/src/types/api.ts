/* Shared API response types */

export interface PaginatedResponse<T> {
  items: T[]
  next_cursor: number | null
  has_more: boolean
}

/* Health */
export interface HealthResponse {
  status: string
  uptime_seconds: number
  database: string
  redis: string
  version: string
}

/* Metrics */
export interface MetricsOverview {
  uptime_seconds: number
  uptime_human: string
  tokens_discovered: number
  tokens_enriched: number
  enrichment_rate_per_min: number
  queue_size: number
  sol_price_usd: number | null
  alerts_sent_24h: number
  active_signals: number
  open_positions: number
}

export interface ConnectionInfo {
  name: string
  status: "connected" | "disconnected" | "degraded" | "disabled"
  message_count?: number
  last_message?: string | null
  details?: Record<string, unknown>
}

export interface ConnectionsResponse {
  connections: ConnectionInfo[]
}

export interface PipelineStageStats {
  stage: string
  runs: number
  avg_latency_ms: number
  prune_rate: number
  api_errors: Record<string, number>
}

export interface PipelineResponse {
  stages: PipelineStageStats[]
  total_runs: number
  avg_latency_ms: number
}

/* Tokens */
export interface TokenListItem {
  id: number
  address: string
  name: string | null
  symbol: string | null
  source: string | null
  score: number | null
  score_v3: number | null
  price: number | null
  market_cap: number | null
  liquidity_usd: number | null
  holders_count: number | null
  stage: string | null
  created_at: string | null
  image_url: string | null
}

export interface TokenDetail {
  token: Record<string, unknown> | null
  latest_snapshot: Record<string, unknown> | null
  security: Record<string, unknown> | null
  active_signal: Record<string, unknown> | null
}

export interface TokenSnapshot {
  id: number
  token_id: number
  stage: string
  score: number | null
  score_v3: number | null
  price: number | null
  market_cap: number | null
  liquidity_usd: number | null
  volume_1h: number | null
  volume_24h: number | null
  holders_count: number | null
  created_at: string
  [key: string]: unknown
}

/* Signals */
export interface SignalItem {
  id: number
  token_id: number
  token_address: string
  score: number | null
  status: string
  created_at: string
  updated_at: string
  token_symbol: string | null
  token_name: string | null
  token_image_url: string | null
  [key: string]: unknown
}

/* Portfolio */
export type PortfolioMode = "paper" | "real" | "all"

export interface PortfolioSummary {
  mode: PortfolioMode
  open_count: number
  closed_count: number
  total_invested_sol: number
  total_pnl_usd: number
  win_rate: number
  wins: number
  losses: number
  real_trading_enabled: boolean
  wallet_balance?: number | null
  circuit_breaker_tripped?: boolean | null
  total_failures?: number | null
}

export interface PositionItem {
  id: number
  token_address: string
  symbol: string | null
  source: string | null
  entry_price: number | null
  current_price: number | null
  entry_mcap: number | null
  current_mcap: number | null
  amount_sol_invested: number | null
  pnl_pct: number | null
  pnl_usd: number | null
  max_price: number | null
  status: string
  close_reason: string | null
  is_paper: boolean
  tx_hash: string | null
  opened_at: string | null
  closed_at: string | null
}

export interface PnlHistoryItem {
  date: string
  daily_pnl_usd: number
  cumulative_pnl_usd: number
}

/* Settings */
export interface SettingsData {
  paper_trading_enabled: boolean
  paper_sol_per_trade: number
  paper_max_positions: number
  paper_take_profit_x: number
  paper_stop_loss_pct: number
  paper_timeout_hours: number
  signal_decay_enabled: boolean
  signal_strong_buy_ttl_hours: number
  signal_buy_ttl_hours: number
  signal_watch_ttl_hours: number
  enable_birdeye: boolean
  enable_gmgn: boolean
  enable_pumpportal: boolean
  enable_dexscreener: boolean
  enable_meteora_dbc: boolean
  enable_grpc_streaming: boolean
  enable_solsniffer: boolean
  enable_twitter: boolean
  enable_telegram_checker: boolean
  enable_llm_analysis: boolean
}

export interface ApiServiceStatus {
  name: string
  configured: boolean
  enabled: boolean
}

/* Analytics */
export interface ScoreBucket {
  range: string
  count_v2: number
  count_v3: number
}

export interface SignalsByStatus {
  status: string
  count: number
}

export interface DiscoveryBySource {
  source: string
  count: number
}

export interface CloseReason {
  reason: string
  count: number
  avg_pnl_pct: number
}

export interface TopPerformer {
  token_address: string
  symbol: string | null
  peak_multiplier: number
  peak_market_cap: number | null
  source: string | null
}

/* Auth */
export interface AuthUser {
  username: string
  csrf_token: string
}

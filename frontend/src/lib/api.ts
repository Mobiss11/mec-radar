const BASE = "/api/v1"

class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...((options.headers as Record<string, string>) ?? {}),
    },
    ...options,
  })

  if (res.status === 401) {
    // Session expired — redirect to login (avoid redirect loops on auth endpoints)
    if (!path.startsWith("/auth/")) {
      window.location.href = "/login"
    }
    throw new ApiError(401, "Unauthorized")
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, body.detail ?? res.statusText)
  }

  return res.json() as Promise<T>
}

/* Auth */
export const auth = {
  login: (username: string, password: string) =>
    request<{ username: string; csrf_token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  me: () => request<{ username: string }>("/auth/me"),
  csrf: () => request<{ csrf_token: string }>("/auth/csrf"),
}

/* Health */
export const health = {
  check: () =>
    request<{
      status: string
      version: string
      uptime_sec: number
      db_ok: boolean
      redis_ok: boolean
    }>("/health"),
}

/* Metrics */
export const metrics = {
  overview: () =>
    request<Record<string, unknown>>("/metrics/overview"),
  connections: () =>
    request<Record<string, unknown>>("/metrics/connections"),
  pipeline: () =>
    request<{ stages: Record<string, unknown> }>("/metrics/pipeline"),
}

/* Tokens */
export const tokens = {
  list: (params: Record<string, string | number>) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v !== "" && v != null) qs.set(k, String(v))
    }
    return request<{ items: Array<Record<string, unknown>>; next_cursor: number | null; has_more: boolean }>(
      `/tokens?${qs}`,
    )
  },
  detail: (address: string) =>
    request<Record<string, unknown>>(`/tokens/${address}`),
  snapshots: (address: string, limit = 50) =>
    request<{ items: Array<Record<string, unknown>> }>(
      `/tokens/${address}/snapshots?limit=${limit}`,
    ),
}

/* Signals */
export const signals = {
  list: (params: Record<string, string | number>) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v !== "" && v != null) qs.set(k, String(v))
    }
    return request<{ items: Array<Record<string, unknown>>; next_cursor: number | null; has_more: boolean }>(
      `/signals?${qs}`,
    )
  },
  detail: (id: number) =>
    request<Record<string, unknown>>(`/signals/${id}`),
}

/* Portfolio */
export const portfolio = {
  summary: (mode = "paper", pnlFilter = "all", period = "all") =>
    request<Record<string, unknown>>(
      `/portfolio/summary?mode=${mode}&pnl_filter=${pnlFilter}&period=${period}`,
    ),
  positions: (params: Record<string, string | number>) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v !== "" && v != null) qs.set(k, String(v))
    }
    return request<{
      items: Array<Record<string, unknown>>
      next_cursor: number | null
      has_more: boolean
      total: number
      page: number | null
      total_pages: number | null
    }>(`/portfolio/positions?${qs}`)
  },
  positionDetail: (id: number) =>
    request<Record<string, unknown>>(`/portfolio/positions/${id}`),
  pnlHistory: (days = 30, mode = "paper") =>
    request<{ items: Array<Record<string, unknown>> }>(
      `/portfolio/pnl-history?days=${days}&mode=${mode}`,
    ),
  closePosition: (positionId: number, csrfToken: string, force = false) =>
    request<{ ok: boolean; position_id: number; close_reason: string; pnl_pct: number | null }>(
      `/portfolio/positions/${positionId}/close${force ? "?force=true" : ""}`,
      {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
      },
    ),
}

/* Settings */
export const settings = {
  get: () => request<Record<string, unknown>>("/settings"),
  update: (data: Record<string, unknown>, csrfToken: string) =>
    request<{ updated: string[] }>("/settings", {
      method: "PATCH",
      headers: { "X-CSRF-Token": csrfToken },
      body: JSON.stringify(data),
    }),
  apiStatus: () =>
    request<{ services: Array<{ name: string; configured: boolean; enabled: boolean }> }>(
      "/settings/api-status",
    ),
}

/* Analytics */
export const analytics = {
  scoreDistribution: () =>
    request<{ buckets: Array<Record<string, unknown>> }>("/analytics/score-distribution"),
  signalsByStatus: (hours = 24) =>
    request<{ items: Array<Record<string, unknown>> }>(
      `/analytics/signals-by-status?hours=${hours}`,
    ),
  discoveryBySource: (hours = 24) =>
    request<{ items: Array<Record<string, unknown>> }>(
      `/analytics/discovery-by-source?hours=${hours}`,
    ),
  closeReasons: () =>
    request<{ items: Array<Record<string, unknown>> }>("/analytics/close-reasons"),
  topPerformers: (limit = 20) =>
    request<{ items: Array<Record<string, unknown>> }>(
      `/analytics/top-performers?limit=${limit}`,
    ),
}

/* Copy Trading */
export const copyTrading = {
  summary: () =>
    request<{
      active_wallets: number
      total_wallets: number
      open_positions: number
      total_invested_sol: number
      closed_count: number
      total_pnl_usd: number
      win_rate: number
      wins: number
      losses: number
      copy_trading_enabled: boolean
    }>("/copy-trading/summary"),

  wallets: () =>
    request<{
      items: Array<{
        address: string
        label: string
        multiplier: number
        max_sol_per_trade: number
        enabled: boolean
        added_at: string
      }>
      total: number
    }>("/copy-trading/wallets"),

  addWallet: (
    data: {
      address: string
      label?: string
      multiplier?: number
      max_sol_per_trade?: number
      enabled?: boolean
    },
    csrfToken: string,
  ) =>
    request<{ ok: boolean; address: string; total_wallets: number }>(
      "/copy-trading/wallets",
      {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
        body: JSON.stringify(data),
      },
    ),

  updateWallet: (
    address: string,
    data: {
      label?: string
      multiplier?: number
      max_sol_per_trade?: number
      enabled?: boolean
    },
    csrfToken: string,
  ) =>
    request<{ ok: boolean; address: string; wallet: Record<string, unknown> }>(
      `/copy-trading/wallets/${address}`,
      {
        method: "PATCH",
        headers: { "X-CSRF-Token": csrfToken },
        body: JSON.stringify(data),
      },
    ),

  removeWallet: (address: string, csrfToken: string) =>
    request<{ ok: boolean; address: string; total_wallets: number }>(
      `/copy-trading/wallets/${address}`,
      {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrfToken },
      },
    ),

  positions: (params: Record<string, string | number>) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v !== "" && v != null) qs.set(k, String(v))
    }
    return request<{
      items: Array<Record<string, unknown>>
      total: number
      page: number
      total_pages: number
      has_more: boolean
    }>(`/copy-trading/positions?${qs}`)
  },
}

export { ApiError }

import { useCallback, useState } from "react"
import { usePolling } from "@/hooks/use-polling"
import { useAuth } from "@/hooks/use-auth"
import { portfolio } from "@/lib/api"
import { Pagination } from "@/components/common/pagination"
import { StatCard } from "@/components/common/stat-card"
import { AddressBadge } from "@/components/common/address-badge"
import { EmptyState } from "@/components/common/empty-state"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { formatCompact, formatPct, formatPrice, formatSol, formatUsd, timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { PortfolioMode } from "@/types/api"
import {
  Briefcase,
  TrendingUp,
  TrendingDown,
  Target,
  Wallet,
  ShieldCheck,
  ShieldAlert,
  ExternalLink,
  X,
  Loader2,
} from "lucide-react"
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts"

const MODE_TABS: { value: PortfolioMode; label: string; icon: string }[] = [
  { value: "paper", label: "Paper", icon: "📄" },
  { value: "real", label: "Real", icon: "💰" },
  { value: "all", label: "All", icon: "📊" },
]

const CHART_COLORS: Record<PortfolioMode, string> = {
  paper: "oklch(0.72 0.19 155)",
  real: "oklch(0.76 0.16 85)",
  all: "oklch(0.65 0.15 250)",
}

const SUBTITLE: Record<PortfolioMode, string> = {
  paper: "Paper trading performance",
  real: "Real trading performance",
  all: "All trading combined",
}

type PnlFilter = "all" | "profit" | "loss"
type PeriodFilter = "1h" | "3h" | "12h" | "1d" | "1mo" | "all"

const PNL_FILTERS: { value: PnlFilter; label: string; icon?: typeof TrendingUp }[] = [
  { value: "all", label: "All" },
  { value: "profit", label: "Profit", icon: TrendingUp },
  { value: "loss", label: "Loss", icon: TrendingDown },
]

const PERIOD_FILTERS: { value: PeriodFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "1h", label: "1h" },
  { value: "3h", label: "3h" },
  { value: "12h", label: "12h" },
  { value: "1d", label: "1d" },
  { value: "1mo", label: "1mo" },
]

/** Detect platform from token address suffix */
function detectPlatform(address: string | null | undefined): string | null {
  if (!address) return null
  if (address.endsWith("pump")) return "pump.fun"
  return null
}

/** Map raw token source to display label + color classes */
function getSourceConfig(
  source: string | null | undefined,
  address?: string | null,
): { label: string; className: string } | null {
  // Try address-based detection first (always accurate), then fallback to DB source
  const platform = detectPlatform(address)

  const effective = platform ?? source
  if (!effective) return null

  if (
    effective === "pump.fun" ||
    effective.startsWith("pumpportal") ||
    effective === "pumpfun"
  )
    return { label: "pump.fun", className: "bg-fuchsia-500/15 text-fuchsia-400" }
  if (effective.startsWith("meteora"))
    return { label: "meteora", className: "bg-cyan-500/15 text-cyan-400" }
  if (effective.startsWith("gmgn"))
    return { label: "gmgn", className: "bg-emerald-500/15 text-emerald-400" }
  if (effective.startsWith("dexscreener"))
    return { label: "dexscreener", className: "bg-amber-500/15 text-amber-400" }
  if (effective.startsWith("chainstack") || effective === "grpc")
    return { label: "gRPC", className: "bg-violet-500/15 text-violet-400" }
  if (effective === "raydium")
    return { label: "raydium", className: "bg-sky-500/15 text-sky-400" }
  return {
    label: effective.slice(0, 12),
    className: "bg-muted text-muted-foreground",
  }
}

function SourceBadge({
  source,
  address,
}: {
  source: string | null
  address?: string | null
}) {
  const config = getSourceConfig(source, address)
  if (!config) return null
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold",
        config.className,
      )}
    >
      {config.label}
    </span>
  )
}

export function PortfolioPage() {
  const [mode, setMode] = useState<PortfolioMode>("paper")
  const [posStatus, setPosStatus] = useState("open")
  const [page, setPage] = useState(1)
  const [closingId, setClosingId] = useState<number | null>(null)
  const [confirmId, setConfirmId] = useState<number | null>(null)
  const { csrfToken, refreshCsrf } = useAuth()
  const [refreshKey, setRefreshKey] = useState(0)
  const [pnlFilter, setPnlFilter] = useState<PnlFilter>("all")
  const [period, setPeriod] = useState<PeriodFilter>("all")

  const { data: summary, loading: sumLoading } = usePolling({
    fetcher: () => portfolio.summary(mode, pnlFilter, period),
    interval: 15000,
    key: `summary-${mode}-${pnlFilter}-${period}-${refreshKey}`,
  })

  const posFetcher = useCallback(
    () =>
      portfolio.positions({
        mode,
        status: posStatus,
        limit: 20,
        page,
        pnl_filter: pnlFilter,
        period,
      }),
    [mode, posStatus, page, pnlFilter, period],
  )

  const { data: posData, loading: posLoading } = usePolling({
    fetcher: posFetcher,
    interval: 15000,
    key: `pos-${mode}-${posStatus}-${page}-${pnlFilter}-${period}-${refreshKey}`,
  })

  const { data: pnlData, loading: pnlLoading } = usePolling({
    fetcher: () => portfolio.pnlHistory(30, mode),
    interval: 60000,
    key: `pnl-${mode}`,
  })

  const s = summary as Record<string, number | boolean | null> | null
  const positions = (posData?.items ?? []) as Array<Record<string, unknown>>
  const pnlItems = ((pnlData as Record<string, unknown>)?.items ?? []) as Array<
    Record<string, unknown>
  >

  const chartColor = CHART_COLORS[mode]
  const showRealCards = mode !== "paper" && s?.real_trading_enabled

  const [forceCloseId, setForceCloseId] = useState<number | null>(null)

  const handleClose = useCallback(async (positionId: number, force = false) => {
    setClosingId(positionId)
    try {
      // Always refresh CSRF before mutating request (jti must match current JWT)
      const token = await refreshCsrf()
      await portfolio.closePosition(positionId, token, force)
      setConfirmId(null)
      setForceCloseId(null)
      setRefreshKey((k) => k + 1)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to close position"
      if (msg.includes("force close")) {
        // Swap failed, offer force close
        setForceCloseId(positionId)
        setConfirmId(null)
      } else {
        alert(msg)
      }
    } finally {
      setClosingId(null)
    }
  }, [csrfToken, refreshCsrf])

  return (
    <div className="space-y-6">
      {/* Header + mode tabs */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Portfolio</h1>
          <p className="text-sm text-muted-foreground">
            {SUBTITLE[mode]}
          </p>
        </div>

        <div className="flex gap-1 rounded-lg border border-border/50 bg-card/30 p-1">
          {MODE_TABS.map((tab) => (
            <button
              key={tab.value}
              onClick={() => {
                setMode(tab.value)
                setPage(1)
              }}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-all",
                mode === tab.value
                  ? "bg-primary/10 text-primary shadow-sm"
                  : "text-muted-foreground hover:text-foreground hover:bg-card/50",
              )}
            >
              <span>{tab.icon}</span>
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Summary cards */}
      <div className={cn(
        "grid gap-4",
        showRealCards ? "grid-cols-2 lg:grid-cols-3" : "grid-cols-2 lg:grid-cols-4",
      )}>
        <StatCard
          label="Total PnL"
          value={s ? formatUsd(s.total_pnl_usd as number) : null}
          icon={
            s && (s.total_pnl_usd as number) >= 0 ? (
              <TrendingUp className="h-4 w-4" />
            ) : (
              <TrendingDown className="h-4 w-4" />
            )
          }
          loading={sumLoading}
        />
        <StatCard
          label="Win Rate"
          value={s ? `${s.win_rate}%` : null}
          icon={<Target className="h-4 w-4" />}
          loading={sumLoading}
        />
        <StatCard
          label="Open"
          value={s ? String(s.open_count) : null}
          loading={sumLoading}
        />
        <StatCard
          label="Closed"
          value={s ? String(s.closed_count) : null}
          trend={s ? `${s.wins}W / ${s.losses}L` : undefined}
          loading={sumLoading}
        />

        {/* Real trading cards — wallet + circuit breaker */}
        {showRealCards && (
          <>
            <StatCard
              label="Wallet Balance"
              value={
                s?.wallet_balance != null
                  ? `${Number(s.wallet_balance).toFixed(4)} SOL`
                  : "—"
              }
              icon={<Wallet className="h-4 w-4" />}
              loading={sumLoading}
            />
            <StatCard
              label="Circuit Breaker"
              value={
                s?.circuit_breaker_tripped == null
                  ? "—"
                  : s.circuit_breaker_tripped
                    ? "TRIPPED"
                    : "OK"
              }
              icon={
                s?.circuit_breaker_tripped ? (
                  <ShieldAlert className="h-4 w-4" />
                ) : (
                  <ShieldCheck className="h-4 w-4" />
                )
              }
              loading={sumLoading}
              className={cn(
                s?.circuit_breaker_tripped && "border-red-500/50 bg-red-950/20",
              )}
            />
          </>
        )}
      </div>

      {/* PnL chart */}
      <div className="rounded-xl border border-border/50 bg-card/60 p-4 backdrop-blur-sm">
        <h3 className="mb-3 text-sm font-medium text-muted-foreground">
          Cumulative PnL (30 days)
        </h3>
        {pnlLoading ? (
          <Skeleton className="h-48" />
        ) : pnlItems.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No closed positions yet
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={pnlItems}>
              <defs>
                <linearGradient id={`pnlGrad-${mode}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={chartColor} stopOpacity={0.3} />
                  <stop offset="100%" stopColor={chartColor} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="oklch(1 0 0 / 5%)" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: "oklch(0.6 0.01 260)" }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                tick={{ fontSize: 10, fill: "oklch(0.6 0.01 260)" }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v: number) => `$${v}`}
              />
              <Tooltip
                contentStyle={{
                  background: "oklch(0.155 0.008 260)",
                  border: "1px solid oklch(1 0 0 / 10%)",
                  borderRadius: "8px",
                  fontSize: 12,
                }}
              />
              <Area
                type="monotone"
                dataKey="cumulative_pnl_usd"
                stroke={chartColor}
                fill={`url(#pnlGrad-${mode})`}
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Positions */}
      <div>
        <div className="mb-3 flex items-center gap-2">
          <h3 className="text-base font-semibold">Positions</h3>
          <div className="flex gap-1">
            {["open", "closed"].map((st) => (
              <Button
                key={st}
                variant={posStatus === st ? "secondary" : "ghost"}
                size="sm"
                className="text-xs capitalize"
                onClick={() => {
                  setPosStatus(st)
                  setPage(1)
                }}
              >
                {st}
              </Button>
            ))}
          </div>
        </div>

        {/* PnL + Period filters */}
        <div className="mb-3 flex flex-wrap items-center gap-3">
          {/* PnL filter */}
          <div className="flex gap-1 rounded-lg border border-border/50 bg-card/30 p-0.5">
            {PNL_FILTERS.map((f) => {
              const Icon = f.icon
              return (
                <button
                  key={f.value}
                  onClick={() => { setPnlFilter(f.value); setPage(1) }}
                  className={cn(
                    "flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition-all",
                    pnlFilter === f.value
                      ? f.value === "profit"
                        ? "bg-emerald-500/15 text-emerald-400 shadow-sm"
                        : f.value === "loss"
                          ? "bg-red-500/15 text-red-400 shadow-sm"
                          : "bg-primary/10 text-primary shadow-sm"
                      : "text-muted-foreground hover:text-foreground hover:bg-card/50",
                  )}
                >
                  {Icon && <Icon className="h-3 w-3" />}
                  {f.label}
                </button>
              )
            })}
          </div>

          <div className="h-4 w-px bg-border/50" />

          {/* Period filter */}
          <div className="flex gap-1 rounded-lg border border-border/50 bg-card/30 p-0.5">
            {PERIOD_FILTERS.map((f) => (
              <button
                key={f.value}
                onClick={() => { setPeriod(f.value); setPage(1) }}
                className={cn(
                  "rounded-md px-2.5 py-1 text-xs font-medium transition-all",
                  period === f.value
                    ? "bg-primary/10 text-primary shadow-sm"
                    : "text-muted-foreground hover:text-foreground hover:bg-card/50",
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {posLoading && !posData ? (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-16 rounded-lg" />
            ))}
          </div>
        ) : positions.length === 0 ? (
          <EmptyState
            icon={<Briefcase className="h-10 w-10" />}
            title={`No ${posStatus} positions`}
          />
        ) : (
          <div className="space-y-2">
            {positions.map((p) => (
              <div
                key={p.id as number}
                className="flex items-center gap-4 rounded-xl border border-border/50 bg-card/60 p-4 backdrop-blur-sm"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">
                      {(p.symbol as string) ?? "???"}
                    </span>
                    <AddressBadge address={p.token_address as string} />
                    {/* Source badge (pump.fun, meteora, etc.) */}
                    <SourceBadge
                      source={p.source as string | null}
                      address={p.token_address as string}
                    />
                    {/* REAL badge for on-chain positions */}
                    {p.is_paper === false && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-400">
                        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                        REAL
                      </span>
                    )}
                    {p.close_reason ? (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        {String(p.close_reason)}
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-0.5 flex flex-wrap gap-3 text-xs text-muted-foreground font-data">
                    <span>{formatSol(p.amount_sol_invested as number)}</span>
                    <span>
                      Entry: {formatPrice(p.entry_price as number)}
                    </span>
                    {p.current_price != null && (
                      <span>
                        Now: {formatPrice(p.current_price as number)}
                      </span>
                    )}
                    {(p.entry_mcap != null || p.current_mcap != null) && (
                      <span className="text-muted-foreground/70">
                        MC: {p.entry_mcap != null ? `$${formatCompact(p.entry_mcap as number)}` : "—"}
                        {p.current_mcap != null && ` → $${formatCompact(p.current_mcap as number)}`}
                      </span>
                    )}
                    {p.current_liq != null && (
                      <span className={cn(
                        "text-muted-foreground/70",
                        (p.current_liq as number) < 1000 && "text-red-400 font-semibold",
                      )}>
                        Liq: ${formatCompact(p.current_liq as number)}
                      </span>
                    )}
                    <span>{timeAgo((p.opened_at ?? p.closed_at) as string)}</span>
                    <a
                      href={`https://gmgn.ai/sol/token/${p.token_address}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-0.5 text-primary/70 hover:text-primary transition-colors"
                    >
                      gmgn
                      <ExternalLink className="h-3 w-3" />
                    </a>
                    {typeof p.tx_hash === "string" && p.tx_hash && (
                      <a
                        href={`https://solscan.io/tx/${p.tx_hash}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-0.5 text-primary/70 hover:text-primary transition-colors"
                      >
                        tx
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <div className="text-right">
                    <p
                      className={cn(
                        "font-data text-sm font-bold",
                        (p.pnl_pct as number) >= 0
                          ? "text-emerald-400"
                          : "text-red-400",
                      )}
                    >
                      {formatPct(p.pnl_pct as number)}
                    </p>
                    {p.pnl_usd != null && (
                      <p className="font-data text-xs text-muted-foreground">
                        {formatUsd(p.pnl_usd as number)}
                      </p>
                    )}
                  </div>
                  {posStatus === "open" && (
                    forceCloseId === (p.id as number) ? (
                      <div className="flex flex-col items-end gap-1">
                        <span className="text-[10px] text-red-400">Swap failed — pool dead?</span>
                        <div className="flex items-center gap-1">
                          <Button
                            variant="destructive"
                            size="sm"
                            className="h-7 px-2 text-xs"
                            disabled={closingId === (p.id as number)}
                            onClick={() => handleClose(p.id as number, true)}
                          >
                            {closingId === (p.id as number) ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : (
                              "Force Close (-100%)"
                            )}
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 px-1.5 text-xs"
                            onClick={() => setForceCloseId(null)}
                          >
                            <X className="h-3 w-3" />
                          </Button>
                        </div>
                      </div>
                    ) : confirmId === (p.id as number) ? (
                      <div className="flex items-center gap-1">
                        <Button
                          variant="destructive"
                          size="sm"
                          className="h-7 px-2 text-xs"
                          disabled={closingId === (p.id as number)}
                          onClick={() => handleClose(p.id as number)}
                        >
                          {closingId === (p.id as number) ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            "Confirm"
                          )}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 px-1.5 text-xs"
                          onClick={() => setConfirmId(null)}
                        >
                          <X className="h-3 w-3" />
                        </Button>
                      </div>
                    ) : (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-xs text-muted-foreground hover:text-red-400 hover:bg-red-500/10"
                        onClick={() => setConfirmId(p.id as number)}
                      >
                        Close
                      </Button>
                    )
                  )}
                </div>
              </div>
            ))}

            {posData?.total_pages != null && posData.total_pages > 1 && (
              <Pagination
                page={posData.page ?? page}
                totalPages={posData.total_pages}
                onPageChange={setPage}
                className="pt-3"
              />
            )}
          </div>
        )}
      </div>
    </div>
  )
}

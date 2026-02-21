import { useCallback, useState } from "react"
import { usePolling } from "@/hooks/use-polling"
import { portfolio } from "@/lib/api"
import { StatCard } from "@/components/common/stat-card"
import { AddressBadge } from "@/components/common/address-badge"
import { EmptyState } from "@/components/common/empty-state"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { formatPct, formatSol, formatUsd, timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"
import {
  Briefcase,
  TrendingUp,
  TrendingDown,
  Target,
  ChevronRight,
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

export function PortfolioPage() {
  const [posStatus, setPosStatus] = useState("open")
  const [cursor, setCursor] = useState<number | undefined>(undefined)

  const { data: summary, loading: sumLoading } = usePolling({
    fetcher: portfolio.summary,
    interval: 15000,
  })

  const posFetcher = useCallback(
    () =>
      portfolio.positions({
        status: posStatus,
        limit: 20,
        ...(cursor != null ? { cursor } : {}),
      }),
    [posStatus, cursor],
  )

  const { data: posData, loading: posLoading } = usePolling({
    fetcher: posFetcher,
    interval: 15000,
  })

  const { data: pnlData, loading: pnlLoading } = usePolling({
    fetcher: () => portfolio.pnlHistory(30),
    interval: 60000,
  })

  const s = summary as Record<string, number> | null
  const positions = (posData?.items ?? []) as Array<Record<string, unknown>>
  const pnlItems = ((pnlData as Record<string, unknown>)?.items ?? []) as Array<
    Record<string, unknown>
  >

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Portfolio</h1>
        <p className="text-sm text-muted-foreground">
          Paper trading performance
        </p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Total PnL"
          value={s ? formatUsd(s.total_pnl_usd) : null}
          icon={
            s && s.total_pnl_usd >= 0 ? (
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
                <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="oklch(0.72 0.19 155)" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="oklch(0.72 0.19 155)" stopOpacity={0} />
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
                stroke="oklch(0.72 0.19 155)"
                fill="url(#pnlGrad)"
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
                  setCursor(undefined)
                }}
              >
                {st}
              </Button>
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
                    {p.close_reason ? (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        {String(p.close_reason)}
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-0.5 flex gap-3 text-xs text-muted-foreground font-data">
                    <span>{formatSol(p.amount_sol_invested as number)}</span>
                    <span>
                      Entry: {formatUsd(p.entry_price as number)}
                    </span>
                    {p.current_price != null && (
                      <span>
                        Now: {formatUsd(p.current_price as number)}
                      </span>
                    )}
                    <span>{timeAgo((p.opened_at ?? p.closed_at) as string)}</span>
                  </div>
                </div>
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
              </div>
            ))}

            {posData?.has_more && positions.length > 0 && (
              <div className="flex justify-center pt-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    setCursor(
                      positions[positions.length - 1]?.id as number,
                    )
                  }
                >
                  Load more
                  <ChevronRight className="h-4 w-4 ml-1" />
                </Button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

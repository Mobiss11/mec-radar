import { usePolling } from "@/hooks/use-polling"
import { analytics } from "@/lib/api"
import { Skeleton } from "@/components/ui/skeleton"
import { EmptyState } from "@/components/common/empty-state"
import { AddressBadge } from "@/components/common/address-badge"
import { formatUsd } from "@/lib/format"
import { BarChart3 } from "lucide-react"
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from "recharts"

const COLORS = [
  "oklch(0.72 0.19 155)",
  "oklch(0.65 0.16 220)",
  "oklch(0.75 0.15 80)",
  "oklch(0.60 0.22 300)",
  "oklch(0.70 0.20 30)",
]

export function AnalyticsPage() {
  const { data: scoreDist, loading: scoreLoading } = usePolling({
    fetcher: analytics.scoreDistribution,
    interval: 60000,
  })

  const { data: sigStatus, loading: sigLoading } = usePolling({
    fetcher: () => analytics.signalsByStatus(24),
    interval: 60000,
  })

  const { data: discovery, loading: discLoading } = usePolling({
    fetcher: () => analytics.discoveryBySource(24),
    interval: 60000,
  })

  const { data: closeReasons, loading: closeLoading } = usePolling({
    fetcher: analytics.closeReasons,
    interval: 60000,
  })

  const { data: topPerf, loading: topLoading } = usePolling({
    fetcher: () => analytics.topPerformers(10),
    interval: 60000,
  })

  const buckets = ((scoreDist as Record<string, unknown>)?.buckets ?? []) as Array<
    Record<string, unknown>
  >
  const sigItems = ((sigStatus as Record<string, unknown>)?.items ?? []) as Array<
    Record<string, unknown>
  >
  const discItems = ((discovery as Record<string, unknown>)?.items ?? []) as Array<
    Record<string, unknown>
  >
  const closeItems = ((closeReasons as Record<string, unknown>)?.items ?? []) as Array<
    Record<string, unknown>
  >
  const topItems = ((topPerf as Record<string, unknown>)?.items ?? []) as Array<
    Record<string, unknown>
  >

  const tooltipStyle = {
    background: "oklch(0.155 0.008 260)",
    border: "1px solid oklch(1 0 0 / 10%)",
    borderRadius: "8px",
    fontSize: 12,
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Analytics</h1>
        <p className="text-sm text-muted-foreground">
          Scoring distributions, signal breakdown, and performance data
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Score Distribution */}
        <ChartCard title="Score Distribution" loading={scoreLoading}>
          {buckets.length === 0 ? (
            <NoData />
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={buckets}>
                <XAxis
                  dataKey="range"
                  tick={{ fontSize: 10, fill: "oklch(0.6 0.01 260)" }}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  tick={{ fontSize: 10, fill: "oklch(0.6 0.01 260)" }}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip contentStyle={tooltipStyle} />
                <Bar
                  dataKey="count_v2"
                  fill="oklch(0.72 0.19 155)"
                  radius={[4, 4, 0, 0]}
                  name="v2"
                />
                <Bar
                  dataKey="count_v3"
                  fill="oklch(0.65 0.16 220)"
                  radius={[4, 4, 0, 0]}
                  name="v3"
                />
              </BarChart>
            </ResponsiveContainer>
          )}
        </ChartCard>

        {/* Signals by Status */}
        <ChartCard title="Signals by Status (24h)" loading={sigLoading}>
          {sigItems.length === 0 ? (
            <NoData />
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie
                  data={sigItems}
                  dataKey="count"
                  nameKey="status"
                  cx="50%"
                  cy="50%"
                  innerRadius={50}
                  outerRadius={80}
                  paddingAngle={2}
                >
                  {sigItems.map((_, i) => (
                    <Cell key={i} fill={COLORS[i % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip contentStyle={tooltipStyle} />
              </PieChart>
            </ResponsiveContainer>
          )}
          <div className="flex flex-wrap justify-center gap-3 mt-2">
            {sigItems.map((s, i) => (
              <span key={i} className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ background: COLORS[i % COLORS.length] }}
                />
                {s.status as string}: {s.count as number}
              </span>
            ))}
          </div>
        </ChartCard>

        {/* Discovery by Source */}
        <ChartCard title="Discovery by Source (24h)" loading={discLoading}>
          {discItems.length === 0 ? (
            <NoData />
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={discItems} layout="vertical">
                <XAxis
                  type="number"
                  tick={{ fontSize: 10, fill: "oklch(0.6 0.01 260)" }}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="source"
                  tick={{ fontSize: 10, fill: "oklch(0.6 0.01 260)" }}
                  tickLine={false}
                  axisLine={false}
                  width={80}
                />
                <Tooltip contentStyle={tooltipStyle} />
                <Bar
                  dataKey="count"
                  fill="oklch(0.72 0.19 155)"
                  radius={[0, 4, 4, 0]}
                />
              </BarChart>
            </ResponsiveContainer>
          )}
        </ChartCard>

        {/* Close Reasons */}
        <ChartCard title="Close Reasons" loading={closeLoading}>
          {closeItems.length === 0 ? (
            <NoData />
          ) : (
            <div className="space-y-2">
              {closeItems.map((c, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between text-sm"
                >
                  <span className="text-muted-foreground">
                    {c.reason as string}
                  </span>
                  <div className="flex items-center gap-3 font-data">
                    <span>{c.count as number}x</span>
                    <span
                      className={
                        (c.avg_pnl_pct as number) >= 0
                          ? "text-emerald-400"
                          : "text-red-400"
                      }
                    >
                      {(c.avg_pnl_pct as number) >= 0 ? "+" : ""}
                      {(c.avg_pnl_pct as number).toFixed(1)}%
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </ChartCard>
      </div>

      {/* Top Performers */}
      <div className="rounded-xl border border-border/50 bg-card/60 p-4 backdrop-blur-sm">
        <h3 className="mb-3 text-sm font-medium text-muted-foreground uppercase tracking-wider">
          Top Performers (Peak Multiplier)
        </h3>
        {topLoading ? (
          <Skeleton className="h-32" />
        ) : topItems.length === 0 ? (
          <EmptyState
            icon={<BarChart3 className="h-8 w-8" />}
            title="No performance data yet"
          />
        ) : (
          <div className="space-y-2">
            {topItems.map((t, i) => (
              <div
                key={i}
                className="flex items-center justify-between text-sm"
              >
                <div className="flex items-center gap-2">
                  <span className="w-5 text-xs text-muted-foreground font-data">
                    #{i + 1}
                  </span>
                  <span className="font-medium">
                    {(t.symbol as string) ?? "???"}
                  </span>
                  <AddressBadge address={t.token_address as string} />
                </div>
                <div className="flex items-center gap-4 font-data">
                  <span className="text-emerald-400 font-bold">
                    {(t.peak_multiplier as number).toFixed(1)}x
                  </span>
                  <span className="text-muted-foreground text-xs">
                    {formatUsd(t.peak_market_cap as number)} MCap
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function ChartCard({
  title,
  loading,
  children,
}: {
  title: string
  loading: boolean
  children: React.ReactNode
}) {
  return (
    <div className="rounded-xl border border-border/50 bg-card/60 p-4 backdrop-blur-sm">
      <h3 className="mb-3 text-sm font-medium text-muted-foreground uppercase tracking-wider">
        {title}
      </h3>
      {loading ? <Skeleton className="h-52" /> : children}
    </div>
  )
}

function NoData() {
  return (
    <p className="flex h-52 items-center justify-center text-sm text-muted-foreground">
      No data yet
    </p>
  )
}

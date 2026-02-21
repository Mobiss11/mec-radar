import { usePolling } from "@/hooks/use-polling"
import { health, metrics } from "@/lib/api"
import { StatCard } from "@/components/common/stat-card"
import { StatusDot } from "@/components/common/status-dot"
import { Skeleton } from "@/components/ui/skeleton"
import { formatCompact, formatDuration, formatUsd } from "@/lib/format"
import {
  Clock,
  Layers,
  Server,
  TrendingUp,
  Zap,
} from "lucide-react"

interface ConnInfo {
  state?: string
  connected?: boolean
  message_count?: number
  token_count?: number
}

interface StageInfo {
  runs: number
  avg_latency_ms: number
  max_latency_ms: number
}

export function OverviewPage() {
  const { data: healthData, loading: healthLoading } = usePolling({
    fetcher: health.check,
    interval: 5000,
  })

  const { data: overview, loading: overviewLoading } = usePolling({
    fetcher: metrics.overview,
    interval: 5000,
  })

  const { data: conns, loading: connsLoading } = usePolling({
    fetcher: metrics.connections,
    interval: 5000,
  })

  const { data: pipeline, loading: pipelineLoading } = usePolling({
    fetcher: metrics.pipeline,
    interval: 10000,
  })

  const loading = healthLoading || overviewLoading

  const ov = overview as Record<string, number | string | null> | null

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Overview</h1>
        <p className="text-sm text-muted-foreground">
          Real-time system metrics and pipeline status
        </p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Uptime"
          value={ov?.uptime_sec != null ? formatDuration(ov.uptime_sec as number) : null}
          icon={<Clock className="h-4 w-4" />}
          loading={loading}
        />
        <StatCard
          label="Enrichment Rate"
          value={ov?.enrichments_per_min != null ? `${Number(ov.enrichments_per_min).toFixed(1)}/min` : null}
          icon={<Zap className="h-4 w-4" />}
          loading={loading}
        />
        <StatCard
          label="Queue Size"
          value={ov?.queue_size != null ? formatCompact(ov.queue_size as number) : null}
          icon={<Layers className="h-4 w-4" />}
          loading={loading}
        />
        <StatCard
          label="SOL Price"
          value={ov?.sol_price_usd != null ? formatUsd(ov.sol_price_usd as number) : "—"}
          icon={<TrendingUp className="h-4 w-4" />}
          loading={loading}
        />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Total Enrichments"
          value={ov?.total_enrichments != null ? formatCompact(ov.total_enrichments as number) : null}
          loading={loading}
        />
        <StatCard
          label="Pruned"
          value={ov?.total_pruned != null ? String(ov.total_pruned) : null}
          loading={loading}
        />
        <StatCard
          label="Alerts Sent"
          value={ov?.alerts_sent != null ? String(ov.alerts_sent) : null}
          loading={loading}
        />
        <StatCard
          label="Version"
          value={healthData?.version ?? null}
          icon={<Server className="h-4 w-4" />}
          loading={healthLoading}
        />
      </div>

      {/* Connection cards */}
      <div>
        <h2 className="mb-3 text-base font-semibold">Connections</h2>
        {connsLoading ? (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-20 rounded-xl" />
            ))}
          </div>
        ) : conns ? (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {Object.entries(conns as Record<string, ConnInfo>).map(
              ([name, c]) => {
                const isUp = c.state === "active" || c.connected === true
                return (
                  <div
                    key={name}
                    className="flex items-center gap-3 rounded-xl border border-border/50 bg-card/60 p-4 backdrop-blur-sm"
                  >
                    <StatusDot
                      status={isUp ? "connected" : "disconnected"}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium truncate">
                        {name}
                      </p>
                      {c.message_count != null && (
                        <p className="text-xs text-muted-foreground font-data">
                          {formatCompact(c.message_count)} msgs
                        </p>
                      )}
                    </div>
                  </div>
                )
              },
            )}
          </div>
        ) : null}
      </div>

      {/* Pipeline stages */}
      <div>
        <h2 className="mb-3 text-base font-semibold">Pipeline</h2>
        {pipelineLoading ? (
          <Skeleton className="h-32 rounded-xl" />
        ) : pipeline?.stages ? (
          <div className="rounded-xl border border-border/50 bg-card/60 p-4 backdrop-blur-sm">
            <div className="space-y-2">
              {Object.entries(
                pipeline.stages as Record<string, StageInfo>,
              ).map(([name, s]) => {
                const maxRuns = Math.max(
                  ...Object.values(
                    pipeline.stages as Record<string, StageInfo>,
                  ).map((st) => st.runs),
                  1,
                )
                return (
                  <div
                    key={name}
                    className="flex items-center gap-3 text-sm"
                  >
                    <span className="w-24 truncate text-xs text-muted-foreground">
                      {name}
                    </span>
                    <div className="flex-1">
                      <div className="h-1.5 rounded-full bg-muted">
                        <div
                          className="h-1.5 rounded-full bg-primary/70 transition-all"
                          style={{
                            width: `${Math.min(100, (s.runs / maxRuns) * 100)}%`,
                          }}
                        />
                      </div>
                    </div>
                    <span className="w-12 text-right font-data text-xs text-muted-foreground">
                      {s.runs}
                    </span>
                    <span className="w-16 text-right font-data text-xs text-muted-foreground">
                      {s.avg_latency_ms != null ? `${s.avg_latency_ms.toFixed(0)}ms` : "—"}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        ) : null}
      </div>

      {/* Health */}
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Server className="h-3.5 w-3.5" />
        <span>
          DB:{" "}
          <span
            className={
              healthData?.db_ok
                ? "text-emerald-400"
                : "text-red-400"
            }
          >
            {healthData?.db_ok ? "ok" : "down"}
          </span>
        </span>
        <span className="mx-1">|</span>
        <span>
          Redis:{" "}
          <span
            className={
              healthData?.redis_ok
                ? "text-emerald-400"
                : "text-red-400"
            }
          >
            {healthData?.redis_ok ? "ok" : "down"}
          </span>
        </span>
      </div>
    </div>
  )
}
